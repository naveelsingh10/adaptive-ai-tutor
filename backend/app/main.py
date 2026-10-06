from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .database import Base, engine
from . import models, models_kb  # noqa: F401
from .config import settings
from .routes import uploads, knowledge


with engine.begin() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Adaptive AI Tutor - V0.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# V0.1 upload functionality — preserved
app.include_router(uploads.router)

# V0.2 knowledge-base API
app.include_router(knowledge.router)

# V0.2 generated page/slide artifacts
app.mount(
    "/artifacts",
    StaticFiles(directory=settings.artifact_dir),
    name="artifacts",
)


@app.get("/")
def health():
    return {"status": "ok"}
