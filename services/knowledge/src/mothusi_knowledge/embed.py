"""Embedders (knowledge.md §6.4): bge-m3 on CPU for real, a hashing double for tests."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

MODEL = "BAAI/bge-m3"
DIM = 1024


class Embedder(Protocol):
    dim: int
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class BgeM3Embedder:
    """sentence-transformers on CPU, normalised. The weights are baked into the image at build
    time (HF_HOME), so nothing is fetched while serving."""

    dim = DIM
    model = MODEL

    def __init__(self, batch_size: int = 16) -> None:
        from sentence_transformers import SentenceTransformer

        self._model: Any = SentenceTransformer(MODEL, device="cpu")
        self._batch = batch_size

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), batch_size=self._batch, normalize_embeddings=True, convert_to_numpy=True
        )
        return [[float(x) for x in row] for row in vectors]


class HashingEmbedder:
    """A deterministic test double: words hashed into `dim` buckets, L2-normalised. Texts that
    share words are close, so search order is testable without a model."""

    model = "hashing"

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            out.append([v / norm for v in vector])
        return out
