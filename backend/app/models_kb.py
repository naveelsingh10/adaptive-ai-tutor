from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from .config import settings
from .database import Base


def _now():
    return datetime.now(timezone.utc)


def _ts():
    return DateTime(timezone=True)


class Course(Base):
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(_ts(), default=_now)


# ---------------- processing jobs ----------------
class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id = Column(Integer, primary_key=True)
    source_id = Column(
        Integer,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    status = Column(String, default="queued")  # queued|processing|completed|partial|failed
    created_at = Column(_ts(), default=_now)
    updated_at = Column(_ts(), default=_now, onupdate=_now)


class ProcessingStage(Base):
    __tablename__ = "processing_stages"
    __table_args__ = (UniqueConstraint("job_id", "stage"),)

    id = Column(Integer, primary_key=True)
    job_id = Column(
        Integer,
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    stage = Column(String, nullable=False)
    position = Column(Integer, nullable=False)
    status = Column(String, default="pending")  # pending|processing|completed|failed
    retry_count = Column(Integer, default=0)
    started_at = Column(_ts())
    completed_at = Column(_ts())
    error = Column(Text)
    output_ref = Column(JSONB)


# ---------------- extraction evidence ----------------
class SourcePage(Base):
    __tablename__ = "source_pages"
    __table_args__ = (UniqueConstraint("source_id", "page_number"),)

    id = Column(Integer, primary_key=True)
    source_id = Column(
        Integer,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    page_number = Column(Integer, nullable=False)
    slide_number = Column(Integer)  # set for slide decks
    image_path = Column(String)
    embedded_text = Column(Text)  # OBSERVED: text layer of the PDF
    ocr_text = Column(Text)  # OBSERVED: OCR output
    has_visuals = Column(Boolean, default=False)
    visual_description = Column(
        Text
    )  # INFERENCE: vision model summary (None = not analysed yet)


class VisualElement(Base):
    __tablename__ = "visual_elements"

    id = Column(Integer, primary_key=True)
    source_id = Column(
        Integer,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    page_id = Column(
        Integer,
        ForeignKey("source_pages.id", ondelete="CASCADE"),
        index=True,
    )
    visual_type = Column(String)
    title = Column(String)
    description = Column(Text)
    data = Column(
        JSONB
    )  # labels, relationships, concepts, formula, table_summary, diagram_flow, meaning
    origin = Column(String, default="vision_model")


# ---------------- knowledge organisation ----------------
class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("course_id", "norm_name"),)

    id = Column(Integer, primary_key=True)
    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name = Column(String, nullable=False)
    norm_name = Column(String, nullable=False)


class Subtopic(Base):
    __tablename__ = "subtopics"
    __table_args__ = (UniqueConstraint("topic_id", "norm_name"),)

    id = Column(Integer, primary_key=True)
    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    topic_id = Column(
        Integer,
        ForeignKey("topics.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name = Column(String, nullable=False)
    norm_name = Column(String, nullable=False)


class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (UniqueConstraint("course_id", "norm_name"),)

    id = Column(Integer, primary_key=True)
    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subtopic_id = Column(
        Integer,
        ForeignKey("subtopics.id", ondelete="SET NULL"),
        index=True,
    )
    name = Column(String, nullable=False)
    norm_name = Column(String, nullable=False)


class ConceptPrerequisite(Base):
    __tablename__ = "concept_prerequisites"

    concept_id = Column(
        Integer,
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    prerequisite_id = Column(
        Integer,
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    basis = Column(
        String,
        default="inferred",
    )  # stated (quote verified in source) | inferred (model judgement)
    evidence_quote = Column(Text)


class ContentUnit(Base):
    __tablename__ = "content_units"

    id = Column(Integer, primary_key=True)
    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    content_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    visual_description = Column(Text)
    structured_visual_data = Column(JSONB)
    topic_id = Column(
        Integer,
        ForeignKey("topics.id", ondelete="SET NULL"),
    )
    subtopic_id = Column(
        Integer,
        ForeignKey("subtopics.id", ondelete="SET NULL"),
    )
    origin = Column(String, default="llm_grounded")
    embedding = Column(Vector(settings.embedding_dim))
    created_at = Column(_ts(), default=_now)

    __table_args__ = (
        Index(
            "ix_content_units_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ContentUnitSource(Base):
    """Provenance. A unit can have many of these (textbook page + slide + video segment...)."""

    __tablename__ = "content_unit_sources"

    id = Column(Integer, primary_key=True)
    content_unit_id = Column(
        Integer,
        ForeignKey("content_units.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_id = Column(
        Integer,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    page_number = Column(Integer)
    slide_number = Column(Integer)
    video_start_seconds = Column(Float)
    video_end_seconds = Column(Float)
    evidence_quote = Column(Text)


class ContentUnitConcept(Base):
    __tablename__ = "content_unit_concepts"

    content_unit_id = Column(
        Integer,
        ForeignKey("content_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    concept_id = Column(
        Integer,
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )
