"""Pin the rendered v1 prompts to golden fixtures.

If you intentionally change a prompt template, run with
``pytest --snapshot-update`` or update the fixture file by hand — the diff in
the PR should reflect the intent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from recordings.summarize.prompts import KNOWN_VERSIONS, PromptError, get_prompt_spec

FIXTURES = Path(__file__).parent / "fixtures"


def test_known_versions_contain_v1() -> None:
    assert "v1" in KNOWN_VERSIONS


def test_get_prompt_spec_v1_loads() -> None:
    spec = get_prompt_spec("v1")
    assert spec.version == "v1"
    assert "{transcript}" in spec.single
    assert "{chunk}" in spec.map_
    assert "{partials}" in spec.reduce


def test_unknown_version_errors() -> None:
    with pytest.raises(PromptError):
        get_prompt_spec("nope")


def test_render_single_golden() -> None:
    spec = get_prompt_spec("v1")
    rendered = spec.render_single("Alice: We will ship Friday.\nBob: Confirmed.")
    golden = FIXTURES / "prompt_v1_single.txt"
    if not golden.exists():
        golden.write_text(rendered)
    assert rendered == golden.read_text()


def test_render_map_golden() -> None:
    spec = get_prompt_spec("v1")
    rendered = spec.render_map(index=2, total=3, chunk="Alice: We can ship by Friday.")
    golden = FIXTURES / "prompt_v1_map.txt"
    if not golden.exists():
        golden.write_text(rendered)
    assert rendered == golden.read_text()


def test_render_reduce_golden() -> None:
    spec = get_prompt_spec("v1")
    rendered = spec.render_reduce(partials=[
        "## Decisions\n- Ship Friday.",
        "## Decisions\n- None recorded in this chunk.",
    ])
    golden = FIXTURES / "prompt_v1_reduce.txt"
    if not golden.exists():
        golden.write_text(rendered)
    assert rendered == golden.read_text()
