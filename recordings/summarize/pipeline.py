"""High-level summarization pipeline. Chooses single-pass vs map-reduce
based on transcript size, then delegates to the llama.cpp wrapper.

Public surface:
    summarize(transcript_text, segments, prompt_version, ...) -> SummaryResult
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from . import llamacpp
from .chunking import Segment, plan_chunks
from .prompts import DEFAULT_VERSION, get_prompt_spec

log = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    text: str
    model_name: str
    prompt_version: str
    n_chunks: int  # 1 for single-pass, >1 for map-reduce


def _opts_from_env() -> llamacpp.GenerateOptions:
    """Allow user to override decoding params without touching code."""
    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        return int(raw) if raw else default

    def _float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        return float(raw) if raw else default

    return llamacpp.GenerateOptions(
        temperature=_float("GENNOTES_LLAMA_TEMP", 0.2),
        top_p=_float("GENNOTES_LLAMA_TOP_P", 0.9),
        seed=_int("GENNOTES_LLAMA_SEED", 1234),
        n_predict=_int("GENNOTES_LLAMA_N_PREDICT", 1024),
        n_ctx=_int("GENNOTES_LLAMA_N_CTX", 0),
        threads=_int("GENNOTES_LLAMA_THREADS", 0),
    )


def summarize(
    transcript_text: str,
    segments: list[Segment] | None = None,
    *,
    prompt_version: str = DEFAULT_VERSION,
    model_path: Path | None = None,
) -> SummaryResult:
    if not transcript_text.strip():
        raise ValueError("transcript_text is empty")

    spec = get_prompt_spec(prompt_version)
    if model_path is None:
        model_path = llamacpp.resolve_model_path()

    opts = _opts_from_env()
    # Estimate token cost using llama-tokenize when present, else heuristic.
    plan = plan_chunks(
        segments or [],
        transcript_text,
        count_tokens=lambda t: llamacpp.tokenize_count(t, model_path),
        n_ctx=opts.n_ctx or 8192,
        output_tokens=opts.n_predict,
    )
    log.info(
        "summarize: %d chunks, %d total tokens (budget=%d, single_pass=%s)",
        len(plan.chunks), plan.total_tokens, plan.chunk_token_budget,
        plan.fits_single_pass,
    )

    if plan.fits_single_pass:
        prompt = spec.render_single(transcript_text)
        result = llamacpp.generate(prompt, model_path, opts)
        return SummaryResult(
            text=result.text,
            model_name=result.model_name,
            prompt_version=prompt_version,
            n_chunks=1,
        )

    # Map step
    partials: list[str] = []
    for i, chunk in enumerate(plan.chunks, start=1):
        prompt = spec.render_map(index=i, total=len(plan.chunks), chunk=chunk.text)
        partial = llamacpp.generate(prompt, model_path, opts)
        partials.append(partial.text)

    # Reduce step
    reduce_prompt = spec.render_reduce(partials=partials)
    final = llamacpp.generate(reduce_prompt, model_path, opts)
    return SummaryResult(
        text=final.text,
        model_name=final.model_name,
        prompt_version=prompt_version,
        n_chunks=len(plan.chunks),
    )
