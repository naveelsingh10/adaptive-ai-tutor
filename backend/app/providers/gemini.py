import time

from google import genai
from google.genai import types

from ..config import settings
from .base import LLMProvider, VisionProvider, parse_json


class GeminiProvider(LLMProvider, VisionProvider):
    def __init__(self):
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model

    def _call(self, contents, system: str) -> dict:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.1,
        )
        delay = 8
        for attempt in range(5):
            try:
                r = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=cfg,
                )
                return parse_json(r.text)
            except Exception as e:  # retry only on rate-limit / overload
                msg = str(e)
                transient = any(
                    k in msg
                    for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")
                )
                if transient and attempt < 4:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise

    def generate_json(self, system: str, prompt: str) -> dict:
        return self._call([prompt], system)

    def analyze_image(self, image_bytes: bytes, system: str, prompt: str) -> dict:
        part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
        return self._call([part, prompt], system)

