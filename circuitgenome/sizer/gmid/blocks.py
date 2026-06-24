"""Functional-block view of a recognised op-amp, for the gm/Id sizer.

Groups the FBR slot devices into typed blocks (input pair, first-stage load,
gain stages, tail, bias, compensation) and classifies the *kind* of each load /
tail (current-mirror, cascode, resistor, plain current-source).  This is the
structural layer the gm/Id pipeline organises sizing and the DC-operating-point
check around, and the extension point for cascode / resistor / CMFB handling in
later phases.

Phase 1 uses it for classification (load kind → first-stage gain factor, cascode
detection for the headroom check); the per-block self-sizing is layered on top in
subsequent phases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from circuitgenome.synthesizer.models import Device

# Slot-name groups (mirror the constants in sizer.py so this stays standalone).
_SECOND_STAGE_SLOTS = frozenset({"second_stage", "second_stage_p", "second_stage_n"})
_THIRD_STAGE_SLOTS = frozenset({"third_stage", "third_stage_p", "third_stage_n"})
_BIAS_NETS = frozenset({"vdd!", "vss!", "gnd!", "ibias"})


def _is_signal_dev(device: Device) -> bool:
    """True if the device's gate is a signal net (not a bias rail)."""
    gate = device.terminals.get("g", "")
    return bool(gate) and gate not in _BIAS_NETS and not gate.startswith("net_bias")


class LoadKind(Enum):
    """How a stage load converts/combines current (sets gain & headroom)."""
    MIRROR = "mirror"               # active current-mirror (diode-connected member)
    CASCODE = "cascode"             # series-stacked devices
    RESISTOR = "resistor"           # resistor load
    CURRENT_SOURCE = "current_source"  # plain (non-mirror) current-source load
    NONE = "none"


def _has_diode(devs: list[Device]) -> bool:
    return any(d.type in ("nmos", "pmos") and d.terminals.get("g") == d.terminals.get("d")
               for d in devs)


def _has_cascode(devs: list[Device]) -> bool:
    """True if any two same-type devices are series-stacked (source==drain)."""
    mos = [d for d in devs if d.type in ("nmos", "pmos")]
    drains = {d.terminals.get("d") for d in mos}
    return any(d.terminals.get("s") in drains for d in mos)


def classify_load(mosfets: list[Device], resistors: list[Device]) -> LoadKind:
    """Classify a load slot by its devices."""
    if _has_cascode(mosfets):
        return LoadKind.CASCODE
    if _has_diode(mosfets):
        return LoadKind.MIRROR
    if resistors:
        return LoadKind.RESISTOR
    if mosfets:
        return LoadKind.CURRENT_SOURCE
    return LoadKind.NONE


@dataclass
class Block:
    """A functional group of devices from one or more FBR slots."""
    name: str                        # "input_pair", "load", "second_stage", ...
    mosfets: list[Device] = field(default_factory=list)
    resistors: list[Device] = field(default_factory=list)
    load_kind: LoadKind = LoadKind.NONE

    @property
    def signal_device(self) -> Device | None:
        """The gm-contributing device (gate on a signal net), if any."""
        return next((d for d in self.mosfets if _is_signal_dev(d)), None)

    @property
    def is_cascode(self) -> bool:
        return self.load_kind == LoadKind.CASCODE or _has_cascode(self.mosfets)


@dataclass
class OpAmpBlocks:
    """The block decomposition of a recognised op-amp."""
    blocks: dict[str, Block]
    is_fully_differential: bool
    n_stages: int

    @property
    def input_pair(self) -> Block | None:
        return self.blocks.get("input_pair")

    @property
    def load(self) -> Block | None:
        return self.blocks.get("load")

    @property
    def tail(self) -> Block | None:
        return self.blocks.get("tail_current")

    def first_stage_gain_factor(self) -> float:
        """``k_fs``: 0.5 for a single-ended non-mirror first-stage load, else 1.0."""
        if self.is_fully_differential:
            return 1.0
        ld = self.load
        if ld and ld.load_kind in (LoadKind.MIRROR, LoadKind.CASCODE):
            return 1.0
        return 0.5


def build_blocks(slot_transistors: dict[str, list[Device]],
                 slot_resistors: dict[str, list[Device]]) -> OpAmpBlocks:
    """Build the :class:`OpAmpBlocks` view from the FBR slot device lists."""
    blocks: dict[str, Block] = {}
    for slot, mosfets in slot_transistors.items():
        rs = slot_resistors.get(slot, [])
        kind = classify_load(mosfets, rs) if slot in ("load",) else LoadKind.NONE
        blocks[slot] = Block(name=slot, mosfets=list(mosfets), resistors=list(rs),
                             load_kind=kind)
    # Resistor-only slots (e.g. a resistor load with no mosfets) still need a block.
    for slot, rs in slot_resistors.items():
        if slot not in blocks:
            blocks[slot] = Block(name=slot, resistors=list(rs),
                                 load_kind=classify_load([], rs) if slot == "load"
                                 else LoadKind.NONE)
    fd = any(s in slot_transistors for s in ("second_stage_p", "second_stage_n",
                                             "third_stage_p", "third_stage_n"))
    has_2 = any(s in slot_transistors for s in _SECOND_STAGE_SLOTS)
    has_3 = any(s in slot_transistors for s in _THIRD_STAGE_SLOTS)
    n_stages = 1 + (1 if has_2 else 0) + (1 if has_3 else 0)
    return OpAmpBlocks(blocks=blocks, is_fully_differential=fd, n_stages=n_stages)
