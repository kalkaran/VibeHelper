"""Expose the standalone quality suite's installer fixtures to pytest."""

import pytest
from quality_controls_test import install_platform


@pytest.fixture(scope="module")
def repositories(tmp_path_factory):
    root = tmp_path_factory.mktemp("quality-installations")
    repos = {}
    for platform in ("MacOS", "WSL"):
        repos[platform] = root / platform.lower()
        install_platform(platform, repos[platform])
    return repos


@pytest.fixture
def repository(repositories):
    return repositories["MacOS"]
