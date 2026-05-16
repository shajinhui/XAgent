"""Context truncation helpers."""

from __future__ import annotations


def truncate_text(text: str, max_chars: int, notice: str = "\n...[truncated]") -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(notice))
    return f"{text[:keep]}{notice}"
