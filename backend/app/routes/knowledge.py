from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import UploadedFile
from ..models_kb import (Concept, ConceptPrerequisite, ContentUnit, ContentUnitConcept,
                         ContentUnitSource, Course, ProcessingJob, ProcessingStage, Subtopic, Topic)
from ..pipeline.runner import UnsupportedSource, plan_for, prepare_retry, run_job
from ..providers import get_embedder

router = APIRouter(prefix="/api", tags=["knowledge"])


# ---------- helpers ----------
def _default_course(db: Session) -> Course:
    c = db.query(Course).filter_by(name="Default Course").first()
    if not c:
        c = Course(name="Default Course")
        db.add(c)
        db.commit()
        db.refresh(c)
    return c


def _job_dict(db, job):
    stages = db.query(ProcessingStage).filter_by(job_id=job.id).order_by(ProcessingStage.position).all()
    return {"job_id": job.id, "source_id": job.source_id, "course_id": job.course_id, "status": job.status,
            "stages": [{"stage": s.stage, "status": s.status, "retry_count": s.retry_count, "error": s.error,
                        "started_at": s.started_at, "completed_at": s.completed_at, "output": s.output_ref}
                       for s in stages]}


def _sources(db, cu_id):
    rows = (db.query(ContentUnitSource, UploadedFile)
            .join(UploadedFile, UploadedFile.id == ContentUnitSource.source_id)
            .filter(ContentUnitSource.content_unit_id == cu_id)
            .order_by(ContentUnitSource.page_number).all())
    return [{"source_id": s.source_id, "file": f.original_filename, "source_type": f.file_type,
             "page": s.page_number, "slide": s.slide_number,
             "video_start": s.video_start_seconds, "video_end": s.video_end_seconds,
             "evidence_quote": s.evidence_quote} for s, f in rows]


def _unit(db, cu, score=None, with_sources=True):
    topic = db.get(Topic, cu.topic_id) if cu.topic_id else None
    sub = db.get(Subtopic, cu.subtopic_id) if cu.subtopic_id else None
    concepts = [n for (n,) in db.query(Concept.name).join(
        ContentUnitConcept, ContentUnitConcept.concept_id == Concept.id)
        .filter(ContentUnitConcept.content_unit_id == cu.id)]
    d = {"content_unit_id": cu.id, "title": cu.title, "content_type": cu.content_type, "text": cu.text,
         "visual_description": cu.visual_description, "structured_visual_data": cu.structured_visual_data,
         "topic": topic.name if topic else None, "subtopic": sub.name if sub else None, "concepts": concepts}
    if score is not None:
        d["score"] = round(score, 4)
    if with_sources:
        d["sources"] = _sources(db, cu.id)
    return d


# ---------- courses ----------
class CourseIn(BaseModel):
    name: str


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)):
    _default_course(db)
    return [{"id": c.id, "name": c.name} for c in db.query(Course).order_by(Course.id)]


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, db: Session = Depends(get_db)):
    if db.query(Course).filter_by(name=body.name).first():
        raise HTTPException(409, "Course already exists")
    c = Course(name=body.name)
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name}


# ---------- processing ----------
class ProcessIn(BaseModel):
    course_id: int | None = None


