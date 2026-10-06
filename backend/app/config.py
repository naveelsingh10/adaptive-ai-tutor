import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    database_url = os.getenv("DATABASE_URL")
    artifact_dir = Path(os.getenv("ARTIFACT_DIR", "artifacts")).resolve()

    llm_provider = os.getenv("LLM_PROVIDER", "gemini")
    vision_provider = os.getenv("VISION_PROVIDER", llm_provider)
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_llm_model = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:7b-instruct")
    ollama_vision_model = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")

    embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "384"))

    vision_max_pages_per_run = int(os.getenv("VISION_MAX_PAGES_PER_RUN", "30"))
    ocr_min_chars = int(os.getenv("OCR_MIN_CHARS", "40"))
    knowledge_window_chars = int(os.getenv("KNOWLEDGE_WINDOW_CHARS", "9000"))


settings = Settings()
settings.artifact_dir.mkdir(parents=True, exist_ok=True)
