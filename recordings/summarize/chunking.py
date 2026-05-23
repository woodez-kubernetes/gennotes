"""Split a transcript into chunks for map-reduce summarization.

Splits on segment boundaries (never mid-segment) so each chunk reads as
natural speech. The token counter is injected so this module stays pure and
testable without the llama binary.

Budget math:
    chunk_token_budget = n_ctx - prompt_overhead - n_predict_per_chunk

Anything larger than the budget gets split; smaller transcripts get one
single chunk and the caller takes the single-pass path.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

# Cost model defaults — tuned for a Llama-3-class 8K context model.
DEFAULT_N_CTX = 8192
# How many tokens to reserve for the prompt template + system tokens + safety.
DEFAULT_PROMPT_OVERHEAD = 700
# How many tokens the model is allowed to spend on the output per call.
DEFAULT_OUTPUT_TOKENS = 1024
# Soft cap on the number of partial summaries the reduce step will receive.
# Beyond this, we'd need recursive reduction (deferred to a future stage).
MAX_PARTIALS = 16


@dataclass(frozen=True)
class Segment:
    """A timecoded chunk of speech. Mirrors ``Transcript.segments`` rows."""
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Chunk:
    text: str
    token_estimate: int
    segment_start_index: int
    segment_end_index: int  # inclusive

    @property
    def time_span(self) -> tuple[float | None, float | None]:
        return (None, None)  # filled in by ``segments_to_chunks`` when known


@dataclass(frozen=True)
class ChunkPlan:
    chunks: list[Chunk]
    total_tokens: int
    fits_single_pass: bool
    chunk_token_budget: int


def chunk_token_budget(
    *,
    n_ctx: int = DEFAULT_N_CTX,
    prompt_overhead: int = DEFAULT_PROMPT_OVERHEAD,
    output_tokens: int = DEFAULT_OUTPUT_TOKENS,
) -> int:
    budget = n_ctx - prompt_overhead - output_tokens
    if budget < 256:
        raise ValueError(
            f"chunk budget too small ({budget} tokens); n_ctx={n_ctx} is "
            "too small for the configured overhead+output."
        )
    return budget


def plan_chunks(
    segments: Iterable[Segment],
    transcript_text: str,
    *,
    count_tokens: Callable[[str], int],
    n_ctx: int = DEFAULT_N_CTX,
    prompt_overhead: int = DEFAULT_PROMPT_OVERHEAD,
    output_tokens: int = DEFAULT_OUTPUT_TOKENS,
) -> ChunkPlan:
    """Decide whether to do single-pass or map-reduce, and produce the chunks.

    Each transcript segment lands in exactly one chunk (no overlap). Overlap
    would help continuity at chunk seams, but it makes the cost model harder
    to reason about — and in practice the reduce step already smooths seams.

    ``segments`` may be empty (e.g. only the joined transcript is known) —
    we then return a single chunk and trust the caller to handle a context
    overflow from llama-cli.
    """
    budget = chunk_token_budget(
        n_ctx=n_ctx,
        prompt_overhead=prompt_overhead,
        output_tokens=output_tokens,
    )
    total = count_tokens(transcript_text)
    seglist = list(segments)

    if total <= budget or not seglist:
        return ChunkPlan(
            chunks=[Chunk(
                text=transcript_text.strip(),
                token_estimate=total,
                segment_start_index=0,
                segment_end_index=max(0, len(seglist) - 1),
            )],
            total_tokens=total,
            fits_single_pass=True,
            chunk_token_budget=budget,
        )

    chunks: list[Chunk] = []
    current: list[Segment] = []
    current_tokens = 0
    start_idx = 0

    def _flush(next_start_idx: int) -> None:
        nonlocal current, current_tokens, start_idx
        if not current:
            return
        chunks.append(Chunk(
            text=" ".join(s.text.strip() for s in current).strip(),
            token_estimate=current_tokens,
            segment_start_index=start_idx,
            segment_end_index=start_idx + len(current) - 1,
        ))
        current = []
        current_tokens = 0
        start_idx = next_start_idx

    for i, seg in enumerate(seglist):
        seg_tokens = count_tokens(seg.text)
        if current and current_tokens + seg_tokens > budget:
            _flush(next_start_idx=i)
        current.append(seg)
        current_tokens += seg_tokens

    _flush(next_start_idx=len(seglist))

    if len(chunks) > MAX_PARTIALS:
        raise ValueError(
            f"transcript would produce {len(chunks)} chunks (cap is "
            f"{MAX_PARTIALS}); use a model with a larger context window."
        )

    return ChunkPlan(
        chunks=chunks,
        total_tokens=total,
        fits_single_pass=False,
        chunk_token_budget=budget,
    )
