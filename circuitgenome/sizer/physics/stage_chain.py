"""The small-signal chain a solved sizing presents to metric evaluation.

Between "here is a circuit with W/L on every device" and "here is its gain and
phase margin" sits one small vocabulary: per-stage transconductance and output
resistance, plus the handful of node conductances CMRR/PSRR need.  This module
owns that vocabulary (:class:`StageChain`) and the extraction that produces it
from a solved sizing (:func:`build_stage_chain`), so
:mod:`~circuitgenome.sizer.physics.metrics` can be pure algebra over the chain.

Output resistances come from :func:`node_rout`, a cascode-aware walk of the
device graph: a cascode device boosts the resistance below it by ``1 + gm·R``,
which a per-device ``gds`` sum cannot see.  It reads only ``model.gm`` and
``model.gds``, so both the Level-1 and gm/Id backends get the same treatment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from circuitgenome.synthesizer.models import Device

from .circuit_view import CircuitView
from .device_model import DeviceModel
from ..models import SizingSpec, TransistorSizing
from .preprocess import _first_stage_gain_factor
from .taxonomy import RAILS, SECOND_STAGE_SLOTS, THIRD_STAGE_SLOTS, is_signal_device


# --------------------------------------------------------------------------- #
# Cascode-aware output resistance
# --------------------------------------------------------------------------- #
def _by_drain(mosfets: list[Device]) -> dict[str, Device]:
    """Net -> the MOSFET whose drain sits on it (first wins, for the walk)."""
    by_drain: dict[str, Device] = {}
    for d in mosfets:
        if d.type in ("nmos", "pmos"):
            by_drain.setdefault(d.terminals.get("d"), d)
    return by_drain


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
    by_drain = _by_drain(mosfets)
    g = 0.0
    for d in mosfets:
        if d.type in ("nmos", "pmos") and d.terminals.get("d") == out_net:
            r = _looking_in_drain(d, by_drain, model, sizing, stop)
            if r > 0:
                g += 1.0 / r
    return 1.0 / g if g > 0 else float("inf")


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Stage:
    """One gain stage's small-signal contribution.

    :param gm: signal-device transconductance in A/V, gm-ceiling clamped.
    :param rout: output resistance in Ω at the stage's output node.
    """
    gm: float
    rout: float


@dataclass(frozen=True)
class StageChain:
    """Everything metric evaluation needs from a solved sizing.

    :param stages: one :class:`Stage` per *gain* stage, input pair first (a
        follower ``output_stage`` has A ≈ 1 and is deliberately absent).
    :param k_fs: first-stage gain/transconductance factor — ``0.5`` for a
        single-ended tap without a mirror load, else ``1.0``.  Applies to the
        first stage's gain and to its role in GBW/PM, **not** to the raw ``gm``
        CMRR uses.
    :param gd_tail: tail-source output conductance in A/V (CMRR).
    :param gd_output_load: output conductance in A/V of the load branch on the
        output-driving stage's output node (PSRR) — the second stage's load on
        a multi-stage chain, the first stage's own load on a single-stage one.
        Cascode-aware on a single stage, and ``1/R`` for a resistor load, so
        every load family reports it (issue #228).
    :param mirror_pole_hz: current-mirror node pole in Hz, ``None`` when the
        chain has no diode-connected mirror load or the tech supplies no
        ``cox`` — see :func:`_mirror_pole_hz` for the two single-stage load
        families that deliberately land there.  The first non-dominant pole of
        a **load**-compensated single-stage OTA, so it sets that topology's
        phase margin; a Miller-compensated chain has its own non-dominant pole
        at the output and ignores this one.
    :param cc_pf: Miller compensation cap, ``None`` when uncompensated.
    :param cc2_pf: second compensation cap (three-stage), ``None`` otherwise.
    :param supply_currents: per-branch quiescent currents in A (power).
    :param swing_vdsat: ``(vdsat_pmos, vdsat_nmos)`` of the second stage in V,
        either entry ``None`` when that polarity is absent (output swing).
    :param gain_measurable: ``False`` when the DC operating point the
        small-signal formulas sit on does not exist, so every gain-derived
        metric is withheld rather than reported optimistically (issue #148).
    """
    stages: tuple[Stage, ...]
    k_fs: float = 1.0
    gd_tail: float = 0.0
    gd_output_load: float = 0.0
    mirror_pole_hz: float | None = None
    cc_pf: float | None = None
    cc2_pf: float | None = None
    supply_currents: tuple[float, ...] = ()
    swing_vdsat: tuple[float | None, float | None] = (None, None)
    gain_measurable: bool = True

    def withheld(self) -> StageChain:
        """Mark the small-signal operating point as non-existent (issue #148)."""
        return replace(self, gain_measurable=False)

    def with_first_stage_gm(self, factor: float) -> StageChain:
        """Scale the input pair's ``gm`` (source degeneration: ``1/(1+gm·R)``)."""
        if not self.stages or factor == 1.0:
            return self
        head = replace(self.stages[0], gm=self.stages[0].gm * factor)
        return replace(self, stages=(head,) + self.stages[1:])

    def with_tail_conductance(self, gd_tail: float) -> StageChain:
        """Set the tail conductance a non-MOSFET tail contributes (CMRR).

        Only takes effect when the device walk found no tail stack: a resistor
        tail and a transistor tail are mutually exclusive.
        """
        if self.gd_tail > 0.0 or gd_tail <= 0.0:
            return self
        return replace(self, gd_tail=gd_tail)

    def with_output_loading(self, gd_extra: float) -> StageChain:
        """Add ``gd_extra`` A/V at the load-driving stage's output node.

        A resistive-sense CMFB averager loads the fully-differential output;
        ``rout`` of the last gain stage drops accordingly.
        """
        if not self.stages or gd_extra <= 0.0:
            return self
        last = self.stages[-1]
        if last.rout == float("inf"):
            return self
        loaded = replace(last, rout=1.0 / (1.0 / last.rout + gd_extra))
        return replace(self, stages=self.stages[:-1] + (loaded,))


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
# Membership lives in taxonomy; this only fixes the order in which a
# representative device is picked (the SE name before the FD legs).
_STAGE_SLOT_GROUPS = (
    ("second_stage", "second_stage_p", "second_stage_n"),
    ("third_stage", "third_stage_p", "third_stage_n"),
)
assert set(_STAGE_SLOT_GROUPS[0]) == SECOND_STAGE_SLOTS
assert set(_STAGE_SLOT_GROUPS[1]) == THIRD_STAGE_SLOTS


def _first_present(slot_transistors: dict[str, list[Device]],
                   slots: tuple[str, ...]) -> list[Device]:
    """Devices of the first present slot (SE name before the FD legs)."""
    for name in slots:
        if name in slot_transistors:
            return slot_transistors[name]
    return []


def _signal_path_nets(ip_devs: list[Device],
                      mosfets: list[Device]) -> frozenset[str]:
    """Every net the input pair drives, walking up through cascodes above it.

    Starts at the pair's own drains and repeatedly adds the drain of any MOSFET
    *sourcing* from a net already in the set -- which is exactly what a cascode
    device does.  The result is the signal path from the pair to wherever it
    ends, and it is the one structural fact that separates the two branches
    meeting on a single stage's output node: the branch that came up from the
    pair, and the branch that came from a rail (issue #228).
    """
    nets = {d.terminals.get("d") for d in ip_devs} - {None}
    for _ in range(len(mosfets)):          # bounded: each pass adds ≥1 net
        grown = {d.terminals.get("d") for d in mosfets
                 if d.terminals.get("s") in nets} - {None}
        if grown <= nets:
            break
        nets |= grown
    return frozenset(nets)


def _single_ended_output_net(load_devs: list[Device], signal_nets: frozenset[str],
                             mosfets: list[Device]) -> str | None:
    """The net a single-stage load presents as the amplifier output, or ``None``.

    The output is where the input pair's signal path *ends*: a net in
    ``signal_nets`` that no further device sources from, that gates nothing,
    and whose devices are not diode-connected.  The last two exclusions drop
    the mirror reference node, which also terminates a signal path -- on a
    telescopic or folded cascode both legs terminate, and only one of them is
    the output (issue #228).

    ``None`` when the rule does not single one out.  That happens exactly for a
    **resistor** load: with no load device there is no diode leg to exclude, so
    both of the pair's drains terminate identically.  They are also
    interchangeable -- the two halves are symmetric -- so the caller's fallback
    to the pair's own drain is the right answer, and returning ``None`` keeps
    it the documented one instead of a coin flip between two equal nets.
    """
    gates = {d.terminals.get("g") for d in mosfets}
    sources = {d.terminals.get("s") for d in mosfets}
    diodes = {d.terminals.get("d") for d in load_devs
              if d.terminals.get("g") and d.terminals.get("g") == d.terminals.get("d")}
    ends = [n for n in signal_nets
            if n not in sources and n not in gates and n not in diodes]
    return ends[0] if len(ends) == 1 else None


def _mirror_pole_hz(load_devs: list[Device], mosfets: list[Device],
                   model, sizing) -> float | None:
    """Current-mirror node pole in Hz, or ``None`` when there is none.

    A diode-connected load device pins its own node at ``1/gm``; the
    capacitance that node drives is the gate capacitance of every device it
    gates -- itself and the mirror devices copying it -- so the pole sits at
    ``gm/(2π·ΣCgs)``.  This is the first non-dominant pole of a
    load-compensated single-stage OTA (issue #221).

    ``None`` when the load has no diode-connected device, when that device is
    unsized, or when the technology supplies no ``cox`` for ``Cgs`` -- each a
    case where the pole genuinely cannot be placed, and the caller withholds
    phase margin rather than inventing one.

    Two single-stage load families fall in that hole, and the omission is
    deliberate in both (issue #228):

    * **Resistor loads** have no internal node at all.  In this model such a
      stage is genuinely single-pole, so the phase margin is exactly 90° for
      *every* sizing -- a statement about the model, not about the design, and
      one that would pass any ``phase_margin_min_deg`` a spec could set.
    * **Wide-swing telescopic loads** bias their cascode gates from a level
      rail instead of diode-connecting them.  A non-dominant pole does exist
      there, at the cascode *source* node (``1/gm_cascode`` against that node's
      capacitance), but the sizer models only ``Cgs`` -- and at a cascode
      source the junction capacitance of the current source below it is the
      larger term.  The pole can be bounded, not placed, so it is not reported.

    Neither is "no non-dominant pole"; both are "no pole this model can place".
    """
    diode = next((d for d in load_devs
                  if d.terminals.get("g")
                  and d.terminals.get("g") == d.terminals.get("d")), None)
    if diode is None:
        return None
    s = sizing.get(diode.ref)
    if s is None:
        return None
    gm = model.gm(diode.type, s.w_um, s.l_um, s.ids_a)
    net = diode.terminals["g"]
    c_f = 0.0
    for d in mosfets:
        sd = sizing.get(d.ref)
        if sd is not None and d.terminals.get("g") == net:
            c_f += model.cgs(d.type, sd.w_um, sd.l_um)
    if gm <= 0.0 or c_f <= 0.0:
        return None
    return gm / (2.0 * math.pi * c_f)


def build_stage_chain(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
    *,
    cc_pf: float | None = None,
    cc2_pf: float | None = None,
    gd_load_r: float = 0.0,
) -> StageChain:
    """Extract the :class:`StageChain` from a solved sizing.

    ``gd_load_r`` is the first-stage load resistor's conductance in A/V; it
    loads the first stage's output node, which ``node_rout`` — a walk over
    MOSFETs — cannot see.

    The input pair's source is the tail node, treated as an AC ground so the
    pair contributes ``ro`` rather than a tail-degenerated cascode.  The first
    stage's output is the *next* stage's signal gate, not the pair's drain:
    on a folded cascode those are different nets.  With no next stage the
    output node is resolved structurally instead — again not the pair's drain,
    for the same reason (issue #228).
    """
    slot_transistors = view.slot_transistors
    mosfets = [d for d, _slot in view.all_transistors.values()]
    ip_devs = slot_transistors.get("input_pair", [])
    tail_net = ip_devs[0].terminals.get("s") if ip_devs else None
    stop = frozenset({tail_net}) if tail_net else frozenset()

    def _gm(d: Device) -> float:
        s = sizing.get(d.ref)
        if s is None:
            return 0.0
        return min(model.gm(d.type, s.w_um, s.l_um, s.ids_a),
                   model.gm_ceiling(d.type, s.ids_a, s.l_um))

    def _rout(net: str | None, extra_gd: float = 0.0) -> float:
        if not net:
            return float("inf")
        r = node_rout(net, mosfets, model, sizing, stop)
        g = (1.0 / r if r != float("inf") else 0.0) + extra_gd
        return 1.0 / g if g > 0 else float("inf")

    # Gain slots present, in order; a three-stage always has a second stage.
    slot_devs: list[list[Device]] = []
    for slots in _STAGE_SLOT_GROUPS:
        devs = _first_present(slot_transistors, slots)
        if not devs:
            break
        slot_devs.append(devs)
    signal_devs = [next((d for d in devs if is_signal_device(d)), None)
                   for devs in slot_devs]

    # --- Stage 1: input pair into the first-stage output node ---
    out1 = next((d.terminals.get("g") for d in signal_devs if d is not None), None)
    signal_nets = frozenset()
    if out1 is None and ip_devs:
        # One-stage: the amplifier's output node is where the pair's signal
        # path ends.  On a mirror or resistor load that is the pair's own drain
        # (the fallback); on a folded or telescopic cascode load it is one
        # cascode further up, and measuring at the pair's drain would report
        # the cascode *source* -- a low-impedance node -- as the output (#228).
        signal_nets = _signal_path_nets(ip_devs, mosfets)
        out1 = (_single_ended_output_net(slot_transistors.get("load", []),
                                         signal_nets, mosfets)
                or ip_devs[0].terminals.get("d"))
    stages = [Stage(gm=_gm(ip_devs[0]) if ip_devs else 0.0,
                    rout=_rout(out1, gd_load_r))]

    # --- Stages 2 and 3: each numbered gain slot's signal device ---
    for sig in signal_devs:
        stages.append(Stage(gm=_gm(sig) if sig is not None else 0.0,
                            rout=_rout(sig.terminals.get("d") if sig else None)))

    # --- Output-stage load conductance (PSRR) ---
    # Multi-stage: the second stage's current-source load, whose gds is the
    # path supply ripple takes to the output node.  Single-stage: the output
    # node *is* the first stage's, so the same role is played by the load slot
    # -- where the mirror is the load, and both its devices are gate-driven, so
    # the not-a-signal-device test that picks a second-stage load finds nothing
    # and the device on the output node is taken instead (issue #221).
    #
    # Two devices meet on a single stage's output node, and only one of them is
    # the load: the other came up from the input pair (the cascode above it on
    # a telescopic or folded load), so it is excluded by ``signal_nets``.  The
    # load's conductance is read cascode-aware rather than as a bare ``gds`` --
    # a stacked load presents ``1/(ro·(1+gm·R))`` to the rail, and cascoding a
    # load improves supply rejection rather than, as a bare top-device ``gds``
    # would say, degrading it.  A load with no stack reduces to ``gds``
    # unchanged (issue #228).
    gd_output_load = 0.0
    if slot_devs:
        for d in slot_devs[0]:
            s = sizing.get(d.ref)
            if s is not None and not is_signal_device(d):
                gd_output_load = model.gds(d.type, s.w_um, s.l_um, s.ids_a)
    else:
        by_drain = _by_drain(mosfets)
        for d in slot_transistors.get("load", []):
            if (sizing.get(d.ref) is None or d.terminals.get("d") != out1
                    or d.terminals.get("s") in signal_nets):
                continue
            r = _looking_in_drain(d, by_drain, model, sizing, stop)
            gd_output_load = 1.0 / r if 0.0 < r < float("inf") else 0.0
        # A resistor load has no device to read: the output-node conductance is
        # exactly 1/R, which the sizer already solved for and passed in here.
        if gd_output_load == 0.0:
            gd_output_load = gd_load_r

    # --- Tail conductance (CMRR): the full stack down to the rail ---
    gd_tail = 0.0
    if slot_transistors.get("tail_current") and tail_net:
        r_tail = node_rout(tail_net, mosfets, model, sizing, frozenset())
        gd_tail = 1.0 / r_tail if r_tail and r_tail != float("inf") else 0.0

    # --- Output swing: the second stage's Vdsat per polarity ---
    ss_devs = slot_devs[0] if slot_devs else []

    def _vdsat(dtype: str) -> float | None:
        d = next((d for d in ss_devs if d.type == dtype), None)
        s = sizing.get(d.ref) if d is not None else None
        return s.vds_sat_v if s is not None else None

    # --- Power: tail + each gain-stage branch + the bias generator ---
    n_bias = len([d for d in slot_transistors.get("bias_gen", [])
                  if d.type in ("nmos", "pmos")])
    supply = [spec.ibias]
    if len(stages) > 1:
        n_ss = sum(1 for s in SECOND_STAGE_SLOTS if s in slot_transistors)
        supply.append(spec.ibias * spec.second_stage_current_ratio * n_ss)
    if len(stages) > 2:
        n_ts = sum(1 for s in THIRD_STAGE_SLOTS if s in slot_transistors)
        supply.append(spec.ibias * spec.third_stage_current_ratio * n_ts)
    supply.append(spec.ibias * max(n_bias, 1))

    return StageChain(
        stages=tuple(stages),
        k_fs=_first_stage_gain_factor(slot_transistors),
        gd_tail=gd_tail,
        gd_output_load=gd_output_load,
        mirror_pole_hz=_mirror_pole_hz(
            slot_transistors.get("load", []), mosfets, model, sizing),
        cc_pf=cc_pf,
        cc2_pf=cc2_pf,
        supply_currents=tuple(supply),
        swing_vdsat=(_vdsat("pmos"), _vdsat("nmos")),
    )
