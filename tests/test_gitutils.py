import pathlib
import shutil
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from fromager.gitutils import _generate_git_archival_from_git, git_clone_fast


@patch("fromager.external_commands.run")
def test_git_clone_fast(m_run: Mock, tmp_path: pathlib.Path) -> None:
    repo_url = "https://git.test/project.git"
    git_clone_fast(output_dir=tmp_path, repo_url=repo_url)

    assert m_run.call_count == 2
    m_run.assert_any_call(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            repo_url,
            str(tmp_path),
        ],
        network_isolation=False,
    )
    m_run.assert_any_call(
        [
            "git",
            "checkout",
            "HEAD",
        ],
        network_isolation=False,
        cwd=str(tmp_path),
    )


@patch("fromager.external_commands.run")
def test_git_clone_fast_submodules(m_run: Mock, tmp_path: pathlib.Path) -> None:
    repo_url = "https://git.test/project.git"
    tmp_path.joinpath(".gitmodules").touch()
    git_clone_fast(output_dir=tmp_path, repo_url=repo_url)

    assert m_run.call_count == 3
    m_run.assert_any_call(
        [
            "git",
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--filter=blob:none",
            "--jobs=4",
        ],
        cwd=str(tmp_path),
        network_isolation=False,
    )


@patch("fromager.external_commands.run")
def test_generate_git_archival_from_git(m_run: Mock, tmp_path: pathlib.Path) -> None:
    m_run.return_value = "git log output"
    _generate_git_archival_from_git(tmp_path)

    assert m_run.call_count == 1
    m_run.assert_any_call(
        [
            "git",
            "--git-dir",
            str(tmp_path / ".git"),
            "log",
            "--pretty=format:node: %H%nnode-date: %cI%ndescribe-name: %(describe:tags=true,match=*[0-9]*)%n",
            "-1",
        ],
        network_isolation=False,
    )


def _setuptools_scm_version(path: pathlib.Path) -> str:
    return (
        subprocess.check_output(
            [sys.executable, "-m", "setuptools_scm"],
            text=True,
            stderr=subprocess.STDOUT,
            cwd=str(path),
        )
        .strip()
        .splitlines()[-1]
    )


@pytest.mark.network
@pytest.mark.skipif(shutil.which("git") is None, reason="requires 'git' command")
def test_git_clone_real(tmp_path: pathlib.Path) -> None:
    repo_url = "https://github.com/python-wheel-build/fromager.git"
    git_clone_fast(output_dir=tmp_path, repo_url=repo_url, ref="refs/tags/0.73.0")
    assert tmp_path.joinpath("src", "fromager").is_dir()

    _generate_git_archival_from_git(tmp_path)
    archival_txt = tmp_path / ".git_archival.txt"
    assert archival_txt.read_text().splitlines() == [
        "node: a7ee0f89fd1e5d7afbb6914b974d1fe73dff36c8",
        "node-date: 2025-11-19T19:41:04Z",
        "describe-name: 0.73.0",
    ]
    assert _setuptools_scm_version(tmp_path) == "0.73.0"

    # again without .git
    shutil.rmtree(tmp_path / ".git")
    assert _setuptools_scm_version(tmp_path) == "0.73.0"
