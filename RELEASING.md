# Releasing

Notes for maintainers. Pushing a tag `vX.Y.Z` (final release) or `vX.Y.ZrcN` (release candidate)
runs `.github/workflows/release.yml`.

## Secret `STORENET_DIST_DENY`

- Set it under the repository's Settings > Secrets and variables > Actions: a comma-separated
  list of words that must never ship in a wheel or sdist (internal names).
- The words live only in the secret. Never write them in any file, commit, issue or log of this
  repository.
- The `build` job runs `tools/check_dist.py --require-deny` for **every** tag, release candidates
  included (they are published on the public TestPyPI). If the secret is empty or missing, the
  job stops with exit code 2 and nothing is published.
- `ci.yml` scans the dist without the secret, because pull requests from forks do not receive
  secrets.

Check locally before tagging:

```bash
rm -rf dist && python -m build
STORENET_DIST_DENY="<word 1>,<word 2>" python tools/check_dist.py --require-deny dist/*
```

## The `PLACEHOLDER` string

`pyproject.toml`, `LICENSE`, `SECURITY.md`, `README.md` and `CHANGELOG.md` must not contain
`PLACEHOLDER` (author, security contact) in a final release. Release-candidate tags still run;
a final tag `vX.Y.Z` stops in the `build` job if any of these files still contains
`PLACEHOLDER`. Replace every occurrence before tagging a final release.
