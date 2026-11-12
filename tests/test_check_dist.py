from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_dist", ROOT / "tools" / "check_dist.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_dist = _load()

CLEAN_CODE = b'"""Package."""\nHOME = "https://storenet.ai"\n'
README = b"# storenet\n\nSTORENET_BASE_URL=https://shop.example.com\nhttps://pypi.org/project/storenet/\n"
WHEEL_FILES = {
    "storenet/__init__.py": CLEAN_CODE,
    "storenet/cli.py": CLEAN_CODE,
    "storenet/py.typed": b"",
    "storenet-0.1.0.dist-info/METADATA": b"Name: storenet\n\n" + README,
    "storenet-0.1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
    "storenet-0.1.0.dist-info/RECORD": b"storenet/__init__.py,sha256=abc,10\n",
    "storenet-0.1.0.dist-info/entry_points.txt": b"[console_scripts]\n",
    "storenet-0.1.0.dist-info/licenses/LICENSE": b"MIT License\n",
}
SDIST_FILES = {
    "storenet-0.1.0/PKG-INFO": b"Name: storenet\n",
    "storenet-0.1.0/pyproject.toml": b'[project.urls]\nSource = "https://github.com/storenet-ai/storenet-python"\n',
    "storenet-0.1.0/README.md": README,
    "storenet-0.1.0/LICENSE": b"MIT License\n",
    "storenet-0.1.0/.gitignore": b"dist/\n",
    "storenet-0.1.0/src/storenet/__init__.py": CLEAN_CODE,
    "storenet-0.1.0/src/storenet/py.typed": b"",
}


def make_wheel(tmp_path: Path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "storenet-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        for name, data in files.items():
            wheel.writestr(name, data)
    return path


def make_sdist(tmp_path: Path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "storenet-0.1.0.tar.gz"
    with tarfile.open(path, "w:gz") as sdist:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            sdist.addfile(info, io.BytesIO(data))
    return path


def run(tmp_path: Path, wheel: dict[str, bytes], sdist: dict[str, bytes], *extra: str) -> int:
    paths = [str(make_wheel(tmp_path, wheel)), str(make_sdist(tmp_path, sdist))]
    return int(check_dist.main([*paths, *extra]))


def test_clean_archives_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(check_dist.DENY_ENV, raising=False)
    assert run(tmp_path, WHEEL_FILES, SDIST_FILES) == 0


@pytest.mark.parametrize(
    "member", ["tests/test_cli.py", "storenet/data/keys.json", "storenet-0.1.0.dist-info/extra"]
)
def test_member_outside_whitelist_fails_wheel(tmp_path: Path, member: str) -> None:
    assert run(tmp_path, {**WHEEL_FILES, member: b"x"}, SDIST_FILES) == 1


@pytest.mark.parametrize(
    "member", ["storenet-0.1.0/tests/test_cli.py", "storenet-0.1.0/tools/check_dist.py"]
)
def test_member_outside_whitelist_fails_sdist(tmp_path: Path, member: str) -> None:
    assert run(tmp_path, WHEEL_FILES, {**SDIST_FILES, member: b"x"}) == 1


@pytest.mark.parametrize("token", check_dist.FORBIDDEN)
def test_forbidden_string_fails(
    tmp_path: Path, token: str, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = {**WHEEL_FILES, "storenet/cli.py": f"X = '{token.upper()}abc'\n".encode()}
    assert run(tmp_path, bad, SDIST_FILES) == 1
    assert "chuỗi cấm" in capsys.readouterr().err


def test_extra_denied_words_from_flag_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = {**SDIST_FILES, "storenet-0.1.0/README.md": b"internal acmecorp and zetashop\n"}
    monkeypatch.delenv(check_dist.DENY_ENV, raising=False)
    assert run(tmp_path, WHEEL_FILES, bad) == 0
    assert run(tmp_path, WHEEL_FILES, bad, "--deny", "AcmeCorp") == 1
    monkeypatch.setenv(check_dist.DENY_ENV, "nothing, zetashop")
    assert run(tmp_path, WHEEL_FILES, bad) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://storenet.ai.evil.example/x",
        "http://storenet.ai",
        "https://github.com/someone-else/storenet-python",
        "https://internal.example.net/api",
    ],
)
def test_url_outside_allowlist_fails(tmp_path: Path, url: str) -> None:
    bad = {**WHEEL_FILES, "storenet/cli.py": f'URL = "{url}"\n'.encode()}
    assert run(tmp_path, bad, SDIST_FILES) == 1


def test_allowed_urls_with_paths_pass(tmp_path: Path) -> None:
    code = (
        b'A = "https://github.com/storenet-ai/storenet-python/issues"\nB = "https://storenet.ai/"\n'
    )
    assert run(tmp_path, {**WHEEL_FILES, "storenet/cli.py": code}, SDIST_FILES) == 0


@pytest.mark.parametrize("kind", ["only-wheel", "only-sdist", "unknown"])
def test_needs_one_wheel_and_one_sdist(tmp_path: Path, kind: str) -> None:
    wheel = str(make_wheel(tmp_path, WHEEL_FILES))
    sdist = str(make_sdist(tmp_path, SDIST_FILES))
    stray = tmp_path / "notes.txt"
    stray.write_text("x")
    argv = {
        "only-wheel": [wheel],
        "only-sdist": [sdist],
        "unknown": [wheel, sdist, str(stray)],
    }[kind]
    assert check_dist.main(argv) == 2


def test_require_deny_with_empty_list_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(check_dist.DENY_ENV, " , ")
    assert run(tmp_path, WHEEL_FILES, SDIST_FILES, "--require-deny") == 2
    assert check_dist.DENY_ENV in capsys.readouterr().err
    monkeypatch.delenv(check_dist.DENY_ENV)
    assert run(tmp_path, WHEEL_FILES, SDIST_FILES, "--require-deny") == 2


def test_require_deny_with_words_scans_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(check_dist.DENY_ENV, "acmecorp")
    assert run(tmp_path, WHEEL_FILES, SDIST_FILES, "--require-deny") == 0
    bad = {**SDIST_FILES, "storenet-0.1.0/README.md": b"internal AcmeCorp\n"}
    assert run(tmp_path, WHEEL_FILES, bad, "--require-deny") == 1
    monkeypatch.delenv(check_dist.DENY_ENV)
    assert run(tmp_path, WHEEL_FILES, SDIST_FILES, "--require-deny", "--deny", "x1y2") == 0
