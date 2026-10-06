import base64

import httpx

from ..config import settings
from .base import LLMProvider, VisionProvider, parse_json


class OllamaProvider(LLMProvider, VisionProvider):
    def _chat(self, model, system, prompt, images=None) -> dict:
        msg = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [base64.b64encode(i).decode() for i in images]

        r = httpx.post(
            f"{settings.ollama_url}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
                "messages": [
                    {"role": "system", "content": system},
                    msg,
                ],
            },
            timeout=900,
        )
        r.raise_for_status()
        return parse_json(r.json()["message"]["content"])

    def generate_json(self, system, prompt):
        return self._chat(
            settings.ollama_llm_model,
            system,
            prompt,
        )

    def analyze_image(self, image_bytes, system, prompt):
        return self._chat(
            settings.ollama_vision_model,
            system,
            prompt,
            [image_bytes],
        )
