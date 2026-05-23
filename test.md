# gennotes — End-to-End Test Runbook

A step-by-step manual test of every feature shipped through Stage 3.
Allow ~30 minutes for the full pass. The runbook assumes macOS; Linux
should work identically if you adapt the `brew` and `tcpdump` lines.

## Conventions

- ✅ = expected outcome you should see
- 💡 = optional / nice-to-have
- ❌ = something that should be **rejected** (and how to confirm)
- All shell commands assume `cd ~/projects/coding-repos/gennotes` and
  `source .venv/bin/activate` before running them.

## What you'll need

| Component        | Required for          | Install                                                                        |
|------------------|-----------------------|---------------------------------------------------------------------------------|
| Python 3.12+     | Everything            | already in `.venv`                                                              |
| ffmpeg + ffprobe | Upload + normalize    | `brew install ffmpeg`                                                            |
| whisper.cpp CLI  | Transcription tests   | `brew install whisper-cpp`                                                       |
| Whisper model    | Transcription tests   | `curl -L -o /tmp/ggml-small.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin` |
| llama.cpp CLI    | Summarization tests   | `brew install llama.cpp`                                                         |
| GGUF model       | Summarization tests   | download a 7B-class Q4_K_M from Hugging Face (~4 GB) — e.g. `bartowski/Mistral-7B-Instruct-v0.3-GGUF` |
| sample audio     | Upload tests          | any `.mp3` / `.wav` you have, or generate one (see Test 4)                       |

Without whisper.cpp + llama.cpp installed, you can still run tests 1, 2, 3,
4 (upload), 7, 8, 9, 10, 11, 12, 13. Tests 4 (transcribe), 5, 6 require
the binaries + models.

---

## Test 0 — Clean slate

**Goal:** start from a known-empty state.

```bash
source .venv/bin/activate
python manage.py wipe_all --yes        # removes recordings + audit log
python manage.py migrate               # ensure DB schema is current

# Also delete the local user so the /setup/ flow fires:
python manage.py shell -c "from django.contrib.auth.models import User; User.objects.all().delete()"

# And clear the secret key + sessions to start fresh:
rm -f ~/.gennotes/secret_key
```

✅ `wipe_all complete.`
✅ `migrate` prints `No migrations to apply.`

---

## Test 1 — System check (doctor)

**Goal:** the doctor command lists every dependency and reports honestly.

```bash
python manage.py doctor
echo "exit=$?"
```

✅ Output lists eight rows: `data directory`, `ffmpeg`, `ffprobe`,
`whisper.cpp CLI`, `llama.cpp CLI`, `whisper model`, `llama GGUF model`,
`prompt templates`.
✅ Bottom line is `READY` (exit 0) if everything is installed,
or `NOT READY` (exit 1) if anything is missing.
✅ Missing rows print a clear "looked for X" hint with the env var name.

---

## Test 2 — Start the hardened server

**Goal:** confirm the production-ish server boots and binds to loopback.

```bash
python manage.py collectstatic --noinput   # one-time setup
python manage.py serve 127.0.0.1:8765
```

✅ Prints `gennotes serving on http://127.0.0.1:8765/`.
✅ No `Connection refused` from the next step.

In another terminal:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
```

✅ `302` (redirects to `/setup/` because no user exists yet).

❌ Now try a non-loopback bind. In a fresh terminal:

```bash
python manage.py serve 0.0.0.0:9000
```

✅ Errors out with `CommandError: refusing to bind '0.0.0.0': not on the
loopback allowlist. Set GENNOTES_ALLOW_LAN=1 to override.`

---

## Test 3 — First-run setup + login + logout

**Goal:** Argon2 passphrase flow, default-deny routing, session handling.

In a browser at <http://127.0.0.1:8765/>:

1. **Redirect to setup.** You land at `/setup/`.
   ✅ Title is "Set a passphrase".
   ✅ Page mentions Argon2 and that the passphrase never leaves the machine.

2. **Try too-short passphrase.** Enter `short` twice, submit.
   ✅ Red error: "Passphrase must be at least 12 characters."
   ✅ No user created yet (the form is still there).

3. **Try mismatched confirm.** Enter `supersecure-passphrase` and
   `different-passphrase`, submit.
   ✅ Red error: "Passphrases do not match."

4. **Submit valid passphrase.** Use `supersecure-passphrase` for both.
   ✅ Redirected to `/` (the recordings list).
   ✅ Header shows nav: "Recordings", "Audit", "System", "Sign out".

5. **Sign out, then back in.** Click "Sign out".
   ✅ Redirected to `/login/`.
   ✅ Try the wrong passphrase first → red "Incorrect passphrase."
   ✅ Then the correct passphrase → back at `/`.

6. **Confirm default-deny.** Open a private/incognito window and visit
   `http://127.0.0.1:8765/r/1/`.
   ✅ Redirected to `/login/?next=/r/1/`.

