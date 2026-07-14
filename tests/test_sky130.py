"""Tests for the SKY130 PDK integration (corner-library + subckt tech).

Like GF180, sky130 selects devices via a corner ``.lib`` and instantiates PDK
subcircuits — but its subckts take ``w=``/``l=`` in microns (the library sets
``.option scale=1.0u``) and name the internal BSIM4 instance after the cell
(``msky130_fd_pr__nfet_01v8``) instead of GF180's ``m0``, exercising the
``wl_units``/``device_handle`` tech fields.  All pure — none require ngspice.
"""
from pathlib import Path

from circuitgenome.sizer.shared.spice import deck
from circuitgenome.sizer.shared.device_model import build_device_model
from circuitgenome.sizer.shared.gmid_lut import GmIdLut
from circuitgenome.sizer.shared.loader import load_tech


def test_sky130_tech_loads_lib_and_maps():
    tech = load_tech("sky130")
    assert tech.spice_model is None and tech.spice_lib is not None
    assert Path(tech.spice_lib.file).exists()
    assert tech.spice_lib.design is None  # globals live in the .lib sections
    assert tech.spice_lib.corner == "tt"
    assert tech.spice_lib.corners == ["tt", "ss", "ff", "sf", "fs"]
    assert tech.device_map == {"nmos": "sky130_fd_pr__nfet_01v8",
                               "pmos": "sky130_fd_pr__pfet_01v8"}
    assert tech.device_handle == {"nmos": "msky130_fd_pr__nfet_01v8",
                                  "pmos": "msky130_fd_pr__pfet_01v8"}
    assert tech.wl_units == "um"
    # 1.8 V core grids: sky130 bins cover W>=0.42 (both polarities), L>=0.15.
    assert tech.width.min == 0.42 and tech.length.min == 0.15
    assert tech.nmos.vth > 0 > tech.pmos.vth


def test_sky130_emit_model_selects_corner():
    tech = load_tech("sky130")
    nominal = deck._emit_model(tech)
    assert '.lib "' in nominal and "sky130.lib.spice" in nominal
    assert nominal.rstrip().endswith("tt")
    assert ".include" not in nominal  # no separate design file
    assert deck._emit_model(tech, "ss").rstrip().endswith("ss")


def test_sky130_device_translation_um_units():
    tech = load_tech("sky130")
    body = ["m1_input_pair net1 in1 net2 net2 pmos W=10.00000u L=0.56000u",
            "c1_compensation net2 out 1p"]
    out = deck._emit_body(tech, body)
    # Subckt instance with bare micron numbers (scale=1.0u in the library).
    assert out[0] == ("x1_input_pair net1 in1 net2 net2 "
                      "sky130_fd_pr__pfet_01v8 w=10.00000 l=0.56000")
    assert out[1] == "c1_compensation net2 out 1p"


def test_sky130_dispatches_to_gmid_lut():
    tech = load_tech("sky130")
    assert tech.gmid_lut is not None and Path(tech.gmid_lut).exists()
    assert build_device_model(tech).is_gmid
    lut = GmIdLut(tech.gmid_lut)
    # Physical spot checks at Lmin: nfet vgs near its ~0.72 V threshold at
    # moderate inversion, and both polarities within the 1.8 V supply.
    assert 0.6 < lut.vgs("nmos", 15.0, 0.15) < 0.85
    assert 0.55 < lut.vgs("pmos", 15.0, 0.15) < 0.8
    assert 0.0 < lut.vgs("nmos", 6.0, 0.15) < 1.8
    # Current density falls from strong to weak inversion.
    assert lut.id_per_w("nmos", 6.0, 0.15) > lut.id_per_w("nmos", 20.0, 0.15)


def test_sky130_op_handle_is_per_polarity():
    tech = load_tech("sky130")
    assert (deck._dev_prefix(tech, "m1_input_pair", "pmos")
            == "@m.xdut.x1_input_pair.msky130_fd_pr__pfet_01v8")
    assert (deck._dev_prefix(tech, "m5_tail", "nmos")
            == "@m.xdut.x5_tail.msky130_fd_pr__nfet_01v8")
