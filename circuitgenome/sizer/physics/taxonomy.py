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
SECOND_STAGE_SLOTS = frozenset({
    "second_stage", "second_stage_p", "second_stage_n", "class_ab_stage",
})
# All third-stage slot names (SE: "third_stage"; FD: "third_stage_p"/"third_stage_n").
THIRD_STAGE_SLOTS = frozenset({"third_stage", "third_stage_p", "third_stage_n"})
# Output-stage slots. The ordinary output_stage* names are source followers and
# remain outside STAGE_SLOTS; class_ab_stage is deliberately also a second-stage
# slot because its complementary common-source pair has voltage gain.
OUTPUT_STAGE_SLOTS = frozenset({
    "output_stage", "output_stage_p", "output_stage_n", "class_ab_stage",
})
# All gain-stage slot names (class_ab_stage enters through SECOND_STAGE_SLOTS).
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

    ``*_pref`` and ``*_ncasc`` are the constructed bias generator's internal
    reference gates (``pref``/``ncasc`` in ``synthesizer/config/bias_legs.yaml``,
    slot-prefixed to ``bias_gen_pref``/``bias_gen_ncasc`` on assembly) — the
    PMOS-side mirror reference and the pref branch's wide-swing cascode
    level; devices gated by them are bias devices, not signal devices.
    """
    gate = device.terminals.get("g", "")
    return (bool(gate) and gate not in BIAS_NETS
            and not gate.startswith("net_bias") and not gate.endswith("_pref")
            and not gate.endswith("_ncasc"))


def diode_bias_nets(devices: list[Device]) -> set[str]:
    """Gate nets established by a diode-connected MOS reference."""
    return {
        d.terminals.get("g", "")
        for d in devices
        if d.type in ("nmos", "pmos")
        and d.terminals.get("g")
        and d.terminals.get("g") == d.terminals.get("d")
    }


def signal_devices(devices: list[Device]) -> list[Device]:
    """Signal-gated devices, excluding locally diode-generated bias nets.

    The flat-netlist taxonomy cannot identify an internal mirror gate from its
    spelling alone.  Treating every diode-connected gate net as bias fixes that
    ambiguity for self-biased macros while preserving ordinary gain devices.
    """
    bias_nets = diode_bias_nets(devices)
    return [
        d for d in devices
        if is_signal_device(d) and d.terminals.get("g") not in bias_nets
    ]


def is_complementary_input_stage(devices: list[Device]) -> bool:
    """True for a parallel NMOS+PMOS differential input stage."""
    sig = signal_devices(devices)
    return (sum(d.type == "nmos" for d in sig) >= 2
            and sum(d.type == "pmos" for d in sig) >= 2)


def complementary_output_pair(devices: list[Device]) -> list[Device]:
    """Return a rail-fed complementary follower or common-source output pair."""
    mos = [d for d in devices if d.type in ("nmos", "pmos")]
    for n in (d for d in mos if d.type == "nmos"):
        for p in (d for d in mos if d.type == "pmos"):
            follower = (n.terminals.get("s") == p.terminals.get("s")
                        and n.terminals.get("d") in RAILS
                        and p.terminals.get("d") in RAILS)
            push_pull = (n.terminals.get("d") == p.terminals.get("d")
                         and n.terminals.get("s") in RAILS
                         and p.terminals.get("s") in RAILS)
            if follower or push_pull:
                return [n, p]
    return []
