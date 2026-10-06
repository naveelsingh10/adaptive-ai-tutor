from ..config import settings
from .base import EmbeddingProvider


class SentenceTransformerEmbedder(EmbeddingProvider):
    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self.name = settings.embedding_model
        self.model = SentenceTransformer(self.name)

        dim = self.model.get_sentence_embedding_dimension()
        if dim != settings.embedding_dim:
            raise RuntimeError(
                f"EMBEDDING_DIM={settings.embedding_dim} "
                f"but {self.name} outputs {dim}"
            )

    def embed(self, texts):
        return self.model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
        ).tolist()

    def embed_query(self, text):
        prefix = (
            "Represent this sentence for searching relevant passages: "
            if "bge" in self.name.lower()
            else ""
        )
        return self.embed([prefix + text])[0]