💡 Optional: sign in via the private window with the correct passphrase
and confirm `next` returns you to `/r/1/` (which will 404 — that's fine,
we just want to confirm the redirect parameter was honored without
opening a redirect to another origin).

---

## Test 4 — Upload an audio file

**Goal:** verify the ingest pipeline: streaming upload, sha256 dedupe,
ffprobe validation, ffmpeg normalize, Recording row creation.

If you don't have a handy audio file, generate one:

```bash
python -c "
import wave
with wave.open('/tmp/test-meeting.wav','wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    # 3 seconds of silence
    w.writeframes(b'\\x00\\x00' * 16000 * 3)
"
ls -la /tmp/test-meeting.wav
```

In the browser at `/`:

1. **Upload the file.** Click "Choose File", pick `/tmp/test-meeting.wav`,
   click "Upload & transcribe".
   ✅ Redirected to `/r/1/`.
   ✅ Detail page shows the filename, duration ≈ 3.0 s, size, and
      SHA-256.
   ✅ Status badge: `Uploaded` → `Transcribing` (if whisper.cpp is
      installed) or `Failed` (if not).

2. **❌ Try a disallowed extension.** Back at `/`, pick any `.txt` or
   `.pdf` you have and submit.
   ✅ Red error banner: "Extension not allowed. Permitted: ...".
   ✅ No new recording created.

3. **❌ Try unparseable audio.** Create a fake mp3:
   ```bash
   echo "not audio" > /tmp/fake.mp3
   ```
   Upload it.
   ✅ Red error: "File could not be parsed as audio/video."

4. **Dedupe.** Upload `/tmp/test-meeting.wav` again.
   ✅ Yellow info banner: "Already uploaded — redirected to existing
      recording."
   ✅ You're back at `/r/1/` (not a new id).

---

## Test 5 — Transcription + summarization (requires binaries)

**Goal:** end-to-end whisper.cpp → llama.cpp pipeline.

**Prereq:** doctor reports OK for whisper.cpp + model AND llama.cpp + GGUF.

If you used the silent WAV from Test 4, the transcript will be empty —
upload an actual speech file (a podcast clip, recorded voice memo, or
something from your existing recordings).

1. Upload a speech audio file.
2. Watch the status badge on the detail page. It auto-refreshes every
   2 seconds.
   ✅ `Uploaded` → `Transcribing` → `Transcribed` → `Summarizing` → `Done`.
3. After `Done` the page reloads and you see:
   ✅ A "Summary" section with four headings: Decisions, Action Items,
      Open Questions, Brief Recap.
   ✅ Below it, a "Transcript" section with timecodes like `[0.0-2.5]`.

**Re-summarize.** Click the "Re-summarize" button.
   ✅ Status goes `Done` → `Summarizing` → `Done` again.
   ✅ The summary refreshes; the old summary row stays in the DB (we
      keep history; verify via the audit log in Test 8 — you'll see two
      `summarized` events).

**Retry on failure.** To test retry, temporarily unset the model:
```bash
mv ~/.gennotes/models/*.gguf /tmp/gguf-stash/    # adjust the glob
```
Then click "Re-summarize" — it'll fail. ✅ Status `Failed` with the
error message visible. Restore the model:
```bash
mv /tmp/gguf-stash/*.gguf ~/.gennotes/models/
```
Click "Retry". ✅ It picks up from summarization (not re-transcribe)
because the Transcript row already exists.

---

## Test 6 — Export to zip

**Goal:** export produces a portable archive with transcript + summary
+ original audio.

On the detail page of a `Done` recording, click **"Export .zip"**.

