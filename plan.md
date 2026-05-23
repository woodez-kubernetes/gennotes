# Hyper-Secure Local Transcription & Meeting Summarizer — Staged Plan

## Product summary

A Python/Django desktop utility that runs as a localhost web app. The user
uploads an audio/video file; the app transcribes it with **whisper.cpp**, then
summarizes the transcript with a bundled **llama.cpp** quantized model. No
network calls. No telemetry. All data stays on the user's machine.

## Guiding principles

1. **Offline by default, offline by construction.** The app binds to
   `127.0.0.1` only. Outbound network access is blocked in code (no
   `requests`/`httpx` import in runtime paths; egress test in CI).
2. **No moving parts the user can't see.** All artifacts (audio, transcripts,
   summaries, DB, models) live under one user-visible data directory.
3. **Lean v1.** Upload + transcribe + summarize. Nothing else. Diarization,
   encryption, tagging, live mic, and system-audio loopback are explicitly
   deferred to later stages.
4. **Local-first toolchain.** Python venv, SQLite, file-system storage. No
   Redis/Postgres dependency in v1.

## Target stack

| Layer            | Choice                                           |
|------------------|--------------------------------------------------|
| Web framework    | Django 5.x                                       |
| DB               | SQLite (single-user, single-machine)             |
| Async work       | `django-q2` *or* a simple threaded worker        |
| Audio preprocess | `ffmpeg` (system binary, called via `subprocess`)|
| STT              | `whisper.cpp` (CLI, called via `subprocess`)     |
| Summarization    | `llama.cpp` (CLI + bundled GGUF quantized model) |
| Frontend         | Django templates + HTMX (no SPA build step)      |
| Packaging        | `pip install -e .` for dev; PyInstaller later    |

---

## Stage 0 — Foundation & scaffolding

**Goal:** A runnable Django project with the data model, settings, and tooling
for an offline-only app. No transcription yet.

**Deliverables**

- `gennotes/` Django project with one app: `recordings`.
- `pyproject.toml` (or `requirements.txt`) with pinned versions; Python 3.12.
- `settings.py`:
  - `DEBUG=False` by default; dev uses `settings_dev.py`.
  - `ALLOWED_HOSTS=["127.0.0.1", "localhost"]`.
  - `SECURE_*` headers tuned for localhost.
  - Data directory configurable via env var `GENNOTES_DATA_DIR`
    (default `~/.gennotes/`), containing `media/`, `models/`, `db.sqlite3`.
- Models (initial):
  - `Recording`: original filename, stored path, sha256, duration, created_at,
    state (`uploaded|transcribing|transcribed|summarizing|done|failed`),
    error_message.
  - `Transcript`: FK Recording, text, language, engine, model_name, created_at.
  - `Summary`: FK Recording, text, model_name, prompt_version, created_at.
- Management command: `gennotes doctor` — checks for `ffmpeg`, `whisper.cpp`
  binary, `llama.cpp` binary, and the expected GGUF model file; prints a clear
  pass/fail report.
- README with one-paragraph install path and "what this is/isn't."
- CI: `ruff`, `mypy --strict` on `recordings/`, `pytest` smoke test.

**Exit criteria**

- `python manage.py runserver 127.0.0.1:8765` serves a single page that says
  "Ready" if `doctor` passes, otherwise lists missing dependencies.

---

## Stage 1 — Upload + transcription (MVP slice)

**Goal:** Upload an audio/video file and get a transcript back. Summarization
is **not** in this stage.

**Deliverables**

- Upload view: drag-drop or file picker. Server-side validation:
  - Extension allowlist: `wav, mp3, m4a, flac, ogg, mp4, mov, mkv, webm`.
  - Max size configurable (default 2 GB).
  - Reject anything `ffprobe` can't parse.
