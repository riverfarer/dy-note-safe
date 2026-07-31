# DyNote Browser Modes

DyNote supports two browser routes. They share the same security boundary but
serve different runtimes.

## Mode A: Codex Chrome capture

Use this mode when DyNote runs as a Codex Skill and `chrome:control-chrome` is
available.

1. Read and follow the installed Chrome control Skill.
2. Reuse the user's current Chrome only when the task needs its logged-in state.
3. Read visible page state, playback state, Douyin AI chapters, and a bounded
   visible comment sample.
4. Never read cookies, localStorage, browser profiles, tokens, request headers,
   request signatures, or signed media URLs.
5. Save a sanitized JSON capture using the schema below.
6. Import it with `scripts/import_browser_capture.py`.

This mode does not promise full media download or hidden API pagination. If the
browser capability cannot safely produce a local media file, mark ASR and full
comment collection as blocked instead of exporting credentials or signed URLs.

### Capture schema

```json
{
  "schema": "dy-note-browser-capture-v1",
  "capture_method": "codex-chrome",
  "captured_at": "2026-07-31T00:00:00Z",
  "source": {
    "share_url": "https://v.douyin.com/example/",
    "canonical_url": "https://www.douyin.com/video/7654321098765432",
    "aweme_id": "7654321098765432",
    "title": "Visible page title",
    "author": "Visible author",
    "duration_seconds": 189.6
  },
  "login_observed": true,
  "video_playback_observed": true,
  "video_ready_state": 4,
  "evidence_grade": "visible-page-observation",
  "douyin_ai": {
    "status": "ok",
    "evidence_level": "douyin-web-ai-chapters",
    "summary": "Visible Douyin AI summary",
    "timeline": [
      {
        "time": "00:01",
        "title": "Opening",
        "desc": "Visible chapter description"
      }
    ],
    "limitations": [
      "This is not a complete transcript."
    ]
  },
  "comments": {
    "capture_kind": "visible-page-sample",
    "comments": [
      {
        "level": "main",
        "cid": "visible-or-empty",
        "nickname": "Visible nickname",
        "text": "Visible comment text"
      }
    ],
    "coverage": {
      "termination_reasons": [
        "visible-page-only"
      ]
    }
  },
  "limitations": [
    "No local media file was produced."
  ]
}
```

Import it:

```powershell
python scripts/import_browser_capture.py `
  ".\browser_capture.json" `
  --out-dir ".\dy_note_output"
```

The importer rejects credential/signature fields, signed media URLs,
non-Douyin source URLs, mismatched work IDs, and unsupported schemas. It writes
`page_metadata.json`, optional Douyin AI/comment assets, and
`assets/asset_manifest.json`.

## Mode B: local browser bridge

Use this mode for standalone CLI automation. DyNote ships an authenticated
bridge exposing the legacy-compatible `/targets`, `/new`, `/eval`, `/clickAt`,
and `/close` operations needed by the selected script.

Install its single pinned dependency, then launch it:

```powershell
python scripts/setup_browser_bridge_env.py
python scripts/start_browser_bridge.py
```

The launcher opens Chrome with a non-default profile under the shared DyNote
cache and a loopback-only DevTools port. Log into Douyin or Doubao in that
dedicated window when needed. Keep the launcher process running while using the
CLI.

The endpoint is configurable:

```powershell
$env:DY_NOTE_BROWSER_ENDPOINT = "http://127.0.0.1:3456"
python scripts/check_environment.py
```

Or pass it per command:

```powershell
python scripts/extract_douyin_text.py `
  "https://v.douyin.com/example/" `
  --browser-endpoint "http://127.0.0.1:3456" `
  --out-dir ".\dy_note_output"
```

Only loopback HTTP origins with an explicit port are accepted:

- `http://127.0.0.1:<port>`
- `http://localhost:<port>`
- `http://[::1]:<port>`

Remote hosts, HTTPS origins, credentials in the endpoint, paths, query strings,
and fragments are rejected. The default remains
`http://127.0.0.1:3456`, which is compatible with the original route.

Use a dedicated browser profile for standalone automation. Do not attach an
untrusted bridge to a daily browser profile, and do not export cookies or
storage state to make automation easier.

The bundled bridge adds these controls:

- binds the HTTP service and Chrome DevTools to loopback;
- authenticates every request with a random 256-bit token stored in a
  user-only cache file;
- rejects browser-originated requests and unexpected `Host` headers;
- permits navigation only to allowlisted Douyin/Doubao HTTPS hosts;
- rejects JavaScript that requests Cookie or browser credential storage;
- limits request, selector, target-ID, and navigation inputs;
- refuses to auto-attach when the requested DevTools port is already occupied.

The generic evaluation endpoint is powerful and is intended only for trusted
DyNote processes running as the same local user. Stop the bridge after use.
Never publish its token or profile directory.

Chrome 136 and later ignore remote-debugging flags on the default data
directory; the dedicated `--user-data-dir` is therefore required as well as
safer. See Chrome's
[remote debugging security change](https://developer.chrome.com/blog/remote-debugging-port)
and the official
[DevTools Protocol HTTP endpoints](https://chromedevtools.github.io/devtools-protocol/).

## Evidence and fallback rules

- Visible-page capture is suitable for quick understanding, AI chapters,
  playback verification, and bounded comment samples.
- Full transcript, quoteable wording, and publishable notes still require a
  local media/subtitle file and ASR when no independent subtitle exists.
- Full visible-comment collection requires a supported local browser bridge.
- When a route is unavailable, record `blocked` plus the reason. Never silently
  upgrade a visible-page sample to complete coverage.
