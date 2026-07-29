"""Tests for the functional-block design-intent layer (Spec → Block → Transistor)."""
import dataclasses

import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.gmid import size_gmid
from circuitgenome.sizer.gmid.intent import (
    DEFAULT_BLOCK_INTENTS,
    DEFAULT_INTENT,
    INTENT_BY_TECH,
    GmIdIntent,
    functional_block,
    intent_for_tech,
    make_intent,
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
    # Numbered gain stages are all gain stages; only the follower slot is the
    # output_stage (buffer) block.
    assert functional_block("third_stage", True, False) == "gain_stage"
    assert functional_block("output_stage", True, False) == "output_stage"
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
        # Signal blocks solve gm/Id from the spec (no fixed region); others fix
        # it.  The source-follower output stage is the one exception: a signal
        # device with a fixed region, since it never gets a gm requirement.
        if bi.role == SIGNAL and name != "output_stage":
            assert bi.gm_id is None, name
        else:
            assert bi.gm_id and bi.gm_id > 0, name


def test_make_intent_default_matches_default_intent():
    # make_intent() with no overrides must reproduce DEFAULT_INTENT block-for-block
    # and in the flat role fallbacks — otherwise an "unchanged" per-tech entry
    # would silently perturb sizing.
    d = make_intent()
    assert (d.signal_l_mult, d.current_source_l_mult, d.cascode_l_mult) == (
        DEFAULT_INTENT.signal_l_mult, DEFAULT_INTENT.current_source_l_mult,
        DEFAULT_INTENT.cascode_l_mult)
    assert d.cascode_gm_id == DEFAULT_INTENT.cascode_gm_id
    for name, bi in DEFAULT_INTENT.block_intents.items():
        got = d.block_intents[name]
        assert (got.role, got.gm_id, got.l_mult) == (bi.role, bi.gm_id, bi.l_mult), name


def test_make_intent_keeps_block_and_flat_in_lockstep():
    # The knob must move BOTH the block intents (drive geometry) and the flat
    # fallbacks (drive the pre-geometry gds estimate).
    t = make_intent(signal_l_mult=3.0, cs_l_mult=6.0, cs_gm_id=12.0)
    assert t.signal_l_mult == 3.0 and t.current_source_l_mult == 6.0
    # Signal blocks: L moved, gm/Id still solved (None); current sources: both moved.
    assert t.block_intents["gain_stage"].l_mult == 3.0
    assert t.block_intents["gain_stage"].gm_id is None
    assert t.block_intents["tail_current"].l_mult == 6.0
    assert t.block_intents["tail_current"].gm_id == 12.0
    # Untouched knobs keep their defaults.
    assert t.block_intents["cascode"].l_mult == DEFAULT_INTENT.cascode_l_mult


def test_intent_for_tech_registry_fallback():
    # Unregistered tech → DEFAULT_INTENT; a registered entry is returned verbatim.
    assert intent_for_tech("ptm45_hp") is DEFAULT_INTENT
    assert intent_for_tech("does_not_exist") is DEFAULT_INTENT
    custom = make_intent(signal_l_mult=3.0)
    INTENT_BY_TECH["__test_tech__"] = custom
    try:
        assert intent_for_tech("__test_tech__") is custom
    finally:
        INTENT_BY_TECH.pop("__test_tech__", None)


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
    # A headroom-comfortable supply: at 1.0 V the DC headroom repair re-sizes
    # the tail regardless of intent, which would (correctly) mask the override.
    spec = dataclasses.replace(_spec(), vdd=2.5)
    base = size_gmid(parsed, sr, fbr, topo, tech, spec)

    # Retune only the tail current source to a weaker gm/Id (higher Vdsat).
    bi = dict(DEFAULT_BLOCK_INTENTS)
    bi["tail_current"] = dataclasses.replace(bi["tail_current"], gm_id=6.0)
    tuned = size_gmid(parsed, sr, fbr, topo, tech, spec, GmIdIntent(block_intents=bi))

    t0, t1 = base.transistors["m1_tail_current"], tuned.transistors["m1_tail_current"]
    assert t1.vds_sat_v > t0.vds_sat_v          # lower gm/Id → larger Vdsat
    assert t1.w_um != t0.w_um                    # geometry changed
    # The input pair (a different block) is untouched.
    assert (tuned.transistors["m1_input_pair"].w_um
            == base.transistors["m1_input_pair"].w_um)