- File ingest pipeline:
  1. Stream upload to a temp file under `media/uploads/`.
  2. Compute sha256; dedupe against existing `Recording.sha256`.
  3. `ffmpeg` normalize → 16 kHz mono 16-bit PCM WAV under `media/wav/`.
  4. Persist `Recording` row, queue transcription job.
- Worker:
  - Start with **inline threading** (`concurrent.futures.ThreadPoolExecutor`,
    size=1) gated by a process lock — avoids dragging in `django-q2`/Redis
    on day one. Promote to `django-q2` only if a second concurrent job is
    needed.
  - One job: `transcribe(recording_id)`.
- `whisper.cpp` wrapper:
  - `recordings/stt/whispercpp.py` — single function
    `transcribe(wav_path, model_path, language=None) -> TranscriptResult`.
  - Calls the `whisper-cli` binary; parses JSON output (segments + full text).
  - Model path resolved from `GENNOTES_DATA_DIR/models/<name>.bin`.
  - Surfaces stderr in `Recording.error_message` on non-zero exit.
- UI:
  - Recordings list page (table, newest first, status badge).
  - Recording detail page: metadata + transcript text with segment timecodes.
  - HTMX polling on status until `transcribed` or `failed`.
- Tests:
  - Unit: ffmpeg-normalize helper, sha256 dedupe, whisper.cpp output parser
    (golden-file fixture, no real binary call in CI).
  - Integration (opt-in, skipped in CI): run end-to-end on a 30-second sample
    when `GENNOTES_E2E=1` and binaries are present.

**Exit criteria**

- Upload a 5-minute MP3 from the browser, see the transcript on the detail
  page within a reasonable time. No internet during the run (verify with
  `Little Snitch` / `nettop` / `tcpdump`).

---

## Stage 2 — Summarization

**Goal:** Each transcript gets a generated summary. Pipeline chains
transcription → summarization automatically.

**Deliverables**

- `recordings/summarize/llamacpp.py`:
  - `summarize(transcript_text, model_path, prompt_version) -> SummaryResult`.
  - Calls `llama-cli` binary in non-interactive mode with a fixed seed and
    deterministic decoding params (temperature 0.2, top_p 0.9). Captures
    stdout, strips role tokens, returns text + tokens used.
- Prompt assets:
  - `recordings/summarize/prompts/v1.txt` — system prompt that asks for
    "Decisions, Action Items, Open Questions, Brief Recap" in that order.
  - `prompt_version` recorded on each `Summary` row so we can re-summarize
    later under a newer prompt without losing history.
- Chunking strategy:
  - If transcript token-count > model context − headroom, split on segment
    boundaries with overlap; summarize each chunk; final pass produces a
    single combined summary (map-reduce).
  - Token count via `llama-cli --tokenize` to avoid pulling in a tokenizer
    Python package.
- Job chaining: after `transcribe()` completes successfully, enqueue
  `summarize(recording_id)`. State machine moves
  `transcribed → summarizing → done`.
- Model bundling:
  - **Do not** check the GGUF into git. Document a one-shot
    `gennotes fetch-models` command that copies a model from a user-provided
    path into `GENNOTES_DATA_DIR/models/`. (Optional later: a signed
    download from a chosen mirror — but explicitly off by default to
    preserve the "no network" promise.)
  - Default model targeted for v1: a 7B Q4_K_M class GGUF (~4 GB) — small
    enough to ship/install, good enough for meeting summaries.
- UI:
  - Recording detail page gains a "Summary" section above the transcript.
  - "Re-summarize" button (uses current `prompt_version`).
- Tests:
  - Map-reduce chunking logic unit tests with mocked token counts.
  - Golden-file test for the prompt builder (so prompt edits are visible in
    diffs).
  - E2E (opt-in) covering full upload → transcript → summary.

**Exit criteria**

- For a 30-minute meeting WAV, the app produces a transcript and a structured
  summary, with no external network traffic observed during the run.

---

## Stage 3 — Hardening for "hyper-secure" v1 ship

