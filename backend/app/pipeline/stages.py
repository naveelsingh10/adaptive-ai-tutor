import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ..config import settings
from ..models import UploadedFile
from ..models_kb import (Concept, ConceptPrerequisite, ContentUnit, ContentUnitConcept,
                         ContentUnitSource, SourcePage, Subtopic, Topic, VisualElement)
from ..providers import get_embedder, get_llm, get_vision
from .util import norm_name, quote_in


class StageContext:
    def __init__(self, db, job, source: UploadedFile):
        self.db, self.job, self.source = db, job, source
        self.course_id = job.course_id
        self.artifact_root = settings.artifact_dir / f"source_{source.id}"
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def source_path(self) -> Path:
        p = Path(self.source.storage_location)
        return p if p.is_absolute() else Path.cwd() / p


class StageIncomplete(Exception):
    pass


# =====================================================================
# 1. EXTRACT_PAGES  - page images + embedded text (observed evidence)
# =====================================================================
def _to_pdf(path: Path, out_dir: Path) -> Path:
    pdf = out_dir / f"{path.stem}.pdf"
    if pdf.exists():
        return pdf
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError("PPT/PPTX needs LibreOffice (soffice not on PATH). Install it or upload the slides as PDF.")
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
                   check=True, timeout=900)
    return pdf


def extract_pages(ctx: StageContext):
    import fitz  # PyMuPDF
    db, src = ctx.db, ctx.source
    path = ctx.source_path()
    if path.suffix.lower() in (".ppt", ".pptx"):
        path = _to_pdf(path, ctx.artifact_root)

    pages_dir = ctx.artifact_root / "pages"
    pages_dir.mkdir(exist_ok=True)
    is_slides = src.file_type == "slides"

    doc = fitz.open(path)
    for i, page in enumerate(doc, start=1):
        row = db.query(SourcePage).filter_by(source_id=src.id, page_number=i).first()
        if row and row.image_path and Path(row.image_path).exists():
            continue  # resumable
        img = pages_dir / f"page_{i:04d}.png"
        page.get_pixmap(dpi=150).save(str(img))
        try:
            tables = len(page.find_tables().tables)
        except Exception:
            tables = 0
        has_visuals = is_slides or len(page.get_images()) > 0 or len(page.get_drawings()) > 15 or tables > 0
        row = row or SourcePage(source_id=src.id, page_number=i)
        row.slide_number = i if is_slides else None
        row.image_path = str(img)
        row.embedded_text = page.get_text("text").strip()
        row.has_visuals = has_visuals
        db.add(row)
        if i % 10 == 0:
            db.commit()
    db.commit()
    return {"pages": len(doc)}


# =====================================================================
# 2. OCR - only where the text layer is missing/very small
# =====================================================================
_ocr = None


def ocr(ctx: StageContext):
    global _ocr
    db = ctx.db
    pages = (db.query(SourcePage)
             .filter(SourcePage.source_id == ctx.source.id, SourcePage.ocr_text.is_(None))
             .order_by(SourcePage.page_number).all())
    ran = 0
    for p in pages:
        if len((p.embedded_text or "").strip()) >= settings.ocr_min_chars:
            p.ocr_text = ""  # marks "checked, not needed"
            continue
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        result, _ = _ocr(p.image_path)
        p.ocr_text = "\n".join(r[1] for r in (result or []))
        ran += 1
        db.commit()
    db.commit()
    return {"ocr_pages": ran}


# =====================================================================
# 3. VISION - meaning of diagrams/figures/tables/formulas (interpretation)
# =====================================================================
VISION_SYSTEM = (
    "You analyse one page or slide of academic material. Report ONLY what is visibly present. "
    "Never guess or add outside knowledge. Do NOT transcribe ordinary body text (OCR handles text). "
    "Answer in JSON only."
)
VISION_PROMPT = """Return JSON:
{"page_summary": "1-2 sentences on what the visual content shows, or empty string",
 "elements": [{"visual_type": "diagram|figure|chart|table|formula|equation|flowchart|architecture|graph|plot|illustration|image|code_screenshot",
   "title": string or null, "description": string, "meaning": string or null,
   "labels": [string], "relationships": [string], "concepts": [string],
   "formula": string or null, "table_summary": string or null, "diagram_flow": [string]}]}
If there is no academically meaningful visual element, return {"page_summary": "", "elements": []}."""


