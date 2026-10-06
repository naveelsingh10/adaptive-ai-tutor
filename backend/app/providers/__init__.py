from functools import lru_cache

from ..config import settings


def _build(name: str):
    if name == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider()

    if name == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider()

    raise ValueError(f"Unknown provider '{name}' (use gemini or ollama)")


@lru_cache
def get_llm():
    return _build(settings.llm_provider)


@lru_cache
def get_vision():
    return _build(settings.vision_provider)


@lru_cache
def get_embedder():
    from .embedder import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder()
