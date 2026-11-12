from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
PUBLISH_JOBS = {"testpypi", "pypi"}
PYTHONS = ('"3.10"', '"3.12"', '"3.13"')


def top_level(text: str, key: str) -> str:
    """The block under a column-0 key, up to the next column-0 key."""
    match = re.search(rf"^{key}:\s*\n((?:[ \t].*\n|\s*\n)*)", text + "\n", re.MULTILINE)
    assert match, f"no top-level {key!r}"
    return match.group(1)


def jobs(text: str) -> dict[str, str]:
    """Job name to its body, split on the two-space-indented keys under `jobs:`."""
    found: dict[str, str] = {}
    current: str | None = None
    for line in top_level(text, "jobs").splitlines():
        start = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if start:
            current = start.group(1)
            found[current] = ""
        elif current is not None:
            found[current] += line + "\n"
    return found


def needs(body: str) -> set[str]:
    match = re.search(r"^    needs:\s*(.+)$", body, re.MULTILINE)
    if not match:
        return set()
    return {name.strip() for name in match.group(1).strip("[] ").split(",")}


RELEASE_JOBS = jobs(RELEASE)


def test_release_runs_only_on_v_tags() -> None:
    trigger = top_level(RELEASE, "on")
    assert re.search(r"tags:\s*\[\s*\"v\*\"\s*\]", trigger)
    for other in ("branches", "pull_request", "workflow_dispatch", "schedule", "release"):
        assert other not in trigger


def test_no_password_or_secrets_anywhere() -> None:
    for text in (RELEASE, CI):
        assert "password" not in text
        assert "secrets." not in text
        assert "attestations: false" not in text


def test_id_token_write_only_on_publish_jobs() -> None:
    assert "id-token" not in top_level(RELEASE, "permissions")
    assert "id-token" not in CI
    holders = {name for name, body in RELEASE_JOBS.items() if "id-token: write" in body}
    assert holders == PUBLISH_JOBS
    assert RELEASE.count("id-token") == len(PUBLISH_JOBS)


def test_job_chain_test_build_testpypi_smoke_pypi() -> None:
    assert set(RELEASE_JOBS) == {"test", "build", "testpypi", "smoke", "pypi"}
    assert needs(RELEASE_JOBS["build"]) == {"test"}
    assert needs(RELEASE_JOBS["testpypi"]) == {"build"}
    assert "testpypi" in needs(RELEASE_JOBS["smoke"])
    assert "smoke" in needs(RELEASE_JOBS["pypi"])


def test_pypi_needs_smoke_and_is_gated_by_is_final() -> None:
    body = RELEASE_JOBS["pypi"]
    assert re.search(r"^    if: needs\.build\.outputs\.is_final == 'true'$", body, re.MULTILINE)
    assert re.search(r"environment:\s*\n\s+name: pypi\n", body)
    assert "repository-url" not in body


def test_testpypi_publishes_to_test_index_in_its_environment() -> None:
    body = RELEASE_JOBS["testpypi"]
    assert re.search(r"environment:\s*\n\s+name: testpypi\n", body)
    assert "repository-url: https://test.pypi.org/legacy/" in body


@pytest.mark.parametrize("job", sorted(PUBLISH_JOBS))
def test_publish_jobs_use_trusted_publishing_and_no_checkout(job: str) -> None:
    body = RELEASE_JOBS[job]
    assert "uses: pypa/gh-action-pypi-publish@release/v1" in body
    assert "actions/checkout" not in body


def test_build_checks_tag_against_pyproject_and_scans_dist() -> None:
    body = RELEASE_JOBS["build"]
    assert "${GITHUB_REF_NAME#v}" in body
    assert "tomllib" in body
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in body
    assert "is_final: ${{ steps.version.outputs.is_final }}" in body
    assert "python tools/check_dist.py dist/*" in body
    assert body.index("check_dist.py") < body.index("upload-artifact")


def test_smoke_installs_from_testpypi_and_checks_version_and_exit_2() -> None:
    body = RELEASE_JOBS["smoke"]
    assert "--index-url https://test.pypi.org/simple/" in body
    assert 'test "$(storenet --version)" = "storenet ${VERSION}"' in body
    assert "storenet site list" in body
    assert 'test "$code" -eq 2' in body
    assert "actions/checkout" not in body


@pytest.mark.parametrize(("name", "text"), [("ci", CI), ("release", RELEASE)])
def test_matrix_covers_310_312_313(name: str, text: str) -> None:
    body = jobs(text)["test"]
    for version in PYTHONS:
        assert version in body, f"{name} matrix lacks {version}"
    for step in ("ruff check .", "mypy src", "pytest -q"):
        assert step in body


def test_ci_runs_on_main_push_and_pull_request() -> None:
    trigger = top_level(CI, "on")
    assert re.search(r"push:\s*\n\s+branches: \[main\]", trigger)
    assert re.search(r"pull_request:\s*\n\s+branches: \[main\]", trigger)
