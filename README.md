# gennotes

Hyper-secure, fully-local transcription and meeting summarizer. Upload an
audio or video file, get back a transcript (via [whisper.cpp](https://github.com/ggml-org/whisper.cpp))
and a structured summary (via [llama.cpp](https://github.com/ggml-org/llama.cpp)).
No network calls. No telemetry. All data stays under one user-visible directory.

## What this is

- A Django app you run on `127.0.0.1` and open in a browser.
- Single-user, single-machine.
- Designed for sensitive recordings (interviews, internal meetings, voice
  notes) where uploading to a cloud service is not acceptable.

## What this isn't (yet)

- Not a real-time meeting bot. v1 ingests files; live mic and system-audio
  loopback are deferred — see [`plan.md`](plan.md).
- Not encrypted at rest. The DB and audio files live in plaintext under
  `~/.gennotes/` in v1. Encryption is a Stage 4 deliverable.
- Not multi-user. LAN/multi-user mode is explicitly behind a future flag.

## Install (dev)

Requires Python 3.12+, `ffmpeg`, a `whisper.cpp` build, and a `llama.cpp` build.
See `plan.md` for model recommendations.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python manage.py migrate
python manage.py doctor          # tells you what's missing
python manage.py runserver 127.0.0.1:8765
```

Open <http://127.0.0.1:8765/> — you'll see "Ready" if all checks pass, or a
list of what's missing.

## Data directory

Everything the app reads or writes lives under `~/.gennotes/` (override with
`GENNOTES_DATA_DIR`):

    ~/.gennotes/
    ├── db.sqlite3      Django DB
    ├── secret_key      per-install Django SECRET_KEY (mode 600)
    ├── media/          uploaded originals + normalized WAV
    ├── models/         ggml-*.bin (whisper) and *.gguf (llama) model files
    └── logs/           audit / state-transition logs

Delete the directory to fully reset the app.

## Tooling

```bash
ruff check .
mypy recordings/ gennotes/
pytest
```

## Roadmap

See [`plan.md`](plan.md) for the staged roadmap. We're currently in Stage 0
(foundation); Stage 1 adds upload + whisper.cpp transcription.