def vision(ctx: StageContext):
    db, vp = ctx.db, get_vision()
    pending = (db.query(SourcePage)
               .filter(SourcePage.source_id == ctx.source.id, SourcePage.has_visuals.is_(True),
                       SourcePage.visual_description.is_(None))
               .order_by(SourcePage.page_number).all())
    done = 0
    for p in pending:
        if done >= settings.vision_max_pages_per_run:
            raise StageIncomplete(
                f"Vision cap reached ({settings.vision_max_pages_per_run} pages/run, protects the free quota). "
                f"{len(pending) - done} pages left - retry to continue.")
        out = vp.analyze_image(Path(p.image_path).read_bytes(), VISION_SYSTEM, VISION_PROMPT)
        for el in out.get("elements") or []:
            db.add(VisualElement(
                source_id=ctx.source.id, page_id=p.id,
                visual_type=el.get("visual_type"), title=el.get("title"),
                description=el.get("description"),
                data={k: el.get(k) for k in ("meaning", "labels", "relationships", "concepts",
                                             "formula", "table_summary", "diagram_flow")}))
        p.visual_description = (out.get("page_summary") or "").strip()
        db.commit()  # per page: a later failure keeps earlier work
        done += 1
    return {"pages_analysed": done}


# =====================================================================
# 4. KNOWLEDGE - grounded topic / concept / prerequisite extraction
# =====================================================================
CONTENT_TYPES = {"definition", "explanation", "example", "formula", "diagram_explanation",
                 "table_explanation", "code_example", "slide_content", "textbook_passage"}

KNOWLEDGE_SYSTEM = (
    "You build an academic knowledge base from the provided pages ONLY. Never add facts that are not "
    "supported by the pages. Every unit needs a verbatim evidence_quote copied exactly from the pages. "
    "Answer in JSON only."
)
KNOWLEDGE_SCHEMA = """Return JSON:
{"units": [{"title": str, "content_type": "definition|explanation|example|formula|diagram_explanation|table_explanation|code_example|slide_content|textbook_passage",
   "text": "self-contained explanation using only the pages",
   "visual_description": str or null,
   "page_numbers": [int],
   "evidence_quote": "verbatim sentence/phrase (12-200 chars) copied from the cited page(s)",
   "topic": str, "subtopic": str or null, "concepts": [str, max 8]}],
 "prerequisites": [{"concept": str, "requires": [str], "evidence_quote": "verbatim quote if the pages state it, else null"}]}
Rules: 1) Reuse an existing topic name when it fits. 2) Prerequisites = what a learner must know BEFORE this concept
(not merely what appears earlier in the document). 3) Skip pages with no academic content. 4) Short concept names, lowercase noun phrases."""


def _page_evidence(db, p: SourcePage) -> str:
    parts = []
    if p.embedded_text:
        parts.append(p.embedded_text)
    if p.ocr_text:
        parts.append(p.ocr_text)
    if p.visual_description:
        parts.append(f"[Visual summary] {p.visual_description}")
    for v in db.query(VisualElement).filter_by(page_id=p.id).all():
        d = v.data or {}
        bits = [f"[{v.visual_type}] {v.description or ''}"]
        if d.get("formula"):
            bits.append(f"formula: {d['formula']}")
        if d.get("table_summary"):
            bits.append(f"table: {d['table_summary']}")
        if d.get("diagram_flow"):
            bits.append("flow: " + " -> ".join(map(str, d["diagram_flow"])))
        if d.get("relationships"):
            bits.append("relations: " + "; ".join(map(str, d["relationships"])))
        parts.append(" ".join(bits))
    return "\n".join(parts).strip()


def _windows(pages_text, max_chars):
    out, cur, size = [], [], 0
    for n, t in pages_text:
        if not t:
            continue
        t = t[:max_chars]
        if cur and size + len(t) > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append((n, t))
        size += len(t)
    if cur:
        out.append(cur)
    return out


def _clean_list(xs, limit):
    seen, out = set(), []
    for x in xs or []:
        s = str(x).strip()
        if s and norm_name(s) not in seen:
            seen.add(norm_name(s))
            out.append(s)
    return out[:limit]


