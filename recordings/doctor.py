"""Environment self-checks shared by the home view and the management command.

Reports presence of:
    - ffmpeg / ffprobe binaries
    - whisper.cpp CLI (default: ``whisper-cli`` on PATH)
    - llama.cpp CLI (default: ``llama-cli`` on PATH)
    - at least one whisper model under DATA_DIR/models/
    - at least one GGUF model under DATA_DIR/models/

Any of these can be overridden via env vars:
    GENNOTES_FFMPEG, GENNOTES_FFPROBE,
    GENNOTES_WHISPER_BIN, GENNOTES_LLAMA_BIN
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from gennotes.data_dir import data_dir, models_dir

from .summarize.prompts import KNOWN_VERSIONS, PROMPTS_ROOT


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def as_text(self) -> str:
        lines = []
        for c in self.checks:
            mark = "OK  " if c.passed else "MISS"
            lines.append(f"[{mark}] {c.name}: {c.detail}")
        lines.append("")
        lines.append("READY" if self.all_passed else "NOT READY")
        return "\n".join(lines)


def _check_binary(name: str, env_var: str, default: str) -> Check:
    candidate = os.environ.get(env_var, default)
    found = shutil.which(candidate)
    if found:
        return Check(name=name, passed=True, detail=found)
    return Check(
        name=name,
        passed=False,
        detail=f"not on PATH (looked for {candidate!r}; set {env_var} to override)",
    )


def _check_llama_completion() -> Check:
    """llama.cpp has split ``llama-cli`` into ``llama-cli`` + ``llama-completion``
    on recent builds. Either is fine; we prefer the new one. The env-var
    override (``GENNOTES_LLAMA_BIN``) wins outright."""
    explicit = os.environ.get("GENNOTES_LLAMA_BIN")
    if explicit:
        found = shutil.which(explicit)
        if found:
            return Check(name="llama.cpp CLI", passed=True, detail=found)
        return Check(
            name="llama.cpp CLI",
            passed=False,
            detail=f"GENNOTES_LLAMA_BIN={explicit!r} not on PATH",
        )
    completion = shutil.which("llama-completion")
    if completion:
        return Check(name="llama.cpp CLI", passed=True, detail=f"{completion} (one-shot)")
    cli = shutil.which("llama-cli")
    if cli:
        return Check(name="llama.cpp CLI", passed=True, detail=f"{cli} (legacy, will pass -no-cnv)")
    return Check(
        name="llama.cpp CLI",
        passed=False,
        detail="neither 'llama-completion' nor 'llama-cli' on PATH "
        "(set GENNOTES_LLAMA_BIN to override)",
    )


def _check_model_glob(name: str, directory: Path, pattern: str) -> Check:
    if not directory.exists():
        return Check(
            name=name,
            passed=False,
            detail=f"models directory missing: {directory}",
        )
    matches = sorted(directory.glob(pattern))
    if matches:
        return Check(
            name=name,
            passed=True,
            detail=f"{len(matches)} found ({matches[0].name}{', …' if len(matches) > 1 else ''})",
        )
    return Check(
        name=name,
        passed=False,
        detail=f"no files matching {pattern!r} under {directory}",
    )


def _check_prompts() -> Check:
    missing: list[str] = []
    for version in KNOWN_VERSIONS:
        base = PROMPTS_ROOT / version
        for fname in ("single.txt", "map.txt", "reduce.txt"):
            if not (base / fname).exists():
                missing.append(f"{version}/{fname}")
    if missing:
        return Check(
            name="prompt templates",
            passed=False,
            detail=f"missing: {', '.join(missing)}",
        )
    return Check(
        name="prompt templates",
        passed=True,
        detail=f"versions: {', '.join(KNOWN_VERSIONS)}",
    )


def run_checks() -> Report:
    checks = [
        Check(
            name="data directory",
            passed=data_dir().exists(),
            detail=str(data_dir()),
        ),
        _check_binary("ffmpeg", "GENNOTES_FFMPEG", "ffmpeg"),
        _check_binary("ffprobe", "GENNOTES_FFPROBE", "ffprobe"),
        _check_binary("whisper.cpp CLI", "GENNOTES_WHISPER_BIN", "whisper-cli"),
        _check_llama_completion(),
        _check_model_glob("whisper model", models_dir(), "ggml-*.bin"),
        _check_model_glob("llama GGUF model", models_dir(), "*.gguf"),
        _check_prompts(),
    ]
    return Report(checks=checks)
