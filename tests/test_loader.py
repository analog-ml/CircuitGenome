"""Tests for tech-config loading and process-param validation (issue #158)."""
from pathlib import Path

import pytest
import yaml

from circuitgenome.sizer.shared.loader import load_tech

_BASE = {
    "name": "t",
    "width": {"min": 1, "max": 10, "step": 1},
    "length": {"min": 1, "max": 5, "step": 1},
    "cap": {"min_pf": 1, "max_pf": 10, "step_pf": 1},
}


def _write(tmp_path: Path, nmos: dict, pmos: dict, **extra) -> Path:
    data = {**_BASE, "nmos": nmos, "pmos": pmos, **extra}
    p = tmp_path / "tech.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_builtin_techs_load():
    """Every bundled tech still loads."""
    for name in ("generic", "ptm45", "gf180mcu", "sky130"):
        assert load_tech(name).name


def test_lut_tech_omits_level1_params():
    """A LUT-driven PDK tech carries no Level-1 params at all (#158).

    mu_cox/lam are dead weight, and vth is decoupled too — the load-resistor
    seed now comes from the LUT.
    """
    for name in ("gf180mcu", "sky130"):
        tech = load_tech(name)
        assert tech.gmid_lut
        assert tech.nmos.mu_cox is None and tech.nmos.lam is None
        assert tech.pmos.mu_cox is None and tech.pmos.lam is None
        assert tech.nmos.vth is None and tech.pmos.vth is None


def test_level1_tech_keeps_mu_cox_and_lam():
    """A tech with no gmid_lut still provides the square-law params."""
    tech = load_tech("generic")
    assert tech.gmid_lut is None
    assert tech.nmos.mu_cox and tech.nmos.lam
    assert tech.pmos.mu_cox and tech.pmos.lam


def test_gmid_tech_may_omit_all_level1_params(tmp_path):
    """gm/Id tech loads with empty mosfet blocks (no vth/mu_cox/lam)."""
    lut = Path(load_tech("sky130").gmid_lut)
    p = _write(tmp_path, {"gamma": 0.4}, {"gamma": 0.5}, gmid_lut=str(lut))
    tech = load_tech(p)
    assert tech.nmos.vth is None and tech.nmos.mu_cox is None


def test_gmid_tech_may_omit_mosfet_blocks_entirely(tmp_path):
    """gm/Id tech loads with no nmos/pmos blocks at all (#158)."""
    lut = Path(load_tech("sky130").gmid_lut)
    data = {**_BASE, "gmid_lut": str(lut)}  # no nmos:/pmos:
    p = tmp_path / "tech.yaml"
    p.write_text(yaml.safe_dump(data))
    tech = load_tech(p)
    assert tech.nmos.vth is None and tech.pmos.mu_cox is None


def test_level1_tech_requires_vth(tmp_path):
    p = _write(tmp_path, {"mu_cox": 1e-4, "lam": 0.04},
               {"vth": -0.5, "mu_cox": 1e-4, "lam": 0.04})
    with pytest.raises(ValueError, match="nmos.vth"):
        load_tech(p)


def test_level1_tech_requires_mu_cox(tmp_path):
    p = _write(tmp_path, {"vth": 0.5, "lam": 0.04},
               {"vth": -0.5, "mu_cox": 1e-4, "lam": 0.04})
    with pytest.raises(ValueError, match="mu_cox"):
        load_tech(p)


def test_level1_tech_requires_positive_lam(tmp_path):
    p = _write(tmp_path, {"vth": 0.5, "mu_cox": 1e-4, "lam": -0.04},
               {"vth": -0.5, "mu_cox": 1e-4, "lam": 0.04})
    with pytest.raises(ValueError, match="lam"):
        load_tech(p)


def test_vth_sign_validated_for_any_tech(tmp_path):
    p = _write(tmp_path, {"vth": -0.5, "mu_cox": 1e-4, "lam": 0.04},
               {"vth": -0.5, "mu_cox": 1e-4, "lam": 0.04})
    with pytest.raises(ValueError, match="nmos.vth"):
        load_tech(p)