def _validate(out: dict, window) -> dict:
    text_by = dict(window)
    corpus_all = "\n".join(text_by.values())
    units, dropped = [], 0
    for u in out.get("units") or []:
        try:
            title, text = str(u.get("title") or "").strip(), str(u.get("text") or "").strip()
            topic, quote = str(u.get("topic") or "").strip(), str(u.get("evidence_quote") or "").strip()
            pages = [int(x) for x in (u.get("page_numbers") or []) if int(x) in text_by]
        except (TypeError, ValueError):
            dropped += 1
            continue
        if not (title and text and topic and quote):
            dropped += 1
            continue
        if not (pages and quote_in(quote, "\n".join(text_by[n] for n in pages))):
            found = [n for n in text_by if quote_in(quote, text_by[n])]
            if not found:
                dropped += 1  # quote not verifiable -> never keep an unverifiable citation
                continue
            pages = found[:3]
        ctype = u.get("content_type")
        units.append({
            "title": title, "text": text, "topic": topic,
            "subtopic": (str(u.get("subtopic")).strip() or None) if u.get("subtopic") else None,
            "content_type": ctype if ctype in CONTENT_TYPES else "explanation",
            "visual_description": u.get("visual_description") or None,
            "page_numbers": sorted(set(pages)), "evidence_quote": quote,
            "concepts": _clean_list(u.get("concepts"), 8),
        })
    prereqs = []
    for p in out.get("prerequisites") or []:
        c = str(p.get("concept") or "").strip()
        req = _clean_list(p.get("requires"), 6)
        if not (c and req):
            continue
        q = str(p.get("evidence_quote") or "").strip()
        stated = bool(q) and quote_in(q, corpus_all)
        prereqs.append({"concept": c, "requires": req,
                        "basis": "stated" if stated else "inferred",
                        "evidence_quote": q if stated else None})
    return {"units": units, "prerequisites": prereqs, "dropped": dropped}


def knowledge(ctx: StageContext):
    db, llm = ctx.db, get_llm()
    path = ctx.artifact_root / "knowledge.json"
    data = json.loads(path.read_text()) if path.exists() else {"windows": {}}

    pages = db.query(SourcePage).filter_by(source_id=ctx.source.id).order_by(SourcePage.page_number).all()
    windows = _windows([(p.page_number, _page_evidence(db, p)) for p in pages], settings.knowledge_window_chars)

    known = {t.name for t in db.query(Topic).filter_by(course_id=ctx.course_id)}
    live = {}
    for w in windows:
        blob = "\n\n".join(f"[Page {n}]\n{t}" for n, t in w)
        key = hashlib.sha1(blob.encode()).hexdigest()
        if key not in data["windows"]:
            prompt = (f"Source: {ctx.source.original_filename} (type: {ctx.source.file_type})\n"
                      f"Existing topics in this course: {sorted(known) or 'none yet'}\n\n"
                      f"{KNOWLEDGE_SCHEMA}\n\nPAGES:\n{blob}")
            data["windows"][key] = _validate(llm.generate_json(KNOWLEDGE_SYSTEM, prompt), w)
            data["windows"][key]["pages"] = [n for n, _ in w]
            path.write_text(json.dumps(data))  # persist after every LLM call
        live[key] = data["windows"][key]
        known |= {u["topic"] for u in live[key]["units"]}
    data["windows"] = live
    path.write_text(json.dumps(data))
    return {"windows": len(live), "units": sum(len(v["units"]) for v in live.values()),
            "dropped_unverifiable": sum(v["dropped"] for v in live.values())}


# =====================================================================
# 5. CONTENT_UNITS - write canonical knowledge + provenance to PostgreSQL
# =====================================================================
def _goc(db, model, defaults=None, **kw):
    obj = db.query(model).filter_by(**kw).first()
    if not obj:
        obj = model(**kw, **(defaults or {}))
        db.add(obj)
        db.flush()
    return obj


def _concept(db, course_id, name, subtopic_id=None):
    c = _goc(db, Concept, {"name": name.strip(), "subtopic_id": subtopic_id},
             course_id=course_id, norm_name=norm_name(name))
    if c.subtopic_id is None and subtopic_id:
        c.subtopic_id = subtopic_id
    return c


