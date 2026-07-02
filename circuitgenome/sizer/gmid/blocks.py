"""Functional-block view of a recognised op-amp, for the gm/Id sizer.

Groups the FBR slot devices into typed blocks (input pair, first-stage load,
gain stages, tail, bias, compensation) and classifies the *kind* of each load /
tail (current-mirror, cascode, resistor, plain current-source).  This is the
structural layer beneath the Analyze phase (:mod:`.analyze`): the load kind
drives the first-stage gain factor, the cascode detection feeds the DC
headroom budget (:mod:`.bias`), and :func:`node_rout` gives the evaluation
phase its cascode-aware output resistance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from circuitgenome.synthesizer.models import Device

from ..shared.taxonomy import (
    RAILS,
    SECOND_STAGE_SLOTS,
    THIRD_STAGE_SLOTS,
    is_signal_device,
)


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


def cascode_device_refs(slot_transistors: dict[str, list[Device]]) -> set[str]:
    """Refs of cascode devices: a non-signal device whose *source* sits on
    another same-slot device's *drain* (i.e. it is stacked on a device below)."""
    out: set[str] = set()
    for devs in slot_transistors.values():
        mos = [d for d in devs if d.type in ("nmos", "pmos")]
        drains = {d.terminals.get("d") for d in mos}
        for d in mos:
            src = d.terminals.get("s")
            if src not in RAILS and src in drains and not is_signal_device(d):
                out.add(d.ref)
    return out


# --------------------------------------------------------------------------- #
# Cascode-aware output resistance
# --------------------------------------------------------------------------- #
def _looking_in_drain(device, by_drain, model, sizing, stop) -> float:
    """Resistance (Ω) looking into ``device``'s drain, cascode-aware.

    A cascode device (source on another device's drain) boosts its own ``ro`` by
    ``1 + gm·R_source`` where ``R_source`` is the resistance below it; a device
    whose source is a rail or in ``stop`` (e.g. the input-pair tail node, an AC
    ground for the differential half-circuit) contributes just ``ro``.  Shallow
    recursion handles multi-high stacks.
    """
    s = sizing.get(device.ref)
    if s is None:
        return float("inf")
    gds = model.gds(device.type, s.w_um, s.l_um, s.ids_a)
    ro = 1.0 / gds if gds > 0 else float("inf")
    src = device.terminals.get("s")
    if src in RAILS or src in stop or src is None:
        return ro
    below = by_drain.get(src)
    if below is not None and below.ref != device.ref and below.type == device.type:
        gm = model.gm(device.type, s.w_um, s.l_um, s.ids_a)
        r_src = _looking_in_drain(below, by_drain, model, sizing, stop)
        return ro * (1.0 + gm * r_src) if r_src != float("inf") else float("inf")
    return ro


def node_rout(out_net: str, mosfets: list[Device], model, sizing,
              stop: frozenset = frozenset()) -> float:
    """Cascode-aware output resistance (Ω) at ``out_net`` = parallel of every
    device whose drain is ``out_net`` (each looking-in, cascode-boosted).

    ``stop`` lists nets to treat as AC ground (typically the input-pair tail node)
    so the input pair contributes ``ro``, not a tail-degenerated cascode.
    """
    by_drain: dict[str, Device] = {}
    for d in mosfets:
        if d.type in ("nmos", "pmos"):
            by_drain.setdefault(d.terminals.get("d"), d)
    g = 0.0
    for d in mosfets:
        if d.type in ("nmos", "pmos") and d.terminals.get("d") == out_net:
            r = _looking_in_drain(d, by_drain, model, sizing, stop)
            if r > 0:
                g += 1.0 / r
    return 1.0 / g if g > 0 else float("inf")


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
        return next((d for d in self.mosfets if is_signal_device(d)), None)

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

    def has_cascode_load(self) -> bool:
        ld = self.load
        return bool(ld and ld.load_kind == LoadKind.CASCODE)

    def first_stage_out_net(self) -> str | None:
        """First-stage output node = the next stage's signal-device gate.

        ``None`` for a one-stage opamp (no downstream stage to read it from).
        """
        for slot in ("second_stage", "second_stage_p", "second_stage_n",
                     "third_stage", "third_stage_p", "third_stage_n"):
            b = self.blocks.get(slot)
            sig = b.signal_device if b else None
            if sig is not None:
                return sig.terminals.get("g")
        return None

    def tail_net(self) -> str | None:
        """The input-pair source (tail) node."""
        ip = self.input_pair
        return ip.mosfets[0].terminals.get("s") if ip and ip.mosfets else None


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
    has_2 = any(s in slot_transistors for s in SECOND_STAGE_SLOTS)
    has_3 = any(s in slot_transistors for s in THIRD_STAGE_SLOTS)
    n_stages = 1 + (1 if has_2 else 0) + (1 if has_3 else 0)
    return OpAmpBlocks(blocks=blocks, is_fully_differential=fd, n_stages=n_stages)
