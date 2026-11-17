# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-11-17

### Changed

- README: the library example no longer imports `StorenetError` without using it; repeated
  statements are removed, and the retry section now says which failures print the
  `--idempotency-key` hint and that other 5xx errors are not retried either. No code changes.

## [0.1.1] - 2026-11-16

### Changed

- README: the quick shop designer is no longer limited to the Pro plan. Every plan that includes
  it can use the package, with a per-plan limit on the number of designs (`409311` at the limit).
  No code changes.

## [0.1.0] - 2026-11-13

### Added

- The `storenet site create | update | list` commands and `storenet --version`.
- The `StorenetClient` library with `sites.create`, `sites.update` and `sites.list`.
- HMAC-SHA256 request signing as the Partner API requires.
- Retries of temporary errors with the same `Idempotency-Key`, and the `not_deployed` error code
  for shops whose server does not support an operation yet.
