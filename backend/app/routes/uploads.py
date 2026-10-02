import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import UploadedFile
from ..schemas import UploadResponse, UploadedFileOut

load_dotenv()

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024

ALLOWED_EXTENSIONS = {
    "video": {".mp4", ".mkv", ".mov"},
    "textbook": {".pdf"},
    "slides": {".ppt", ".pptx", ".pdf"},
    "document": {".pdf"},
}


@router.post("/", response_model=UploadResponse, status_code=201)
async def upload_file(
    content_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if content_type not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Invalid content type. Choose one of: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    original_name = os.path.basename(file.filename or "")
    ext = Path(original_name).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS[content_type]:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS[content_type]))
        raise HTTPException(
            400,
            f"'{ext or 'no extension'}' is not allowed for "
            f"{content_type}. Allowed: {allowed}",
        )

    stored_name = f"{uuid4().hex}{ext}"
    path = UPLOAD_DIR / stored_name
    size = 0

    try:
        with open(path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > MAX_BYTES:
                    raise HTTPException(
                        413,
                        f"File too large. Max is "
                        f"{MAX_BYTES // (1024 * 1024)} MB.",
                    )

                out.write(chunk)

    except HTTPException:
        path.unlink(missing_ok=True)
        raise

    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(500, "Failed to save file.")

    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "File is empty.")

    record = UploadedFile(
        filename=stored_name,
        original_filename=original_name,
        file_type=content_type,
        mime_type=file.content_type,
        file_size=size,
        storage_location=str(path),
        status="uploaded",
    )

    try:
        db.add(record)
        db.commit()
        db.refresh(record)

    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(500, "Failed to save metadata.")

    return UploadResponse(
        id=record.id,
        filename=record.original_filename,
        file_type=record.file_type,
        status=record.status,
    )


@router.get("/", response_model=list[UploadedFileOut])
def list_uploads(db: Session = Depends(get_db)):
    return (
        db.query(UploadedFile)
        .order_by(UploadedFile.uploaded_at.desc())
        .all()
    )
