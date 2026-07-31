# Contributing to DyNote Safe

Thanks for helping improve DyNote Safe. Use the narrowest channel that fits the
feedback so useful reports remain easy to find.

## Choose a channel

| Feedback | Channel |
| --- | --- |
| Installation, environment, or usage question | [Q&A](https://github.com/riverfarer/dy-note-safe/discussions/categories/q-a) |
| Early feature idea or new use case | [Ideas](https://github.com/riverfarer/dy-note-safe/discussions/categories/ideas) |
| Reproducible software defect | [Bug Report](https://github.com/riverfarer/dy-note-safe/issues/new?template=bug-report.yml) |
| Security vulnerability | Follow [SECURITY.md](SECURITY.md) |

Open-ended ideas should start as Discussions. A maintainer can convert a
validated, scoped work item into an Issue.

## Before reporting a bug

1. Check the latest release and search existing Issues and Discussions.
2. Run `python scripts/check_environment.py`.
3. Reduce the problem to the smallest repeatable command or request.
4. Remove credentials, browser data, signatures, temporary media URLs, personal
   data, and unrelated private paths from all logs.

## Pull requests

Keep changes focused and explain the user impact. Run the same core checks as
CI before requesting review:

```text
python -m compileall -q scripts
python scripts/selftest.py
ruff check --select E9,F63,F7,F82 scripts
bandit -q -r scripts -ll
```

Do not commit generated media, `dy_note_*` outputs, browser profiles, bridge
tokens, cookies, storage state, or signed URLs.

