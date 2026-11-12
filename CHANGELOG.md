# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-13

### Added

- The `storenet site create | update | list` commands and `storenet --version`.
- The `StorenetClient` library with `sites.create`, `sites.update` and `sites.list`.
- HMAC-SHA256 request signing as the Partner API requires.
- Retries of temporary errors with the same `Idempotency-Key`, and the `not_deployed` error code
  for shops whose server does not support an operation yet.
