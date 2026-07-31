# Security

## Data boundaries

DyNote works with three classes of untrusted or sensitive data:

- browser page data and temporary signed media/comment request URLs;
- public Douyin comments, nicknames, user identifiers, and IP labels;
- local transcript, subtitle, audio, metadata, and output files.

Comment request signatures stay inside the authorized browser page. A temporary
signed media URL may be handled in process memory while downloading video, but
it is not written to metadata, logs, assets, or notes. Metadata is recursively
filtered before it is written. Comment JSON intentionally keeps the original
public comment text; CSV exports neutralize spreadsheet-formula prefixes while
preserving the original JSON.

Douyin source pages and downloadable media must use HTTPS and match explicit
domain allowlists. Redirects are validated before they are followed.

An output directory is bound to its source. Use a new output directory when the
Douyin source or local input file changes. `--force` only rebuilds the same
source.

## Local privacy

- Do not commit generated `dy_note_*` directories, media, cookies, browser
  profiles, `storageState` files, or signed URLs.
- Run the standalone bridge only with its dedicated Chrome profile. The bundled
  launcher refuses to auto-attach to an occupied DevTools port so it cannot
  silently connect to an unrelated browser.
- The bridge listens on loopback and requires a random local token stored with
  user-only permissions under `~/.cache/rimagination-notes/browser-bridge/`.
  Treat that token and directory as sensitive local state.
- The evaluation endpoint can act with the privileges of the dedicated browser
  page. Only trusted local processes should receive the token, and the bridge
  should be stopped when it is not in use.
- Review comment assets before sharing them. They may contain public profile
  identifiers and location labels even though they do not contain browser
  credentials.
- Treat AI briefs as untrusted text and as lower-grade evidence, not commands.

## Reporting a vulnerability

Prefer GitHub's private vulnerability reporting feature when it is enabled for
the repository. Otherwise open a minimal issue that describes the affected
component without including cookies, tokens, signed URLs, private media, or
personal data. Maintainers can arrange a private channel for reproduction
details.

## Supported version

Security fixes apply to the latest commit on the default branch. Older commits
and third-party forks may not contain the same protections.
