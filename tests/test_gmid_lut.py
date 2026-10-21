"""Tests for the gm/Id lookup table and the Level-1 model equivalence."""
from pathlib import Path

import pytest

import circuitgenome.sizer as _sz
from circuitgenome.sizer.physics import equations as eq
from circuitgenome.sizer.physics.device_model import Level1Model
from circuitgenome.sizer.physics.gmid_lut import GmIdLut
from circuitgenome.sizer import load_tech

_LUT_PATH = Path(_sz.__file__).parent / "config" / "models" / "ptm45_gmid.npz"


@pytest.fixture(scope="module")
def lut():
    return GmIdLut(_LUT_PATH)


@pytest.mark.parametrize("dtype", ["nmos", "pmos"])
@pytest.mark.parametrize("gm_id", [8.0, 12.0, 16.0, 20.0])
@pytest.mark.parametrize("l_um", [0.045, 0.09, 0.25, 0.5])
def test_idw_gmid_round_trip(lut, dtype, gm_id, l_um):
    """gm_id_from_idw(id_per_w(g, L), L) recovers g."""
    idw = lut.id_per_w(dtype, gm_id, l_um)
    assert idw > 0
    g2 = lut.gm_id_from_idw(dtype, idw, l_um)
    assert abs(g2 - gm_id) < 0.3


@pytest.mark.parametrize("dtype", ["nmos", "pmos"])
def test_idw_decreases_with_gm_id(lut, dtype):
    """Current density falls as gm/Id rises (weaker inversion)."""
    vals = [lut.id_per_w(dtype, g, 0.1) for g in (8, 12, 16, 20)]
    assert all(a > b for a, b in zip(vals, vals[1:]))


@pytest.mark.parametrize("dtype", ["nmos", "pmos"])
def test_intrinsic_gain_rises_with_length(lut, dtype):
    """gm/gds increases with L — the lever the single-λ Level-1 model lacked."""
    short = lut.gm_gds(dtype, 12.0, lut.l_axis[0])
    long = lut.gm_gds(dtype, 12.0, lut.l_axis[-1])
    assert long > short > 0


def test_max_gm_id_within_axis(lut):
    assert lut.max_gm_id("nmos", 0.1) == pytest.approx(lut.gm_id_axis[-1])


def test_off_grid_length_interpolates(lut):
    """A length between grid rows lies between the bracketing values."""
    lo = lut.gm_gds("nmos", 12.0, lut.l_axis[0])
    hi = lut.gm_gds("nmos", 12.0, lut.l_axis[1])
    mid = lut.gm_gds("nmos", 12.0, (lut.l_axis[0] + lut.l_axis[1]) / 2)
    assert min(lo, hi) <= mid <= max(lo, hi)


# --------------------------------------------------------------------------- #
# Level1Model must reproduce equations.* exactly (generic-path regression guard)
# --------------------------------------------------------------------------- #
def test_level1_model_matches_equations():
    tech = load_tech("generic")
    m = Level1Model(tech)
    for dtype, p in (("nmos", tech.nmos), ("pmos", tech.pmos)):
        w, l, ids = 4.0, 0.5, 5e-6
        assert m.gm(dtype, w, l, ids) == eq.gm(p.mu_cox, w, l, ids)
        assert m.gds(dtype, w, l, ids) == eq.gd(p.lam, ids)
        assert m.gds_estimate(dtype, ids, "signal") == eq.gd(p.lam, ids)
        assert m.vds_sat(dtype, w, l, ids) == eq.vds_sat(p.mu_cox, w, l, ids)
        assert m.vgs(dtype, w, l, ids) == eq.vgs_from_ids(p.mu_cox, w, l, ids, p.vth)
        assert m.gm_ceiling(dtype, ids, l) == eq.gm_ceiling(ids)


# --------------------------------------------------------------------------- #
# Bias correction tables: gds vs Vds, gmbs/gm vs Vsb (issue #273)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dtype", ["nmos", "pmos"])
def test_gds_scale_is_one_at_the_characterisation_point(lut, dtype):
    """The main table is swept at Vds = Vdd/2 (ptm45: 0.5 V)."""
    assert lut.gds_scale(dtype, 12.0, 0.09, 0.5) == pytest.approx(1.0)


@pytest.mark.parametrize("dtype", ["nmos", "pmos"])
def test_gds_rises_toward_the_saturation_knee(lut, dtype):
    """A device just above its Vdsat -- under a cascode -- has several times
    the Vdd/2 gds, which the single-point table cannot see."""
    near, mid, ref = (lut.gds_scale(dtype, 12.0, 0.09, v) for v in (0.12, 0.25, 0.5))
    assert near > mid > ref
    assert near > 3.0


@pytest.mark.parametrize("dtype", ["nmos", "pmos"])
def test_gmb_gm_falls_with_reverse_body_bias(lut, dtype):
    at0, at_half = lut.gmb_gm(dtype, 12.0, 0.09, 0.0), lut.gmb_gm(dtype, 12.0, 0.09, 0.5)
    assert 0.0 < at_half < at0 < 1.0


def test_table_without_bias_axes_reads_single_point(tmp_path):
    """An older .npz (no vds/vsb tables) keeps the pre-#273 behaviour."""
    import numpy as np
    data = np.load(_LUT_PATH)
    old = tmp_path / "old_gmid.npz"
    np.savez(old, **{k: data[k] for k in data.files
                     if not k.endswith(("gds_scale", "gmb_gm", "vds_axis", "vsb_axis"))})
    lut = GmIdLut(old)
    assert lut.gds_scale("nmos", 12.0, 0.09, 0.1) == 1.0
    assert lut.gmb_gm("nmos", 12.0, 0.09, 0.3) == 0.0


def test_gmid_model_reads_gds_no_deeper_than_vdsat():
    """Below Vdsat the device is in triode, where the node-level estimate that
    supplied ``vds`` (every device saturated) has already failed: the read
    stops at the knee instead of extrapolating into triode."""
    from circuitgenome.sizer.physics.device_model import GmIdModel
    tech = load_tech("ptm45")
    m = GmIdModel(tech, GmIdLut(tech.gmid_lut))
    w, l, ids = 2.0, 0.09, 10e-6
    vdsat = m.vds_sat("nmos", w, l, ids)
    assert m.gds("nmos", w, l, ids, vds=0.0) == m.gds("nmos", w, l, ids, vds=vdsat)
    assert m.gds("nmos", w, l, ids, vds=vdsat) > m.gds("nmos", w, l, ids)
    assert m.gds("nmos", w, l, ids, vds=0.5) == pytest.approx(m.gds("nmos", w, l, ids))
    assert m.gmb("nmos", w, l, ids, 0.0) > 0.0


def test_level1_model_has_no_bias_dependence():
    """Level-1 λ is Vds-independent and its SPICE card has gamma=0."""
    tech = load_tech("generic")
    m = Level1Model(tech)
    assert m.gds("nmos", 4.0, 0.5, 5e-6, vds=0.1) == eq.gd(tech.nmos.lam, 5e-6)
    assert m.gmb("nmos", 4.0, 0.5, 5e-6, 1.0) == 0.0
