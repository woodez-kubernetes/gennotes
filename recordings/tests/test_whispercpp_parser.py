from __future__ import annotations

import json
from pathlib import Path

import pytest

from recordings.stt.whispercpp import WhisperError, parse_whisper_json

FIXTURE = Path(__file__).parent / "fixtures" / "whisper_sample.json"


def test_parse_whisper_golden_fixture() -> None:
    payload = json.loads(FIXTURE.read_text())
    result = parse_whisper_json(payload)
    assert result.language == "en"
    assert len(result.segments) == 3
    assert result.segments[0].start == pytest.approx(0.0)
    assert result.segments[0].end == pytest.approx(2.5)
    assert result.segments[1].start == pytest.approx(2.5)
    assert result.segments[2].end == pytest.approx(7.75)
    assert "Hello world." in result.text
    assert "Goodbye." in result.text


def test_parse_rejects_non_list_transcription() -> None:
    with pytest.raises(WhisperError):
        parse_whisper_json({"transcription": "not a list"})


def test_parse_tolerates_missing_language() -> None:
    payload = {"transcription": [
        {"offsets": {"from": 0, "to": 1000}, "text": "Hi."}
    ]}
    result = parse_whisper_json(payload)
    assert result.language == ""
    assert len(result.segments) == 1


def test_parse_skips_malformed_entries() -> None:
    payload = {
        "result": {"language": "fr"},
        "transcription": [
            {"offsets": {"from": 0, "to": 1000}, "text": "Bonjour."},
            "not a dict",
            {"offsets": {"from": "bad", "to": 2000}, "text": "Skipped."},
            {"text": "No offsets, skipped."},
            {"offsets": {"from": 2000, "to": 3000}, "text": "Au revoir."},
        ],
    }
    result = parse_whisper_json(payload)
    assert len(result.segments) == 2
    assert result.segments[0].text == "Bonjour."
    assert result.segments[1].text == "Au revoir."
