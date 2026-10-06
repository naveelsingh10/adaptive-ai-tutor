from datetime import datetime, timezone
from pathlib import Path

from ..database import SessionLocal
from ..models import UploadedFile
from ..models_kb import ProcessingJob, ProcessingStage
from .stages import STAGE_FUNCS, StageContext


PAGE_STAGES = [
    "EXTRACT_PAGES",
    "OCR",
    "VISION",
    "KNOWLEDGE",
    "CONTENT_UNITS",
    "EMBEDDINGS",
]

OPTIONAL = {"VISION"}  # failure here must not block the rest (text/OCR stay usable)


class UnsupportedSource(Exception):
    pass


def plan_for(source: UploadedFile) -> list[str]:
    """Source router: decides the stage plan from the existing V0.1 record."""
    ext = Path(source.original_filename).suffix.lower()

    if ext in {".pdf", ".ppt", ".pptx"}:
        return PAGE_STAGES

    if ext in {".mp4", ".mkv", ".mov"}:
        raise UnsupportedSource(
            "Video processing is not implemented yet (next milestone)."
        )

    raise UnsupportedSource(f"Unsupported file type '{ext}'.")


def _now():
    return datetime.now(timezone.utc)


def run_job(job_id: int):
    db = SessionLocal()

    try:
        job = db.get(ProcessingJob, job_id)
        source = db.get(UploadedFile, job.source_id)

        job.status = "processing"
        db.commit()

        ctx = StageContext(db, job, source)

        stages = (
            db.query(ProcessingStage)
            .filter_by(job_id=job_id)
            .order_by(ProcessingStage.position)
            .all()
        )

        for st in stages:
            if st.status == "completed":
                continue  # resumable: never redo finished stages

            st.status, st.started_at, st.error = "processing", _now(), None
            db.commit()

            try:
                out = STAGE_FUNCS[st.stage](ctx)
                st.status, st.completed_at, st.output_ref = (
                    "completed",
                    _now(),
                    out or {},
                )
                db.commit()

            except Exception as e:
                db.rollback()

                st = db.get(ProcessingStage, st.id)
                st.status, st.completed_at = "failed", _now()
                st.error = f"{type(e).__name__}: {e}"[:2000]
                db.commit()

                if st.stage not in OPTIONAL:
                    break

        db.expire_all()

        statuses = [
            s.status
            for s in db.query(ProcessingStage).filter_by(job_id=job_id)
        ]

        job = db.get(ProcessingJob, job_id)

        if all(s == "completed" for s in statuses):
            job.status = "completed"
        elif any(s == "completed" for s in statuses):
            job.status = "partial"
        else:
            job.status = "failed"

        db.commit()

    finally:
        db.close()


def prepare_retry(db, job: ProcessingJob):
    """Reset the first failed/stuck stage and everything after it; earlier stages stay completed."""
    stages = (
        db.query(ProcessingStage)
        .filter_by(job_id=job.id)
        .order_by(ProcessingStage.position)
        .all()
    )

    bad = next(
        (i for i, s in enumerate(stages) if s.status in ("failed", "processing")),
        None,
    )

    if bad is None:
        return False

    for s in stages[bad:]:
        if s.status in ("failed", "processing"):
            s.retry_count = (s.retry_count or 0) + 1

        s.status, s.error = "pending", None

    job.status = "queued"
    db.commit()

    return True
