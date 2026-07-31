# Changelog

All notable changes to this derivative are documented here.

## Unreleased

## 0.2.0 - 2026-07-31

### Browser compatibility

- Ship an authenticated loopback Chrome DevTools bridge, a dedicated-profile
  launcher, and a hash-pinned dependency installer for standalone use.
- Add a Codex Chrome capture route that imports visible page evidence through a
  versioned, sanitized JSON contract.
- Add a shared local browser bridge client with configurable
  `DY_NOTE_BROWSER_ENDPOINT` / `--browser-endpoint` support.
- Keep the original port compatible while restricting bridge endpoints to
  explicit loopback HTTP origins.
- Report Codex Chrome capture and standalone local-bridge readiness separately
  in the environment check.
- Support Douyin's separate DASH audio stream so blob-backed video pages can
  still produce a local ASR transcript.
- Resolve Douyin short links from an already-open target before comment
  collection.

### Security

- Authenticate local bridge calls with a random 256-bit token, bind to loopback,
  reject browser origins/unexpected hosts, restrict navigation, and refuse
  automatic attachment to an occupied Chrome DevTools port.
- Reject browser captures containing credentials, browser storage, request
  headers/signatures, or signed media URLs.
- Keep observed Douyin request signatures inside the browser context.
- Recursively remove temporary signed URLs and sensitive metadata before write.
- Neutralize spreadsheet formulas in exported comment CSV files while retaining
  the original values in JSON.
- Restrict source and media downloads to allowlisted HTTPS domains and validate
  every redirect before following it.

### Reliability

- Bind reusable output directories to a Douyin source or local input SHA-256.
- Refuse cross-source reuse even when `--force` is supplied.
- Downgrade page-limited and reply-incomplete comment captures to samples.
- Record explicit completeness and termination reasons in comment coverage.

### Quality

- Add regression tests for source identity, metadata filtering, CSV safety, and
  comment completeness.
- Add a GitHub Actions matrix for Python 3.10/3.13 with pinned Ruff and Bandit
  checks, compilation, and self-tests.
