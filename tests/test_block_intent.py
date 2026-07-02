"""Tests for the functional-block design-intent layer (Spec → Block → Transistor)."""
import dataclasses

import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.gmid import size_gmid
from circuitgenome.sizer.gmid.intent import (
    DEFAULT_BLOCK_INTENTS,
    GmIdIntent,
    functional_block,
)
from circuitgenome.sizer.shared.device_model import (
    CASCODE,
    CURRENT_SOURCE,
    SIGNAL,
)
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits

_TOPO = "two_stage_opamp_single_ended"


def _spec():
    return SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=55, gbw_min_hz=1e6,
                      phase_margin_min_deg=60, slew_rate_min_vps=0.65e6)


@pytest.fixture(scope="module")
def sized():
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    circ = next(enumerate_circuits(topo, load_modules()))
    parsed = parse(to_flat_spice(circ))
    fbr = assign_slots(recognize(parsed), topo)
    return parsed, recognize(parsed), fbr, topo, load_tech("ptm45")


def test_functional_block_mapping():
    # Signal precedence + stage split; non-signal → current-source blocks.
    assert functional_block("input_pair", is_signal=True, is_cascode=False) == "input_stage"
    assert functional_block("second_stage", True, False) == "gain_stage"
    assert functional_block("third_stage", True, False) == "output_stage"
    # Same slot, non-signal device → a current-source load, not a gain stage.
    assert functional_block("second_stage", False, False) == "stage_load"
    assert functional_block("load", False, False) == "active_load"
    assert functional_block("tail_current", False, False) == "tail_current"
    assert functional_block("bias_gen", False, False) == "bias_generator"
    # Cascode modifier (never a signal device).
    assert functional_block("load", False, True) == "cascode"


def test_registry_is_complete_and_documented():
    # Every default block has a role and a non-empty rationale (consumed data).
    for name, bi in DEFAULT_BLOCK_INTENTS.items():
        assert bi.role in (SIGNAL, CURRENT_SOURCE, CASCODE), name
        assert bi.rationale.strip(), name
        # Signal blocks solve gm/Id from the spec (no fixed region); others fix it.
        if bi.role == SIGNAL:
            assert bi.gm_id is None, name
        else:
            assert bi.gm_id and bi.gm_id > 0, name


def test_result_carries_transistor_intents(sized):
    parsed, sr, fbr, topo, tech = sized
    r = size_gmid(parsed, sr, fbr, topo, tech, _spec())
    # One intent per sized transistor, each with a rationale.
    assert set(r.transistor_intents) == set(r.transistors)
    assert all(ti.rationale for ti in r.transistor_intents.values())
    # A mixed slot splits by role: the second-stage driver is a gain stage,
    # its current-source load is not.
    assert r.transistor_intents["mn1_second_stage"].block == "gain_stage"
    assert r.transistor_intents["mp1_second_stage"].block == "stage_load"
    assert r.transistor_intents["m1_input_pair"].role == SIGNAL


def test_per_block_override_is_local(sized):
    parsed, sr, fbr, topo, tech = sized
    base = size_gmid(parsed, sr, fbr, topo, tech, _spec())

    # Retune only the tail current source to a weaker gm/Id (higher Vdsat).
    bi = dict(DEFAULT_BLOCK_INTENTS)
    bi["tail_current"] = dataclasses.replace(bi["tail_current"], gm_id=6.0)
    tuned = size_gmid(parsed, sr, fbr, topo, tech, _spec(), GmIdIntent(block_intents=bi))

    t0, t1 = base.transistors["m1_tail_current"], tuned.transistors["m1_tail_current"]
    assert t1.vds_sat_v > t0.vds_sat_v          # lower gm/Id → larger Vdsat
    assert t1.w_um != t0.w_um                    # geometry changed
    # The input pair (a different block) is untouched.
    assert (tuned.transistors["m1_input_pair"].w_um
            == base.transistors["m1_input_pair"].w_um)