**Goal:** The product can credibly be called "hyper-secure local."

**Deliverables**

- Network lockdown:
  - Bind only to `127.0.0.1`.
  - A startup self-test that attempts an outbound TCP connect to a sentinel
    host; if it succeeds, log a warning to the UI (the app itself never
    initiates outbound traffic, but this surfaces the user's environment).
  - Runtime check that fails fast if `DJANGO_ALLOWED_HOSTS` includes a
    non-loopback value (unless `GENNOTES_ALLOW_LAN=1` is explicitly set).
  - CSP: `default-src 'self'`; no external fonts/CDNs anywhere in templates.
- Local auth:
  - Single-user passphrase set on first run; stored as Argon2 hash.
  - Session cookie `Secure=False` (localhost), `HttpOnly=True`, `SameSite=Strict`.
  - Idle session timeout (default 30 min).
- Data hygiene:
  - "Delete recording" wipes audio, normalized WAV, transcript, and summary;
    `VACUUM` SQLite on demand.
  - "Export" produces a single `.zip` with transcript + summary as Markdown
    and the original audio.
  - "Wipe all" command.
- Audit log: an append-only `events.log` (jsonl) of state transitions and
  destructive actions. Viewable in-app, redactable.
- Threat-model doc: `docs/SECURITY.md` — what we defend against (curious
  process on the same machine getting transcripts; an analyst losing the
  laptop), what we don't (root-level malware, hardware keyloggers,
  cold-boot). Honest scope.
- Packaging:
  - PyInstaller one-folder build that bundles Python, Django, the two CLI
    binaries (downloaded into the build directory), and a launcher that
    `runserver`s on a random localhost port and opens the default browser.
  - Notarization/signing — call out as needed per OS but treat as a separate
    workstream after Stage 3.

**Exit criteria**

- A non-developer can install the package, set a passphrase, upload a file,
  and read a summary. Network monitoring confirms zero outbound traffic at
  any point.

---

## Stage 4+ — Deferred (not in v1, planned)

Listed in rough priority. Each is its own stage when picked up.

1. **Encryption at rest.** SQLCipher for the DB; per-recording AES-GCM file
   encryption keyed off the user passphrase via Argon2id.
2. **Speaker diarization.** Pluggable; first attempt via `whisper.cpp`
   diarization flags, fallback to a local `pyannote` pipeline if the user
   opts in (heavy install).
3. **Tagging + search.** User tags on recordings; SQLite FTS5 full-text
   search across transcripts and summaries.
4. **Live microphone recording.** In-browser recording (MediaRecorder) →
   chunked upload → streaming transcription with whisper.cpp `--step`
   mode.
5. **System audio loopback.** OS-specific — BlackHole on macOS, WASAPI loopback
   on Windows, PulseAudio monitor on Linux. Document, don't auto-install.
6. **Multi-user / LAN mode.** Opt-in via `GENNOTES_ALLOW_LAN=1` with proper
   auth and per-user data isolation.
7. **Model manager UI.** Switch summarization models, swap whisper sizes,
   view RAM/VRAM requirements.

## Open decisions to revisit before Stage 1 starts

- **Worker library choice.** Stage 1 starts with a single-threaded executor;
  promote to `django-q2` only when a second job type or true parallelism is
  needed. Avoid Celery (Redis dependency conflicts with the "no moving
  parts" goal).
- **Default whisper.cpp model.** `ggml-base.en` for English-only quick wins,
  `ggml-small` for multilingual. Pick one for the bundled default; the
  other is documented as a `gennotes fetch-models` add-on.
- **Bundled GGUF for summarization.** Target ~4 GB Q4_K_M 7B-class model;
  exact choice (Llama-3.1-8B-Instruct vs Mistral-7B-Instruct-v0.3 vs Qwen2.5-7B)
  decided after a short quality bake-off on real meeting transcripts.
