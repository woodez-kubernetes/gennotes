"""Prompt registry. Templates live as text files under ``prompts/<version>/``.

The ``PromptSpec`` returned by :func:`get_prompt_spec` is the only thing the
pipeline code interacts with; switching prompt versions is a matter of dropping
a new directory and listing it in ``KNOWN_VERSIONS``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPTS_ROOT = Path(__file__).parent / "prompts"
KNOWN_VERSIONS: tuple[str, ...] = ("v1",)


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class PromptSpec:
    version: str
    single: str  # placeholder: {transcript}
    map_: str    # placeholders: {index}, {total}, {chunk}
    reduce: str  # placeholders: {count}, {partials}

    def render_single(self, transcript: str) -> str:
        return self.single.format(transcript=transcript)

    def render_map(self, *, index: int, total: int, chunk: str) -> str:
        return self.map_.format(index=index, total=total, chunk=chunk)

    def render_reduce(self, *, partials: list[str]) -> str:
        joined = "\n\n---\n\n".join(
            f"### Partial {i + 1}\n{p.strip()}" for i, p in enumerate(partials)
        )
        return self.reduce.format(count=len(partials), partials=joined)


def get_prompt_spec(version: str) -> PromptSpec:
    if version not in KNOWN_VERSIONS:
        raise PromptError(
            f"unknown prompt version {version!r}; known: {KNOWN_VERSIONS}"
        )
    base = PROMPTS_ROOT / version
    try:
        single = (base / "single.txt").read_text()
        map_ = (base / "map.txt").read_text()
        reduce = (base / "reduce.txt").read_text()
    except FileNotFoundError as e:
        raise PromptError(f"prompt files missing for version {version!r}: {e}") from e
    return PromptSpec(version=version, single=single, map_=map_, reduce=reduce)


DEFAULT_VERSION = "v1"
