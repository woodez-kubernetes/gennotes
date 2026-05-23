# gennotes — Security Model

This document describes what gennotes defends against, what it doesn't,
and why we made the trade-offs we did. The goal of the v1 release is to be
"hyper-secure local": every byte stays on the user's machine, the user
chooses when to look at it, and the attack surface is as narrow as we can
make it without sacrificing usability.

## In scope (what we defend against)

| Threat                                                                | Defense                                                                                                                  |
|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| **Network exfiltration.** Code path that "phones home" a transcript or summary. | Zero outbound HTTP/HTTPS calls in the application. No telemetry, no analytics, no CDN. Verified manually with `tcpdump` / Little Snitch. |
| **Curious local processes** reading files in your home directory.    | All transcripts, audio, models, and logs live under a single dir at file mode `0600`. WAV/uploads chmod 600 on write.    |
| **Web origin attacks** (CSRF, clickjacking, XSS via templates).      | Django CSRF middleware enabled, `X-Frame-Options: DENY`, strict CSP (`default-src 'self'`, `script-src 'self'`), `SameSite=Strict` cookies. |
| **Accidental LAN exposure.** Binding to `0.0.0.0` and serving on Wi-Fi. | `manage.py serve` refuses any host outside `127.0.0.1`/`::1`/`localhost` unless `GENNOTES_ALLOW_LAN=1` is explicitly set. |
| **Stolen laptop with an idle session.**                              | Idle session timeout (default 30 min) re-prompts for the passphrase. Logout is one click.                                |
| **Brute-force on the passphrase.**                                   | Argon2id hash (via `argon2-cffi`). Minimum length enforced at setup. Single user means no enumeration.                   |
| **Replay of a downloaded zip on a different machine** by a casual finder. | The exported zip is a clearly-labeled archive; the user controls when and where it gets shared.                       |

## Out of scope (what we don't defend against)

The list below is deliberately specific. If your threat model includes any
of these, gennotes alone is not enough.

- **Root-level malware or kernel rootkits.** A privileged adversary on the
  same machine can read process memory, the SQLite file, and the model
  binaries. Use full-disk encryption and OS-level hardening.
- **Hardware keyloggers / firmware implants.** Out of reach of any user-space
  application.
- **Cold-boot attacks** against the running process.
- **Other users on the same machine.** Files are mode 0600, but if another
  account has admin privilege or shares your home directory permissions,
  they can read everything. v1 does not encrypt at rest — see Roadmap.
- **Side channels.** Disk cache, swap, thumbnail caches built by the OS.
- **Coercion / "rubber-hose" attacks.** The passphrase model defends against
  remote attackers, not someone with physical access who can compel you.
- **Network monitoring of the user's own browsing.** Once the user types
  `127.0.0.1:8765` into their browser, the browser may surface the URL to
  history/sync/extensions. Use a browser profile you trust.

## Specific design choices

- **No JavaScript from third parties.** All scripts (`poll.js`, `confirm.js`)
  ship inside the package. CSP `script-src 'self'` blocks any other source.
- **`style-src 'unsafe-inline'` is intentionally allowed.** Stripping every
  inline `style=` attribute would force a per-template class refactor for
  marginal security benefit on a localhost app. Inline-style XSS requires
  an HTML-injection vector first, which our auto-escaping templates and
  strict `script-src` deny.
- **Single Django user account.** Avoids the complexity of user enumeration
  and password reset flows. The username is hardcoded as `local`; only the
  passphrase varies.
- **SQLite, no Redis/Postgres.** Smaller attack surface and easier
  forensics: one file on disk.
- **No outbound network test.** v1 ships without a sentinel-host probe.
  The negative space ("no outbound traffic") is what matters and is best
  verified externally with `tcpdump` or a host firewall.

## Roadmap (Stage 4+)

The following deferred items would tighten the model further:

1. **Encryption at rest** for the SQLite DB (SQLCipher) and per-recording
   AES-GCM for audio + WAV files, keyed off the passphrase via Argon2id KDF.
2. **Audit-log integrity.** Optionally chain audit entries with HMAC so
   tampering shows up on inspection.
3. **Multi-user / LAN mode** behind `GENNOTES_ALLOW_LAN=1`, with per-user
   data isolation and a proper auth backend.
4. **Reproducible build + signing** of the PyInstaller bundle so casual
   tampering with the binary is detectable.

## Verifying the network promise

To audit a single session yourself on macOS:

```bash
# In one terminal — capture anything leaving the loopback path.
sudo tcpdump -i any -n 'not (host 127.0.0.1 or host ::1)' \
    and port not 53 and not (host 224.0.0.0/4)

# In another — start gennotes and use it normally.
python manage.py serve
```

If `tcpdump` records any packet involving gennotes' Python process, that's
a regression — please open an issue with the captured event.
