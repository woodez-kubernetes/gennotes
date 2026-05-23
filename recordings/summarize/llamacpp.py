"""llama.cpp wrapper.

We shell out to ``llama-cli`` (and ``llama-tokenize`` when available) instead
of importing ``llama-cpp-python``. Why:

  * Keeps the build/install story simple — users either have llama.cpp on
    PATH or they don't.
  * Avoids dragging a heavy C extension into the Python venv.
  * Lets PyInstaller (Stage 3) bundle the binary independently of the venv.

Generation is deterministic (fixed seed, low temperature) so re-runs over the
same transcript with the same prompt version produce stable summaries.
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from gennotes.data_dir import models_dir

log = logging.getLogger(__name__)


class LlamaError(Exception):
    """llama-cli failed or its output could not be interpreted."""


@dataclass
class GenerateOptions:
    temperature: float = 0.2
    top_p: float = 0.9
    seed: int = 1234
    n_predict: int = 1024  # max new tokens for the response
    n_ctx: int = 0          # 0 = use model default (or env override)
    threads: int = 0        # 0 = let llama.cpp decide


@dataclass
class GenerateResult:
    text: str
    model_name: str
    prompt_chars: int
    output_chars: int
    extra: dict[str, str] = field(default_factory=dict)


def _resolve_completion_bin() -> tuple[str, list[str]]:
    """Pick the right llama.cpp completion binary for this install.

    Recent llama.cpp builds split the old ``llama-cli`` into:

      * ``llama-cli`` — interactive chat only.
      * ``llama-completion`` — one-shot, non-interactive completion (what
        we want).

    Older builds still ship one ``llama-cli`` that needs ``-no-cnv`` to
    behave non-interactively. We resolve at call time so the same install
    can keep working when brew upgrades the formula underneath us.

    Returns ``(binary, extra_flags)``. ``GENNOTES_LLAMA_BIN`` overrides
    auto-detection — if you point it at ``llama-cli`` we still add
    ``-no-cnv`` defensively.
    """
    explicit = os.environ.get("GENNOTES_LLAMA_BIN")
    if explicit:
        name = Path(explicit).name
        if "completion" in name:
            return explicit, []
        return explicit, ["-no-cnv"]

    completion = shutil.which("llama-completion")
    if completion:
        return completion, []
    cli = shutil.which("llama-cli")
    if cli:
        return cli, ["-no-cnv"]
    # Neither found — fall through to a default that will raise a clear
    # error inside :func:`generate`.
    return "llama-cli", ["-no-cnv"]


def _llama_bin() -> str:
    """Backwards-compatible helper — returns just the binary name/path."""
    return _resolve_completion_bin()[0]


def _tokenize_bin() -> str | None:
    """Resolve the standalone ``llama-tokenize`` binary, if available.

    Falls back to None — callers should then use :func:`heuristic_token_count`.
    """
    explicit = os.environ.get("GENNOTES_LLAMA_TOKENIZE_BIN")
    if explicit:
        return explicit
    return shutil.which("llama-tokenize")


def resolve_model_path() -> Path:
    """Locate the GGUF model file to use for summarization.

    Resolution order:
      1. ``$GENNOTES_LLAMA_MODEL`` (absolute path OR basename under models/).
      2. First ``*.gguf`` under models/ in alphabetical order.

    Raises FileNotFoundError if nothing matches.
    """
    explicit = os.environ.get("GENNOTES_LLAMA_MODEL")
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = models_dir() / explicit
        if not p.exists():
            raise FileNotFoundError(f"GENNOTES_LLAMA_MODEL points to missing file: {p}")
        return p
    candidates = sorted(models_dir().glob("*.gguf"))
    if not candidates:
        raise FileNotFoundError(
            f"no llama GGUF model (*.gguf) found under {models_dir()}"
        )
    return candidates[0]


def heuristic_token_count(text: str) -> int:
    """Cheap, no-binary token-count estimate.

    English/most-Latin transcripts average ~4 characters per token under
    Llama-3-family BPE tokenizers. We use 3.5 to err toward over-estimating
    (smaller chunks, safer context budget).
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3.5))


def tokenize_count(text: str, model_path: Path) -> int:
    """Exact token count via ``llama-tokenize``. Falls back to heuristic.

    Never raises — the count is advisory for chunking. Returns the heuristic
    on any failure.
    """
    binary = _tokenize_bin()
    if binary is None:
        return heuristic_token_count(text)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="gennotes-tok-"
    ) as fh:
        fh.write(text)
        input_path = Path(fh.name)
    try:
        result = subprocess.run(
            [binary, "-m", str(model_path), "-f", str(input_path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return heuristic_token_count(text)
    finally:
        input_path.unlink(missing_ok=True)
    if result.returncode != 0:
        return heuristic_token_count(text)
    # llama-tokenize prints one token id per line (newer versions) or a
    # space-separated list (older). Count whitespace-delimited fields.
    return len(result.stdout.split()) or heuristic_token_count(text)


def generate(prompt: str, model_path: Path, options: GenerateOptions) -> GenerateResult:
    """Run llama-cli once and return the completion text.

    Output is decoded as UTF-8 with replacement on bad bytes. The wrapper
    explicitly disables both interactive and conversation modes so that
    llama-cli treats the prompt as a one-shot completion and exits when
    generation finishes. ``stdin`` is redirected to /dev/null so a build
    that defaults to interactive (older llama.cpp flag names) still sees
    EOF and exits instead of hanging forever.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".prompt", delete=False, prefix="gennotes-gen-"
    ) as fh:
        fh.write(prompt)
        prompt_path = Path(fh.name)
    binary, extra_flags = _resolve_completion_bin()
    try:
        cmd = [
            binary,
            "-m", str(model_path),
            "-f", str(prompt_path),
            *extra_flags,                # -no-cnv on legacy llama-cli; empty on llama-completion
            "--no-display-prompt",
            "--simple-io",
            "-n", str(options.n_predict),
            "--temp", str(options.temperature),
            "--top-p", str(options.top_p),
            "--seed", str(options.seed),
        ]
        if options.n_ctx > 0:
            cmd += ["-c", str(options.n_ctx)]
        if options.threads > 0:
            cmd += ["-t", str(options.threads)]
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                timeout=60 * 60 * 2,
            )
        except FileNotFoundError as e:
            raise LlamaError(f"llama-cli not found: {e}") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b"").decode("utf-8", errors="replace")[-2000:]
            raise LlamaError(f"llama-cli exited {e.returncode}:\n{stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise LlamaError("llama-cli timed out after 2 hours") from e
    finally:
        prompt_path.unlink(missing_ok=True)

    output = proc.stdout.decode("utf-8", errors="replace")
    output = _strip_echoed_prompt(prompt, output).strip()
    return GenerateResult(
        text=output,
        model_name=model_path.name,
        prompt_chars=len(prompt),
        output_chars=len(output),
    )


def _strip_echoed_prompt(prompt: str, output: str) -> str:
    """Defensive: some llama-cli builds echo the prompt despite the flag."""
    needle = prompt.strip()
    if not needle:
        return output
    idx = output.find(needle)
    if idx == -1:
        return output
    return output[idx + len(needle):]
