"""
This file holds every naming assumption the sizer makes:
    - which slot names exist
    - which bias-current **group** each belongs to
    - and which net names are rails or bias nets.

**Design knowledge the file encodes.**

- KCL: the two sides of a differential pair each carry half the tail current, so the pair and its load form a "half bias" group.  A tail current source is a "full bias" group.
- Stages and fully differential halves (_p / _n slot suffixes).
- Source follower: gain ≈ 1, so it is not a gain stage.
- Rails are AC ground: a small wiggle cannot move a fixed supply.
- Bias generator nets: a transistor gated by one of them is a current source, not a signal device.

Shared by the square-law analytical sizer and the gm/Id pipeline.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

# KCL
# -----------
# Slots that carry iBias/2 per transistor (both sides of the differential pair).
HALF_BIAS_SLOTS = frozenset({"input_pair", "load"})
# Slots whose transistors each carry the full iBias.
FULL_BIAS_SLOTS = frozenset({"tail_current", "bias_gen"})


# Stages
# -----------
# All second-stage slot names (SE: "second_stage"; FD: "second_stage_p"/"second_stage_n").
SECOND_STAGE_SLOTS = frozenset({"second_stage", "second_stage_p", "second_stage_n"})
# All third-stage slot names (SE: "third_stage"; FD: "third_stage_p"/"third_stage_n").
THIRD_STAGE_SLOTS = frozenset({"third_stage", "third_stage_p", "third_stage_n"})
STAGE_SLOTS = SECOND_STAGE_SLOTS | THIRD_STAGE_SLOTS


# Source follower stage is not a gain stage, so it is not included in STAGE_SLOTS.
OUTPUT_STAGE_SLOTS = frozenset({"output_stage", "output_stage_p", "output_stage_n"})

# Rails and AC grounds
# -----------
# External supply / bias net names — gate connected to these → current-source load.
BIAS_NETS = frozenset({"vdd!", "vss!", "gnd!", "ibias"})
# Supply-rail net names (AC grounds for output-resistance walks).
RAILS = frozenset({"vdd!", "vss!", "gnd!", "0"})


def is_signal_device(device: Device) -> bool:
    """
    True if ``device``'s gate is driven by a signal net (the previous stage's output), not a bias net.
    """
    # ``*_pref`` and ``*_ncasc`` are the constructed bias generator's internal
    # reference gates (``pref``/``ncasc`` in ``synthesizer/config/bias_legs.yaml``)

    gate = device.terminals.get("g", "")
    return (bool(gate) and gate not in BIAS_NETS
            and not gate.startswith("net_bias") and not gate.endswith("_pref")
            and not gate.endswith("_ncasc"))