@router.post("/sources/{source_id}/process", status_code=202)
def process_source(source_id: int, bg: BackgroundTasks, body: ProcessIn | None = None,
                   db: Session = Depends(get_db)):
    source = db.get(UploadedFile, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    try:
        stages = plan_for(source)
    except UnsupportedSource as e:
        raise HTTPException(422, str(e))

    active = (db.query(ProcessingJob)
              .filter(ProcessingJob.source_id == source_id, ProcessingJob.status.in_(["queued", "processing"]))
              .first())
    if active:
        return {"source_id": source_id, "job_id": active.id, "status": active.status}

    course_id = (body.course_id if body and body.course_id else _default_course(db).id)
    if not db.get(Course, course_id):
        raise HTTPException(404, "Course not found")

    job = ProcessingJob(source_id=source_id, course_id=course_id, status="queued")
    db.add(job)
    db.flush()
    for i, name in enumerate(stages):
        db.add(ProcessingStage(job_id=job.id, stage=name, position=i))
    db.commit()
    bg.add_task(run_job, job.id)
    return {"source_id": source_id, "job_id": job.id, "status": "queued"}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_dict(db, job)


@router.get("/sources/{source_id}/jobs")
def source_jobs(source_id: int, db: Session = Depends(get_db)):
    jobs = db.query(ProcessingJob).filter_by(source_id=source_id).order_by(ProcessingJob.id.desc()).all()
    return [_job_dict(db, j) for j in jobs]


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: int, bg: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not prepare_retry(db, job):
        raise HTTPException(409, "Nothing to retry (no failed or stuck stage)")
    bg.add_task(run_job, job.id)
    return {"job_id": job.id, "status": "queued"}


# ---------- knowledge browsing ----------
@router.get("/courses/{course_id}/topics")
def list_topics(course_id: int, db: Session = Depends(get_db)):
    out = []
    for t in db.query(Topic).filter_by(course_id=course_id).order_by(Topic.name):
        subs = []
        for s in db.query(Subtopic).filter_by(topic_id=t.id).order_by(Subtopic.name):
            n = db.query(func.count(Concept.id)).filter(Concept.subtopic_id == s.id).scalar()
            subs.append({"id": s.id, "name": s.name, "concept_count": n})
        out.append({"id": t.id, "name": t.name, "subtopics": subs})
    return out


@router.get("/topics/{topic_id}")
def get_topic(topic_id: int, db: Session = Depends(get_db)):
    t = db.get(Topic, topic_id)
    if not t:
        raise HTTPException(404, "Topic not found")
    subs = db.query(Subtopic).filter_by(topic_id=t.id).order_by(Subtopic.name).all()
    return {"id": t.id, "name": t.name, "subtopics": [{"id": s.id, "name": s.name} for s in subs]}


@router.get("/subtopics/{subtopic_id}")
def get_subtopic(subtopic_id: int, db: Session = Depends(get_db)):
    s = db.get(Subtopic, subtopic_id)
    if not s:
        raise HTTPException(404, "Subtopic not found")
    cs = db.query(Concept).filter_by(subtopic_id=s.id).order_by(Concept.name).all()
    return {"id": s.id, "name": s.name, "topic_id": s.topic_id,
            "concepts": [{"id": c.id, "name": c.name} for c in cs]}


@router.get("/concepts/{concept_id}")
def get_concept(concept_id: int, db: Session = Depends(get_db)):
    c = db.get(Concept, concept_id)
    if not c:
        raise HTTPException(404, "Concept not found")
    sub = db.get(Subtopic, c.subtopic_id) if c.subtopic_id else None
    topic = db.get(Topic, sub.topic_id) if sub else None
    prereqs = (db.query(Concept, ConceptPrerequisite)
               .join(ConceptPrerequisite, ConceptPrerequisite.prerequisite_id == Concept.id)
               .filter(ConceptPrerequisite.concept_id == c.id).all())
    unlocks = (db.query(Concept).join(ConceptPrerequisite, ConceptPrerequisite.concept_id == Concept.id)
               .filter(ConceptPrerequisite.prerequisite_id == c.id).all())
    n = db.query(func.count(ContentUnitConcept.content_unit_id)).filter_by(concept_id=c.id).scalar()
    return {"id": c.id, "name": c.name,
            "topic": {"id": topic.id, "name": topic.name} if topic else None,
            "subtopic": {"id": sub.id, "name": sub.name} if sub else None,
            "prerequisites": [{"id": p.id, "name": p.name, "basis": e.basis, "evidence_quote": e.evidence_quote}
                              for p, e in prereqs],
            "required_by": [{"id": u.id, "name": u.name} for u in unlocks],
            "content_count": n}


@router.get("/concepts/{concept_id}/content")
def concept_content(concept_id: int, db: Session = Depends(get_db)):
    units = (db.query(ContentUnit).join(ContentUnitConcept, ContentUnitConcept.content_unit_id == ContentUnit.id)
             .filter(ContentUnitConcept.concept_id == concept_id).order_by(ContentUnit.id).all())
    return [_unit(db, u) for u in units]


@router.get("/content/{content_unit_id}")
def get_content(content_unit_id: int, db: Session = Depends(get_db)):
    cu = db.get(ContentUnit, content_unit_id)
    if not cu:
        raise HTTPException(404, "Content unit not found")
    return _unit(db, cu)


@router.get("/content/{content_unit_id}/sources")
def get_content_sources(content_unit_id: int, db: Session = Depends(get_db)):
    if not db.get(ContentUnit, content_unit_id):
        raise HTTPException(404, "Content unit not found")
    return _sources(db, content_unit_id)


# ---------- semantic search ----------
class SearchIn(BaseModel):
    query: str
    course_id: int | None = None
    top_k: int = 10


@router.post("/search")
def search(body: SearchIn, db: Session = Depends(get_db)):
    qvec = get_embedder().embed_query(body.query)
    dist = ContentUnit.embedding.cosine_distance(qvec)
    q = db.query(ContentUnit, dist.label("dist")).filter(ContentUnit.embedding.isnot(None))
    if body.course_id:
        q = q.filter(ContentUnit.course_id == body.course_id)
    rows = q.order_by(dist).limit(max(1, min(body.top_k, 50))).all()
    return [_unit(db, cu, score=1 - d) for cu, d in rows]