✅ Downloads a file like `gennotes-test-meeting-<sha8>.zip`.
✅ Extract it:
```bash
cd /tmp && unzip -l gennotes-*.zip
```
You should see:
```
original.wav
transcript.md
summary.md
manifest.txt
```
✅ `cat /tmp/transcript.md` shows the timecoded transcript in Markdown.
✅ `cat /tmp/summary.md` shows the four-section summary.
✅ `cat /tmp/manifest.txt` lists the recording id, sha256, original
   filename, created_at, duration_seconds.

---

## Test 7 — Delete recording

**Goal:** delete wipes the DB row, transcript, summary, and files on
disk.

1. On a recording detail page, note the sha256.
2. Confirm the files exist:
   ```bash
   ls ~/.gennotes/media/uploads/<sha>.* ~/.gennotes/media/wav/<sha>.wav
   ```
3. Click **"Delete"** in the actions row.
   ✅ Browser prompts: "Delete this recording, transcript, and summary
      permanently?".
4. Confirm. ✅ Redirected to `/` with green banner: "Recording deleted."
5. Re-run the `ls` from step 2.
   ✅ Both files are gone.
6. ✅ The audit log (Test 8) shows a `recording_deleted` event.

---

## Test 8 — Audit log

**Goal:** every significant action leaves a JSONL record.

In the nav, click **"Audit"**.

