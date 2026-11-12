from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
DEPENDABOT = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
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


def steps(body: str) -> list[str]:
    """Each `- ` step of a job body as text."""
    return re.split(r"^      - ", body, flags=re.MULTILINE)[1:]


def check_dist_step(body: str) -> str:
    found = [step for step in steps(body) if "tools/check_dist.py" in step]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("job", sorted(PUBLISH_JOBS))
def test_publish_jobs_have_no_password_or_secrets(job: str) -> None:
    body = RELEASE_JOBS[job]
    assert "password" not in body
    assert "secrets." not in body


def test_only_secret_is_the_deny_list_on_the_build_scan() -> None:
    assert "attestations: false" not in RELEASE + CI
    assert "secrets." not in CI
    assert re.findall(r"secrets\.[A-Za-z0-9_]+", RELEASE) == ["secrets.STORENET_DIST_DENY"]
    assert "secrets." in check_dist_step(RELEASE_JOBS["build"])
    assert "id-token" not in RELEASE_JOBS["build"]


def test_build_scan_requires_the_deny_list_on_every_tag() -> None:
    step = check_dist_step(RELEASE_JOBS["build"])
    assert "STORENET_DIST_DENY: ${{ secrets.STORENET_DIST_DENY }}" in step
    assert "run: python tools/check_dist.py --require-deny dist/*" in step
    assert "if:" not in step


def test_ci_scan_runs_without_the_deny_list() -> None:
    step = check_dist_step(jobs(CI)["dist"])
    assert "run: python tools/check_dist.py dist/*" in step
    assert "--require-deny" not in CI


def test_final_tag_fails_while_placeholder_remains() -> None:
    body = RELEASE_JOBS["build"]
    guard = [step for step in steps(body) if "PLACEHOLDER" in step]
    assert len(guard) == 1
    assert "if: steps.version.outputs.is_final == 'true'" in guard[0]
    files = "pyproject.toml LICENSE SECURITY.md README.md CHANGELOG.md"
    assert f"grep -nF PLACEHOLDER {files}" in guard[0]
    assert 'if [ "$found" -ne 1 ]; then' in guard[0]
    assert "exit 1" in guard[0]
    assert body.index("PLACEHOLDER") < body.index("upload-artifact")


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
    assert re.search(r"uses: pypa/gh-action-pypi-publish@[0-9a-f]{40} # release/v1\n", body)
    assert "actions/checkout" not in body


def test_build_checks_tag_against_pyproject_and_scans_dist() -> None:
    body = RELEASE_JOBS["build"]
    assert "${GITHUB_REF_NAME#v}" in body
    assert "tomllib" in body
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in body
    assert "is_final: ${{ steps.version.outputs.is_final }}" in body
    assert "python tools/check_dist.py --require-deny dist/*" in body
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


@pytest.mark.parametrize(("name", "text"), [("ci", CI), ("release", RELEASE)])
def test_every_action_is_pinned_to_a_commit_sha(name: str, text: str) -> None:
    uses = re.findall(r"uses: (\S+)(.*)$", text, re.MULTILINE)
    assert uses, f"{name} uses no action"
    for action, comment in uses:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r" # \S+", comment), f"{action} lacks its tag comment"


def test_dependabot_updates_actions_weekly() -> None:
    assert "package-ecosystem: github-actions" in DEPENDABOT
    assert "interval: weekly" in DEPENDABOT


def test_dev_tools_are_pinned_and_used_everywhere() -> None:
    pinned = REQUIREMENTS.split()
    assert {line.split("==")[0] for line in pinned} == {"build", "mypy", "pytest", "ruff"}
    assert all(re.fullmatch(r"[a-z]+==[0-9]+(\.[0-9]+)+", line) for line in pinned)
    for text in (CI, RELEASE):
        joined = re.sub(r"\\\n\s*", " ", text)
        for line in re.findall(r"pip install .*$", joined, re.MULTILINE):
            assert "-r requirements-dev.txt" in line or "storenet==" in line, line
    assert CI.count("-r requirements-dev.txt") == 2
    assert RELEASE.count("-r requirements-dev.txt") == 2
