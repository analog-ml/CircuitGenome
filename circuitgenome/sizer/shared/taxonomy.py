"""Template taxonomy: the slot- and net-naming conventions the sizer assumes.

Single source of truth for every naming assumption the sizer makes about a
circuit template — which FBR slot names exist, how they map onto bias-current
groups, and which net names are supply/bias rails.  A new topology whose slots
follow these conventions needs **no sizer changes**; a topology that introduces
new slot names or bias-net conventions is supported by extending the groups
here (and only here).

Shared by the Level-1 analytical sizer and the gm/Id pipeline.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

# Slots that carry iBias/2 per transistor (both sides of the differential pair).
HALF_BIAS_SLOTS = frozenset({"input_pair", "load"})
# Slots whose transistors each carry the full iBias.
FULL_BIAS_SLOTS = frozenset({"tail_current", "bias_gen"})
# All second-stage slot names (SE: "second_stage"; FD: "second_stage_p"/"second_stage_n").
SECOND_STAGE_SLOTS = frozenset({"second_stage", "second_stage_p", "second_stage_n"})
# All third-stage slot names (SE: "third_stage"; FD: "third_stage_p"/"third_stage_n").
THIRD_STAGE_SLOTS = frozenset({"third_stage", "third_stage_p", "third_stage_n"})
# All gain-stage slot names (used by the topology-mismatch guard).
STAGE_SLOTS = SECOND_STAGE_SLOTS | THIRD_STAGE_SLOTS

# External supply / bias net names — gate connected to these → current-source load.
BIAS_NETS = frozenset({"vdd!", "vss!", "gnd!", "ibias"})
# Supply-rail net names (AC grounds for output-resistance walks).
RAILS = frozenset({"vdd!", "vss!", "gnd!", "0"})


def is_signal_device(device: Device) -> bool:
    """True if ``device``'s gate is driven by a signal net (not a bias rail).

    The signal transistor of a gain stage is the one whose gate is the previous
    stage's output; the partner device is a current-source load (gate on a bias
    net). Used to pick the gm-contributing device regardless of NMOS/PMOS polarity.
    """
    gate = device.terminals.get("g", "")
    return bool(gate) and gate not in BIAS_NETS and not gate.startswith("net_bias")
