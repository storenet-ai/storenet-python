"""Refuse a wheel or sdist that ships more than the package, README, LICENSE and metadata."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

FORBIDDEN = (
    "localhost",
    "127.0.0.1",
    "pk_live_",
    "pk_test_",
    "sk_live_",
    "sk_test_",
    "@gmail.com",
)
# Private names are passed in at run time so this public file never has to spell them out.
DENY_ENV = "STORENET_DIST_DENY"
ALLOWED_URLS = (
    "https://storenet.ai",
    "https://github.com/storenet-ai/storenet-python",
    "https://shop.example.com",
    "https://pypi.org",
)
WHEEL_MEMBERS = (
    re.compile(r"storenet/[a-z_]+\.py"),
    re.compile(r"storenet/py\.typed"),
    re.compile(r"storenet-[^/]+\.dist-info/(METADATA|WHEEL|RECORD|entry_points\.txt)"),
    re.compile(r"storenet-[^/]+\.dist-info/licenses/LICENSE"),
)
SDIST_MEMBERS = (
    re.compile(r"storenet-[^/]+/(PKG-INFO|pyproject\.toml|README\.md|LICENSE)"),
    # hatchling always ships the VCS ignore file in an sdist; it holds patterns only.
    re.compile(r"storenet-[^/]+/\.gitignore"),
    re.compile(r"storenet-[^/]+/src/storenet/([a-z_]+\.py|py\.typed)"),
)
URL_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>()\[\]`]+", re.IGNORECASE)


def _url_allowed(url: str) -> bool:
    url = url.rstrip(".,;:")
    return any(
        url == allowed or url.startswith((allowed + "/", allowed + "?", allowed + "#"))
        for allowed in ALLOWED_URLS
    )


def scan_text(name: str, text: str, extra_denied: Iterable[str] = ()) -> list[str]:
    """Problems in one member's text: forbidden strings and URLs outside the allowlist."""
    lowered = text.lower()
    problems = [
        f"{name}: contains forbidden string {token!r}"
        for token in FORBIDDEN
        if token and token.lower() in lowered
    ]
    # Deny-list words are private: the release log is public, so name only their position.
    problems.extend(
        f"{name}: contains extra forbidden string #{index}"
        for index, token in enumerate(extra_denied, start=1)
        if token and token.lower() in lowered
    )
    problems.extend(
        f"{name}: URL outside the allowlist {url!r}"
        for url in URL_PATTERN.findall(text)
        if not _url_allowed(url)
    )
    return problems


def _check_members(
    archive: str,
    members: Iterable[tuple[str, bytes]],
    allowed: Sequence[re.Pattern[str]],
    extra_denied: Sequence[str],
) -> list[str]:
    problems: list[str] = []
    count = 0
    for name, data in members:
        count += 1
        label = f"{archive}: {name}"
        if not any(pattern.fullmatch(name) for pattern in allowed):
            problems.append(f"{label}: not in the member allowlist")
        problems.extend(scan_text(label, data.decode("utf-8", "replace"), extra_denied))
    if count == 0:
        problems.append(f"{archive}: empty")
    return problems


def _wheel_members(path: Path) -> Iterable[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as wheel:
        for info in wheel.infolist():
            if not info.is_dir():
                yield info.filename, wheel.read(info)


def _sdist_members(path: Path, problems: list[str]) -> Iterable[tuple[str, bytes]]:
    with tarfile.open(path, "r:gz") as sdist:
        for info in sdist.getmembers():
            if info.isdir():
                continue
            handle = sdist.extractfile(info) if info.isfile() else None
            if handle is None:
                problems.append(f"{path.name}: {info.name}: not a regular file")
                continue
            yield info.name, handle.read()


def check_wheel(path: Path, extra_denied: Sequence[str] = ()) -> list[str]:
    """Problems found in one wheel."""
    return _check_members(path.name, _wheel_members(path), WHEEL_MEMBERS, extra_denied)


def check_sdist(path: Path, extra_denied: Sequence[str] = ()) -> list[str]:
    """Problems found in one sdist."""
    problems: list[str] = []
    found = _check_members(
        path.name, list(_sdist_members(path, problems)), SDIST_MEMBERS, extra_denied
    )
    return problems + found


def main(argv: Sequence[str] | None = None) -> int:
    """Check every given archive; exit 1 on any problem, 2 on bad input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--deny", action="append", default=[], help="add a forbidden string")
    parser.add_argument(
        "--require-deny",
        action="store_true",
        help=f"exit 2 when both --deny and {DENY_ENV} are empty",
    )
    args = parser.parse_args(argv)
    extra = [*args.deny, *os.environ.get(DENY_ENV, "").split(",")]
    extra = [word.strip() for word in extra if word.strip()]
    if args.require_deny and not extra:
        print(
            f"The extra forbidden-string list is empty: set {DENY_ENV} or pass --deny.",
            file=sys.stderr,
        )
        return 2
    wheels = [p for p in args.archives if p.name.endswith(".whl")]
    sdists = [p for p in args.archives if p.name.endswith(".tar.gz")]
    unknown = [p for p in args.archives if p not in wheels and p not in sdists]
    if unknown or not wheels or not sdists:
        print("Pass at least one .whl and one .tar.gz, and nothing else.", file=sys.stderr)
        return 2
    problems: list[str] = []
    for wheel in wheels:
        problems.extend(check_wheel(wheel, extra))
    for sdist in sdists:
        problems.extend(check_sdist(sdist, extra))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    names = ", ".join(p.name for p in (*wheels, *sdists))
    print(f"OK: {names} ({len(extra)} extra forbidden strings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
