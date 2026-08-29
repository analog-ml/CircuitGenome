"""Where a single stage's output node is, and what the load presents there.

A multi-stage chain gets its first-stage output node for free -- it is the next
stage's signal gate.  A single stage has no next stage, and the pair's own drain
is only the output node when the load has nothing above it.  These tests pin the
structural rule that resolves it, and the two metrics that depend on the answer:
PSRR (the load branch's conductance at that node) and gain (its ``rout``).

Issue #228.
"""
import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer import load_tech, size_circuit, SizingSpec
from circuitgenome.sizer.physics.stage_chain import (
    _signal_path_nets,
    _single_ended_output_net,
)
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits
from circuitgenome.synthesizer.models import Device


def D(ref, t, **term):
    return Device(ref=ref, type=t, terminals=term)


# --------------------------------------------------------------------------- #
# The structural rule
# --------------------------------------------------------------------------- #
# A telescopic-cascode load in miniature: the pair (mp1/mp2) drives cascodes
# (mc1/mc2) that terminate on the reference node `ref` and on `out`.  `ref` is
# diode-connected and gates the mirror, `out` gates nothing -- which is the
# whole of the distinction.
_PAIR = [D("mp1", "pmos", g="in1", d="d1", s="tail"),
         D("mp2", "pmos", g="in2", d="d2", s="tail")]
_CASCODE_LOAD = [
    D("mc1", "pmos", g="bias1", d="ref", s="d1"),
    D("mc2", "pmos", g="bias1", d="out", s="d2"),
    D("mn1", "nmos", g="ref", d="ref", s="gnd!"),      # diode: the reference
    D("mn2", "nmos", g="ref", d="out", s="gnd!"),      # the mirrored leg
]
_MIRROR_LOAD = [
    D("mn1", "nmos", g="d1", d="d1", s="gnd!"),        # diode on the pair drain
    D("mn2", "nmos", g="d1", d="d2", s="gnd!"),
]


def test_signal_path_climbs_the_cascodes_above_the_pair():
    nets = _signal_path_nets(_PAIR, _PAIR + _CASCODE_LOAD)
    assert nets == frozenset({"d1", "d2", "ref", "out"})


def test_signal_path_stops_at_the_pair_drains_with_no_cascode():
    nets = _signal_path_nets(_PAIR, _PAIR + _MIRROR_LOAD)
    assert nets == frozenset({"d1", "d2"})


def test_output_node_is_the_signal_path_end_that_is_not_the_reference():
    """Both legs of a cascode load terminate; only one of them is the output."""
    out = _single_ended_output_net(_CASCODE_LOAD,
                                   _signal_path_nets(_PAIR, _PAIR + _CASCODE_LOAD),
                                   _PAIR + _CASCODE_LOAD)
    assert out == "out"


def test_output_node_on_a_mirror_load_is_the_non_reference_drain():
    """The rule works one level down too: `d1` is the diode, `d2` the output."""
    out = _single_ended_output_net(_MIRROR_LOAD,
                                   _signal_path_nets(_PAIR, _PAIR + _MIRROR_LOAD),
                                   _PAIR + _MIRROR_LOAD)
    assert out == "d2"


def test_output_node_is_none_for_a_resistor_load():
    """No load device means no diode leg to tell the two drains apart.

    They are also interchangeable — the halves are symmetric — so the caller's
    fallback to the pair's own drain is correct, and ``None`` is what keeps it
    the documented answer rather than a coin flip between two equal nets.
    """
    resistor_load: list[Device] = []                   # a resistor load slot
    assert _single_ended_output_net(
        resistor_load, _signal_path_nets(_PAIR, _PAIR), _PAIR) is None


# --------------------------------------------------------------------------- #
# End to end: every single-stage load variant in the catalog
# --------------------------------------------------------------------------- #
_SPEC = SizingSpec(vdd=3.3, vss=0.0, ibias=20e-6, cl=5e-12, gain_min_db=50)


