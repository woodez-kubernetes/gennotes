"""Pipeline tests with the llama wrapper mocked.

The real ``llama-cli`` isn't available in CI, so we patch ``llamacpp.generate``
and ``llamacpp.tokenize_count`` and verify the orchestration logic.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from recordings.summarize import pipeline
from recordings.summarize.chunking import Segment
from recordings.summarize.llamacpp import GenerateOptions, GenerateResult


def _fake_generate(call_log: list[str]) -> Callable[[str, Path, GenerateOptions], GenerateResult]:
    def _gen(prompt: str, model_path: Path, options: GenerateOptions) -> GenerateResult:
        call_log.append(prompt)
        return GenerateResult(
            text=f"<summary {len(call_log)}>",
            model_name=model_path.name,
            prompt_chars=len(prompt),
            output_chars=10,
        )
    return _gen


def test_single_pass_when_transcript_fits(tmp_path: Path) -> None:
    model = tmp_path / "stub.gguf"
    model.write_bytes(b"\x00")  # exists; never read because we mock generate

    calls: list[str] = []
    with patch("recordings.summarize.pipeline.llamacpp.tokenize_count", return_value=10), \
         patch("recordings.summarize.pipeline.llamacpp.generate", side_effect=_fake_generate(calls)):
        result = pipeline.summarize(
            transcript_text="Alice: Hello. Bob: World.",
            segments=[Segment(0, 1, "Hello."), Segment(1, 2, "World.")],
            model_path=model,
        )
    assert result.n_chunks == 1
    assert len(calls) == 1
    assert "TRANSCRIPT" in calls[0]
    assert result.text.startswith("<summary")
    assert result.prompt_version == "v1"


def test_map_reduce_when_transcript_too_big(tmp_path: Path) -> None:
    model = tmp_path / "stub.gguf"
    model.write_bytes(b"\x00")

    # Three segments, fake-counted as 4000 tokens each → total 12000.
    # Default n_ctx=8192 → budget ≈ 6468 → needs splitting into ≥3 chunks.
    segments = [
        Segment(0, 60, "A " * 200),
        Segment(60, 120, "B " * 200),
        Segment(120, 180, "C " * 200),
    ]
    transcript = " ".join(s.text for s in segments)

    def tokens(text: str, model_path: Path | None = None) -> int:
        # 10 tokens per character — total transcript ≈ 12000, each seg ≈ 4000.
        return max(1, len(text) * 10)

    calls: list[str] = []
    with patch("recordings.summarize.pipeline.llamacpp.tokenize_count", side_effect=tokens), \
         patch("recordings.summarize.pipeline.llamacpp.generate", side_effect=_fake_generate(calls)):
        result = pipeline.summarize(
            transcript_text=transcript,
            segments=segments,
            model_path=model,
        )
    assert result.n_chunks > 1
    # one call per chunk (map) plus one reduce call
    assert len(calls) == result.n_chunks + 1
    # final call should be the reduce prompt (contains "PARTIAL SUMMARIES")
    assert "PARTIAL SUMMARIES" in calls[-1]
    # map calls should reference chunk index/total
    assert any("CHUNK 1 of" in c for c in calls[:-1])


def test_empty_transcript_raises(tmp_path: Path) -> None:
    model = tmp_path / "stub.gguf"
    model.write_bytes(b"\x00")
    with pytest.raises(ValueError):
        pipeline.summarize(transcript_text="   \n   ", model_path=model)
