"""Tests for the sized-netlist dialect support in the recognizer parser.

Covers issue #168: MOSFET ``W/L/nf/m`` params, real process model names
(``sky130_fd_pr__*``), preserved R/C value tokens, and backward compatibility
with the unsized ``to_flat_spice`` dialect. Sized netlists must ``recognize()``
identically to their bare ``nmos``/``pmos`` equivalents — sizes ride along.
"""
import pytest

from circuitgenome.recognizer.netlist_parser import parse
from circuitgenome.recognizer.subcircuit_recognizer import recognize


# ── A hand-written SKY130 differential pair with W/L/nf/m params ─────────────
_SKY130_DIFF_PAIR = """\
.subckt diffpair in1 in2 outp outn tail vdd! gnd!
m1 outp in1 tail gnd! sky130_fd_pr__nfet_01v8 W=4u L=0.15u nf=2 m=1
m2 outn in2 tail gnd! sky130_fd_pr__nfet_01v8 W=4u L=0.15u nf=2 m=1
.ends
"""


def test_sized_diff_pair_types_and_sizing():
    parsed = parse(_SKY130_DIFF_PAIR)

    assert parsed.name == "diffpair"
    assert [d.type for d in parsed.devices] == ["nmos", "nmos"]

    m1 = parsed.devices[0]
    assert m1.terminals == {"d": "outp", "g": "in1", "s": "tail", "b": "gnd!"}
    assert m1.params == {"W": "4u", "L": "0.15u", "nf": "2", "m": "1"}


# ── A 5-transistor OTA with sky130_fd_pr__* model names ──────────────────────
# NMOS input pair (m1/m2), PMOS current-mirror load (m3 diode + m4), NMOS tail
# (m5). Sized with W/L on every device.
_SKY130_5T_OTA = """\
.subckt ota5t in1 in2 out ibias vdd! gnd!
m3 n1  n1  vdd! vdd! sky130_fd_pr__pfet_01v8 W=6u L=0.5u
m4 out n1  vdd! vdd! sky130_fd_pr__pfet_01v8 W=6u L=0.5u
m1 n1  in1 tail gnd! sky130_fd_pr__nfet_01v8 W=4u L=0.5u
m2 out in2 tail gnd! sky130_fd_pr__nfet_01v8 W=4u L=0.5u
m5 tail ibias gnd! gnd! sky130_fd_pr__nfet_01v8 W=8u L=1u
.ends
"""

# The same OTA with bare nmos/pmos tokens and no sizing — the recognition
# result must be identical (sizes ride along, patterns match on topology).
_BARE_5T_OTA = """\
.subckt ota5t in1 in2 out ibias vdd! gnd!
m3 n1  n1  vdd! vdd! pmos
m4 out n1  vdd! vdd! pmos
m1 n1  in1 tail gnd! nmos
m2 out in2 tail gnd! nmos
m5 tail ibias gnd! gnd! nmos
.ends
"""


def test_sized_ota_recognizes_diff_pair_and_current_mirror():
    parsed = parse(_SKY130_5T_OTA)
    result = recognize(parsed)
    names = {s.name for s in result.structures}

    assert "differential_pair_nmos" in names
    assert "current_mirror_pmos" in names


def test_sized_and_bare_ota_recognize_identically():
    sized = {s.name for s in recognize(parse(_SKY130_5T_OTA)).structures}
    bare = {s.name for s in recognize(parse(_BARE_5T_OTA)).structures}
    assert sized == bare


# ── Backward compatibility: unsized dialect unchanged ───────────────────────
def test_unsized_netlist_has_empty_params():
    parsed = parse(_BARE_5T_OTA)
    assert all(d.params == {} for d in parsed.devices)
    assert [d.type for d in parsed.devices] == ["pmos", "pmos", "nmos", "nmos", "nmos"]


# ── Resistor/capacitor value tokens preserved ───────────────────────────────
def test_rc_value_tokens_preserved():
    parsed = parse("""\
.subckt rc a b
r1 a b 12.5k
c1 a b 3.3p
c2 a b
.ends
""")
    r1, c1, c2 = parsed.devices
    assert r1.type == "resistor" and r1.params == {"value": "12.5k"}
    assert c1.type == "capacitor" and c1.params == {"value": "3.3p"}
    assert c2.params == {}  # no value token → nothing preserved


# ── Configurable model-name table ───────────────────────────────────────────
def test_custom_model_map_override():
    netlist = """\
.subckt m in1 in2 t vdd! gnd!
m1 o1 in1 t gnd! my_custom_nfet W=2u L=0.5u
.ends
"""
    with pytest.raises(ValueError):
        parse(netlist)  # unknown model by default

    parsed = parse(netlist, model_map={"my_custom_nfet": "nmos"})
    assert parsed.devices[0].type == "nmos"
    assert parsed.devices[0].params == {"W": "2u", "L": "0.5u"}


# ── Real-flow x-prefixed subcircuit MOSFET instances ────────────────────────
def test_xprefixed_mosfet_instance_parses():
    parsed = parse("""\
.subckt xdev d g s b vdd! gnd!
x1 d g s b sky130_fd_pr__pfet_01v8 W=3u L=0.5u
.ends
""")
    assert parsed.devices[0].type == "pmos"
    assert parsed.devices[0].params == {"W": "3u", "L": "0.5u"}


def test_unknown_model_raises():
    with pytest.raises(ValueError):
        parse(".subckt s a b c d\nm1 a b c d bogus_model\n.ends")
