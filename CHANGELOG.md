# Changelog

All notable changes to this derivative are documented here.

## Unreleased

### Security

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