def _one_stage_by_load():
    """{load variant name: SizingResult} — one sized design per load variant."""
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    tech = load_tech("gf180mcu")
    out = {}
    for c in enumerate_circuits(topo, mods, config={"include_infeasible": True}):
        name = c.variant_map["load"].name
        if name in out:
            continue
        parsed = parse(to_flat_spice(c))
        sr = recognize(parsed)
        out[name] = size_circuit(parsed, sr, assign_slots(sr, topo), topo,
                                 tech, _SPEC)
    return out


@pytest.fixture(scope="module")
def one_stage_by_load():
    return _one_stage_by_load()


def test_every_single_stage_load_variant_reports_psrr(one_stage_by_load):
    """Coverage guard for the PSRR blind spots of issue #228.

    Before the fix `psrr_db` was withheld on 5 of the 10 catalog load variants,
    for two unrelated reasons: a resistor load has no device to read a ``gds``
    off, and a cascode load has no device on the *pair's drain* -- which was
    being used as the output node.  A caller could not tell either from a
    deliberate omission.
    """
    silent = sorted(name for name, r in one_stage_by_load.items()
                    if r.transistors and "psrr_db" not in r.metrics)
    assert not silent, f"psrr_db still withheld for: {silent}"


def test_cascoding_a_load_improves_its_psrr(one_stage_by_load):
    """A stacked load presents 1/(ro·(1+gm·R)), not the top device's bare gds.

    Read naively, a cascode load would report a *worse* PSRR than the plain
    mirror it improves on — the top device of a cascode stack runs at the same
    current with a similar gds, so its bare conductance says nothing about the
    stack below it.
    """
    plain = one_stage_by_load["active_load_nmos"].metrics["psrr_db"]
    cascoded = one_stage_by_load["telescopic_cascode_load_pmos"].metrics["psrr_db"]
    assert cascoded > plain + 20.0


def test_resistor_load_psrr_is_the_stage_gain_over_its_own_load(one_stage_by_load):
    """PSRR = gm1·R for a resistor-loaded stage, i.e. its own gain.

    The output-node conductance is exactly 1/R — no device model needed, the
    sizer already solved for it — so PSRR and gain come out of the same two
    numbers and must agree to within the load's ``ro`` shunting the resistor.
    """
    for name in ("resistor_load_vdd", "resistor_load_gnd"):
        m = one_stage_by_load[name].metrics
        assert m["psrr_db"] == pytest.approx(m["gain_db"], abs=1.0)


def test_cascode_loads_measure_gain_above_the_pair_drain(one_stage_by_load):
    """A telescopic stage's output is not the pair's drain (issue #228).

    The pair's drain is the cascode *source* — a ~1/gm node. Measuring `rout`
    there reported a telescopic cascode with less gain than a plain mirror
    load, which inverts the entire point of the topology.
    """
    plain = one_stage_by_load["active_load_nmos"].metrics["gain_db"]
    for name in ("telescopic_cascode_load_pmos",
                 "telescopic_cascode_load_wideswing_pmos",
                 "folded_cascode_load_pmos_input_single_output"):
        assert one_stage_by_load[name].metrics["gain_db"] > plain


def test_phase_margin_withheld_only_for_the_two_documented_load_families(
        one_stage_by_load):
    """Resistor and wide-swing telescopic loads report no PM, by decision.

    Neither has a diode-connected load device, so this model cannot place a
    non-dominant pole: a resistor-loaded stage is single-pole here (PM would be
    exactly 90° for every sizing), and a wide-swing cascode's pole sits at the
    cascode source against a capacitance the sizer does not model.  Withholding
    is the documented answer; this test is what makes it a decision rather than
    a gap that could silently spread to another load family.
    """
    silent = {name for name, r in one_stage_by_load.items()
              if r.transistors and "phase_margin_deg" not in r.metrics}
    assert silent == {"resistor_load_vdd", "resistor_load_gnd",
                      "telescopic_cascode_load_wideswing_nmos",
                      "telescopic_cascode_load_wideswing_pmos"}
