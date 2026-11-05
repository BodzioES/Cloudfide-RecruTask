"""Deterministic retrieval over the Markdown policy documents.

The corpus is small (three files), so a keyword scorer is enough. It keeps the
tests offline and reproducible: no embeddings, no network, no model.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 1200
HEADING_WEIGHT = 3
K1 = 1.2
B = 0.75

HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$")
TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a an the is are am was were be been being do does did doing have has had
    having i me my mine you your yours he him his she her hers it its we our
    us they them their this that these those of to in on at for from by with
    about into over under and or but not no so if then than there here what
    when where which who whom whose why how can could will would shall should
    may might must please thanks thank hello hi much many
    """.split()
)


@dataclass(frozen=True)
class PolicyChunk:
    """One section of one policy document."""

    source: str
    document: str
    heading: str
    text: str
    line_start: int

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "document": self.document,
            "heading": self.heading,
            "text": self.text,
            "line_start": self.line_start,
        }


@dataclass(frozen=True)
class ScoredChunk:
    chunk: PolicyChunk
    score: float

    def to_dict(self) -> dict[str, object]:
        return {**self.chunk.to_dict(), "score": round(self.score, 4)}


@dataclass(frozen=True)
class _IndexedChunk:
    chunk: PolicyChunk
    counts: Counter
    length: int


def _clip(text: str) -> str:
    if len(text) <= MAX_CHUNK_CHARS:
        return text
    return text[:MAX_CHUNK_CHARS].rstrip() + "..."


def _normalize_token(token: str) -> str:
    """Very small stemmer: plurals and the common -ies ending."""

    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        if raw in STOPWORDS:
            continue
        tokens.append(_normalize_token(raw))
    return tokens


def _tokens_match(query_token: str, corpus_token: str) -> bool:
    """Exact match, or a shared prefix for words of at least four letters.

    This is what lets "cancel" find "cancelling" and "open" find "opening"
    without shipping a full stemming library.
    """

    if query_token == corpus_token:
        return True
    if len(query_token) >= 4 and len(corpus_token) >= 4:
        return query_token.startswith(corpus_token) or corpus_token.startswith(query_token)
    return False


def parse_markdown(source: str, text: str) -> list[PolicyChunk]:
    """Split one document into chunks at ``#`` and ``##`` headings."""

    lines = text.splitlines()
    document = Path(source).stem
    chunks: list[PolicyChunk] = []
    heading = document
    heading_line = 1
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            chunks.append(
                PolicyChunk(
                    source=source,
                    document=document,
                    heading=heading,
                    text=_clip(body),
                    line_start=heading_line,
                )
            )

    for number, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            if level == 1:
                document = title
            heading = title
            heading_line = number
            buffer = []
        else:
            buffer.append(line)
    flush()
    return chunks


class PolicyIndex:
    """In-memory BM25-lite index over policy chunks."""

    def __init__(self, chunks: list[PolicyChunk]) -> None:
        if not chunks:
            raise ValueError("no policy chunks were loaded")

        self._indexed: list[_IndexedChunk] = []
        document_frequency: Counter = Counter()

        for chunk in chunks:
            tokens = tokenize(chunk.heading) * HEADING_WEIGHT + tokenize(chunk.text)
            counts = Counter(tokens)
            self._indexed.append(_IndexedChunk(chunk, counts, len(tokens)))
            document_frequency.update(counts.keys())

        self._df = document_frequency
        self._vocab = tuple(sorted(document_frequency))
        self._avg_len = sum(item.length for item in self._indexed) / len(self._indexed)

    @classmethod
    def from_directory(cls, path: Path) -> "PolicyIndex":
        directory = Path(path)
        if not directory.is_dir():
            raise FileNotFoundError(f"policy directory not found: {directory}")
        chunks: list[PolicyChunk] = []
        for file in sorted(directory.glob("*.md")):
            chunks.extend(parse_markdown(file.name, file.read_text(encoding="utf-8")))
        return cls(chunks)

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> "PolicyIndex":
        return cls.from_directory(Path(data_dir) / "policies")

    @property
    def chunks(self) -> tuple[PolicyChunk, ...]:
        return tuple(item.chunk for item in self._indexed)

    def search(self, query: str, top_k: int = 3) -> list[ScoredChunk]:
        terms = tokenize(query or "")
        if not terms or top_k <= 0:
            return []

        expanded: set[str] = set()
        for term in terms:
            for token in self._vocab:
                if _tokens_match(term, token):
                    expanded.add(token)
        if not expanded:
            return []

        total = len(self._indexed)
        results: list[ScoredChunk] = []

        for item in self._indexed:
            score = 0.0
            for token in expanded:
                frequency = item.counts.get(token, 0)
                if not frequency:
                    continue
                df = self._df[token]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                normaliser = frequency + K1 * (
                    1 - B + B * item.length / self._avg_len
                )
                score += idf * (frequency * (K1 + 1)) / normaliser
            if score > 0:
                results.append(ScoredChunk(item.chunk, score))

        results.sort(key=lambda result: (-result.score, result.chunk.source, result.chunk.line_start))
        return results[:top_k]
