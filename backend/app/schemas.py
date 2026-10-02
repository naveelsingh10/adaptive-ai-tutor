from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UploadResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str


class UploadedFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_type: str
    mime_type: str | None
    file_size: int
    status: str
    uploaded_at: datetime
