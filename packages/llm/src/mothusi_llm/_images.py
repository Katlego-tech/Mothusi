"""Image bytes for the vision-capable adapters (llm-seam.md §11.1)."""

from __future__ import annotations

from mothusi_llm.protocol import ProviderError

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


def media_type(image: bytes, provider: str) -> str:
    """ "image/png" or "image/jpeg" from the file signature; anything else cannot be sent."""
    if image.startswith(_PNG):
        return "image/png"
    if image.startswith(_JPEG):
        return "image/jpeg"
    raise ProviderError(f"{provider}: an image must be PNG or JPEG", retryable=False)