✅ Table of the last 500 events, newest first.
✅ You should see (depending on what you've done):
   `audit_truncated`, `wipe_all`, `setup_complete`, `login_success`,
   `login_failure` (if you tested wrong passphrase), `logout`,
   `recording_uploaded`, `transcribed`, `summarized`,
   `summarization_failed` (if you tested it), `recording_deleted`,
   `export_created`.
✅ Each row has `ts`, `event`, and key=value fields.

**On-disk format.** The audit log is JSONL — one self-contained JSON
object per line. To inspect:
```bash
# Raw (one event per line, already human-readable):
tail -10 ~/.gennotes/logs/events.log

# Pretty-printed, no jq required:
tail -10 ~/.gennotes/logs/events.log | \
  python -c "import json,sys;[print(json.dumps(json.loads(l),indent=2)) for l in sys.stdin if l.strip()]"
```
✅ Each line is a single JSON object. `ts` is ISO 8601 UTC.
✅ Do **not** pipe the whole file through `python -m json.tool` — it
   expects one document, not many, and will error on line 2.

**Truncate.** Click **"Clear audit log"** at the bottom.
✅ Confirm dialog: "Erase the audit log? This cannot be undone."
✅ After confirming, page reloads with just a single `audit_truncated`
   event (logged after truncation).

---

## Test 9 — Security headers

**Goal:** CSP, X-Frame-Options, Permissions-Policy are emitted.

In a shell:

```bash
curl -sI http://127.0.0.1:8765/login/ | grep -iE "(content-security|x-frame|permissions-policy|referrer-policy|x-content-type)"
```

✅ Output includes:
```
Content-Security-Policy: default-src 'self'; script-src 'self'; ... frame-ancestors 'none'; ...
X-Frame-Options: DENY
Permissions-Policy: geolocation=(), microphone=(), camera=()
Referrer-Policy: same-origin
X-Content-Type-Options: nosniff
```

❌ **Confirm no external script loads.** Open the detail page in the
browser, open DevTools → Network. Reload.
✅ Every request goes to `127.0.0.1:8765`. No requests to fonts.googleapis,
   gravatar, CDNs, telemetry endpoints — nothing else.

💡 In DevTools → Console you should also see no CSP violations
(`Refused to load the script ...` errors). If you do, file a bug.

---

## Test 10 — Loopback enforcement

**Goal:** the server refuses to bind to anything other than loopback
without an explicit opt-in.

```bash
# Should be refused:
python manage.py serve 0.0.0.0:9000
python manage.py serve 192.168.1.5:9000   # use any non-loopback IP

# Should be allowed:
python manage.py serve 127.0.0.1:9001
python manage.py serve localhost:9002

# Explicit opt-in:
GENNOTES_ALLOW_LAN=1 python manage.py serve 0.0.0.0:9003
```

✅ The first two refuse with `CommandError: refusing to bind ...`.
✅ The middle two start serving.
✅ The opt-in one starts serving (and prints the URL).

Stop each test server with Ctrl-C between attempts.

---

## Test 11 — Idle session timeout

**Goal:** sessions expire after `GENNOTES_IDLE_TIMEOUT` seconds of inactivity.

Stop your current server, then start one with a short timeout:

```bash
GENNOTES_IDLE_TIMEOUT=60 python manage.py serve 127.0.0.1:8765
```

In the browser, sign in normally, then **leave the tab alone for 65
seconds** (don't navigate or click).

After the wait, click any link in the nav.

✅ Redirected to `/login/?next=...`.
✅ Audit log (after re-login) does **not** show a `logout` event —
   timeout is silent (it's not a destructive action).

Restore the default in the next server start.

---

## Test 12 — Outbound network audit

**Goal:** confirm that gennotes itself never initiates outbound traffic.

This is the load-bearing claim of "hyper-secure local". Two ways to
verify:

### Static (no sudo)

```bash
# 1. No HTTP client libraries imported
grep -RnE "^import (requests|httpx|urllib3|aiohttp)|^from (requests|httpx|urllib3|aiohttp)" recordings/ gennotes/
# 2. No raw urllib outbound
grep -RnE "urllib\.request|urlopen|HTTPConnection" recordings/ gennotes/
# 3. No non-loopback http URLs in source
grep -RnE "https?://" recordings/ gennotes/ | grep -vE "127\.0\.0\.1|localhost"
```

✅ The first two produce no output.
✅ The third shows only the loopback URL printed by `manage.py serve`.

### Runtime (with sudo)

Start the server with `manage.py serve 127.0.0.1:8765`. In another
terminal:

```bash
# Capture anything leaving the loopback path. Filter out DNS and mDNS.
sudo tcpdump -i any -n \
  'not (host 127.0.0.1 or host ::1) and port not 53 and not (host 224.0.0.0/4)'
```

While `tcpdump` runs, exercise the app: upload a file, transcribe,
summarize, export, delete.

✅ `tcpdump` records **no packets** originating from the gennotes
Python process. (Other processes on your machine may show up — that's
fine. Use `lsof -p <pid>` if you want to be sure.)

---

## Test 13 — wipe_all

**Goal:** the wipe command leaves the data directory empty.

Make sure you have at least one recording and a few audit events. Then:

```bash
python manage.py wipe_all
# When prompted, type: wipe
```

✅ Output:
```
deleted N DB row(s).
cleared media tree under /Users/.../.gennotes/media.
audit log cleared.
vacuumed sqlite.
wipe_all complete.
```

Verify:

```bash
ls ~/.gennotes/media/uploads/ ~/.gennotes/media/wav/ 2>/dev/null
python manage.py shell -c "from recordings.models import Recording; print(Recording.objects.count())"
wc -l ~/.gennotes/logs/events.log 2>/dev/null
```

✅ Both media subdirs are empty.
✅ Recording count: 0.
✅ Audit log: either missing or a single line (the `wipe_all` event
   itself, which is logged after truncation).

❌ **Try cancelling the prompt.** Run `python manage.py wipe_all` again
and type anything other than `wipe`.

✅ Output: `aborted.` and no data touched.

---

## Test 14 — Tooling

**Goal:** the dev-side checks all pass on a clean tree.

```bash
ruff check .
mypy recordings/ gennotes/
pytest
```

✅ `ruff`: `All checks passed!`
✅ `mypy`: `Success: no issues found in 47 source files`
✅ `pytest`: `60 passed`

---

## Sign-off checklist

If every section above produced its ✅ outcomes, you've verified the
Stage 1, 2, and 3 deliverables end-to-end:

- [ ] Doctor reports honest status of every dep (Test 1)
- [ ] Server boots and refuses non-loopback binding (Test 2, 10)
- [ ] Auth flow: setup, login, logout, default-deny (Test 3)
- [ ] Upload pipeline: stream, hash, normalize, dedupe (Test 4)
- [ ] Transcription + summarization chain (Test 5) **if binaries installed**
- [ ] Export zip is portable and self-describing (Test 6)
- [ ] Delete wipes files + DB (Test 7)
- [ ] Audit log records every significant action (Test 8)
- [ ] CSP, X-Frame-Options, Permissions-Policy headers present (Test 9)
- [ ] Idle session timeout works (Test 11)
- [ ] No outbound HTTP code in the package (Test 12)
- [ ] wipe_all leaves the data dir empty (Test 13)
- [ ] ruff / mypy / pytest all clean (Test 14)
