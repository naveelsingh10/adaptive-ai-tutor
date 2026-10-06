import re

from rapidfuzz import fuzz


def norm_name(s: str) -> str:
    s = re.sub(r"[^\w\s+#\-]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def quote_in(quote: str, corpus: str, threshold: int = 88) -> bool:
    """True if the quote really appears (allowing small OCR noise)."""
    q, c = norm_text(quote), norm_text(corpus)
    if len(q) < 12 or not c:
        return False
    return q in c or fuzz.partial_ratio(q, c) >= threshold
