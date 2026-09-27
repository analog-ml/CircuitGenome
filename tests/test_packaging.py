"""Packaging guards: what `packages.find` in pyproject.toml puts in a wheel."""

from fnmatch import fnmatchcase
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _ships(package: str) -> bool:
    """Whether setuptools' `packages.find` would put *package* in the wheel.

    Mirrors setuptools' matching: a dotted package name is kept when it
    matches an `include` pattern and no `exclude` pattern (fnmatchcase).
    """
    with PYPROJECT.open("rb") as f:
        cfg = tomllib.load(f)["tool"]["setuptools"]["packages"]["find"]
    return any(fnmatchcase(package, p) for p in cfg["include"]) and not any(
        fnmatchcase(package, p) for p in cfg.get("exclude", ())
    )


def test_real_packages_ship():
    assert _ships("circuitgenome")
    assert _ships("circuitgenome.sizer.verify")


@pytest.mark.parametrize(
    "package",
    [
        "circuitgenome.sizer.shared.eval_engines",
        "circuitgenome.sizer.shared.eval_engines.ngspice",
        "circuitgenome.sizer.shared.eval_engines.util",
    ],
)
def test_gitignored_eval_engines_is_excluded(package):
    # Discovery scans the disk, not git, so this ignored local reference
    # example leaked into the 0.3.0 wheel from a dirty working tree (#211).
    assert not _ships(package)