def content_units(ctx: StageContext):
    db, src = ctx.db, ctx.source
    path = ctx.artifact_root / "knowledge.json"
    data = json.loads(path.read_text())
    is_slides = src.file_type == "slides"

    # idempotent rebuild: remove units previously derived from this source
    old = [r[0] for r in db.query(ContentUnitSource.content_unit_id)
           .filter(ContentUnitSource.source_id == src.id).distinct()]
    if old:
        db.query(ContentUnit).filter(ContentUnit.id.in_(old)).delete(synchronize_session=False)

    n_units = 0
    for w in data["windows"].values():
        for u in w["units"]:
            topic = _goc(db, Topic, {"name": u["topic"]}, course_id=ctx.course_id, norm_name=norm_name(u["topic"]))
            sub = None
            if u["subtopic"]:
                sub = _goc(db, Subtopic, {"name": u["subtopic"], "course_id": ctx.course_id},
                           topic_id=topic.id, norm_name=norm_name(u["subtopic"]))
            vis = None
            if u["page_numbers"]:
                first = db.query(SourcePage).filter_by(source_id=src.id, page_number=u["page_numbers"][0]).first()
                if first:
                    els = db.query(VisualElement).filter_by(page_id=first.id).all()
                    vis = [{"visual_type": e.visual_type, "title": e.title,
                            "description": e.description, **(e.data or {})} for e in els] or None
            cu = ContentUnit(course_id=ctx.course_id, content_type=u["content_type"], title=u["title"],
                             text=u["text"], visual_description=u["visual_description"],
                             structured_visual_data=vis, topic_id=topic.id, subtopic_id=sub.id if sub else None)
            db.add(cu)
            db.flush()
            for pn in u["page_numbers"]:
                db.add(ContentUnitSource(content_unit_id=cu.id, source_id=src.id, page_number=pn,
                                         slide_number=pn if is_slides else None,
                                         evidence_quote=u["evidence_quote"]))
            for cname in u["concepts"]:
                c = _concept(db, ctx.course_id, cname, sub.id if sub else None)
                if not db.get(ContentUnitConcept, (cu.id, c.id)):
                    db.add(ContentUnitConcept(content_unit_id=cu.id, concept_id=c.id))
            n_units += 1

        for p in w["prerequisites"]:
            c = _concept(db, ctx.course_id, p["concept"])
            for rname in p["requires"]:
                r = _concept(db, ctx.course_id, rname)
                if r.id == c.id or db.get(ConceptPrerequisite, (c.id, r.id)) or db.get(ConceptPrerequisite, (r.id, c.id)):
                    continue  # no self loops / 2-cycles
                db.add(ConceptPrerequisite(concept_id=c.id, prerequisite_id=r.id,
                                           basis=p["basis"], evidence_quote=p["evidence_quote"]))
    db.commit()
    return {"content_units": n_units}


# =====================================================================
# 6. EMBEDDINGS
# =====================================================================
def embedding_text(db, cu: ContentUnit) -> str:
    names = [n for (n,) in db.query(Concept.name).join(ContentUnitConcept, ContentUnitConcept.concept_id == Concept.id)
             .filter(ContentUnitConcept.content_unit_id == cu.id)]
    parts = [f"Title: {cu.title}", f"Text: {cu.text}"]
    if cu.visual_description:
        parts.append(f"Visual: {cu.visual_description}")
    if names:
        parts.append("Concepts: " + ", ".join(names))
    return "\n".join(parts)


def embeddings(ctx: StageContext):
    db, emb = ctx.db, get_embedder()
    ids = [r[0] for r in db.query(ContentUnit.id)
           .join(ContentUnitSource, ContentUnitSource.content_unit_id == ContentUnit.id)
           .filter(ContentUnitSource.source_id == ctx.source.id, ContentUnit.embedding.is_(None)).distinct()]
    for i in range(0, len(ids), 32):
        batch = db.query(ContentUnit).filter(ContentUnit.id.in_(ids[i:i + 32])).all()
        vecs = emb.embed([embedding_text(db, u) for u in batch])
        for u, v in zip(batch, vecs):
            u.embedding = v
        db.commit()
    return {"embedded": len(ids)}


STAGE_FUNCS = {
    "EXTRACT_PAGES": extract_pages, "OCR": ocr, "VISION": vision,
    "KNOWLEDGE": knowledge, "CONTENT_UNITS": content_units, "EMBEDDINGS": embeddings,
}

