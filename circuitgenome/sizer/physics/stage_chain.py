"""The small-signal chain a solved sizing presents to metric evaluation.

Between "here is a circuit with W/L on every device" and "here is its gain and
phase margin" sits one small vocabulary: per-stage transconductance and output
resistance, plus the handful of node conductances CMRR/PSRR need.  This module
owns that vocabulary (:class:`StageChain`) and the extraction that produces it
from a solved sizing (:func:`build_stage_chain`), so
:mod:`~circuitgenome.sizer.physics.metrics` can be pure algebra over the chain.

Output resistances come from :func:`node_rout`, a cascode-aware walk of the
device graph: a cascode device boosts the resistance below it by ``1 + gm·R``,
which a per-device ``gds`` sum cannot see -- as does a source-degeneration
resistor, which is the same effect with a resistor in place of the device.  It
reads only ``model.gm``, ``model.gds`` and ``model.gmb``, so both the Level-1
and gm/Id backends get the same treatment.  Given quiescent node levels
(:mod:`~.node_levels`), each device is read at its own ``Vds``/``Vsb``
(issue #273).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from circuitgenome.synthesizer.models import Device

from .circuit_view import CircuitView
from .device_model import DeviceModel
from ..models import SizingSpec, TransistorSizing
from .node_levels import device_bias, estimate_node_levels
from .preprocess import _first_stage_gain_factor, _mirror_reference
from .rnmc import RNMC
from .taxonomy import (
    OUTPUT_STAGE_SLOTS, RAILS, SECOND_STAGE_SLOTS, THIRD_STAGE_SLOTS, is_signal_device)


# --------------------------------------------------------------------------- #
# Cascode-aware output resistance
# --------------------------------------------------------------------------- #
def _by_drain(mosfets: list[Device]) -> dict[str, list[Device]]:
    """Net -> every MOSFET whose drain sits on it."""
    by_drain: dict[str, list[Device]] = {}
    for d in mosfets:
        if d.type in ("nmos", "pmos"):
            by_drain.setdefault(d.terminals.get("d"), []).append(d)
    return by_drain


def _parallel_rout(devices, by_drain, model, sizing, stop, degen, levels) -> float:
    """Parallel combination (Ω) of each device's looking-in drain resistance."""
    g = 0.0
    for d in devices:
        r = _looking_in_drain(d, by_drain, model, sizing, stop, degen, levels)
        if r > 0:
            g += 1.0 / r
    return 1.0 / g if g > 0 else float("inf")


def _looking_in_drain(device, by_drain, model, sizing, stop, degen,
                      levels=None) -> float:
    """Resistance (Ω) looking into ``device``'s drain, cascode-aware.

    A cascode device (source on another device's drain) boosts its own ``ro`` by
    ``1 + gm·R_source`` where ``R_source`` is the resistance below it; a device
    whose source is a rail or in ``stop`` (e.g. the input-pair tail node, an AC
    ground for the differential half-circuit) contributes just ``ro``.  Shallow
    recursion handles multi-high stacks.

    ``R_source`` is every device draining onto the source net, in parallel,
    whatever its type.  On a folded cascode that net is the folding node, where
    the input pair and the current source below the cascode both drain, so
    ``R_source = ro_pair ∥ ro_source``.  Taking only the first drain there found
    the opposite-type pair and dropped the boost entirely (issue #240).

    ``degen`` maps a net to the series resistance between it and AC ground --
    a source-degeneration resistor, which is degeneration in exactly the sense
    a cascode is and boosts ``ro`` by the same ``1 + gm·R``.  The walk is over
    MOSFETs, so ``by_drain`` has no entry for such a net and the device would
    otherwise report a bare ``ro`` (issue #226).  Checked before ``stop``: the
    pair's own source is pinned there as the differential AC ground, and with
    degeneration that ground is one resistor further down.  The ``+ R`` series
    term is dropped, as in the cascode branch above.

    ``levels`` (quiescent node levels, :mod:`~.node_levels`) puts each device
    at its own bias: ``gds`` at its ``Vds`` and, when its source is off its
    bulk, ``gm + gmb`` in the boost -- the source swing modulates the
    threshold too.  Under a cascode the device below sits just above its
    saturation knee, with several times the table's Vdd/2 ``gds``; reading it
    there removes most of the 8-20 dB cascode gain over-estimate (issue #273).
    A device on a net the levels do not reach reads as before.
    """
    s = sizing.get(device.ref)
    if s is None:
        return float("inf")
    vds, vsb = device_bias(device, levels or {})
    gds = model.gds(device.type, s.w_um, s.l_um, s.ids_a, vds=vds)
    ro = 1.0 / gds if gds > 0 else float("inf")
    src = device.terminals.get("s")
    r_deg = degen.get(src)

    def gm_boost() -> float:
        gm = model.gm(device.type, s.w_um, s.l_um, s.ids_a)
        if vsb is not None and device.terminals.get("b") != src:
            gm += model.gmb(device.type, s.w_um, s.l_um, s.ids_a, vsb)
        return gm

    if r_deg:
        return ro * (1.0 + gm_boost() * r_deg)
    if src in RAILS or src in stop or src is None:
        return ro
    below = [d for d in by_drain.get(src, []) if d.ref != device.ref]
    if not below:
        return ro
    r_src = _parallel_rout(below, by_drain, model, sizing, stop, degen, levels)
    return ro * (1.0 + gm_boost() * r_src) if r_src != float("inf") else float("inf")


def node_rout(out_net: str, mosfets: list[Device], model, sizing,
              stop: frozenset = frozenset(),
              degen: dict[str, float] | None = None,
              levels: dict[str, float] | None = None) -> float:
    """Cascode-aware output resistance (Ω) at ``out_net`` = parallel of every
    device whose drain is ``out_net`` (each looking-in, cascode-boosted).

    ``stop`` lists nets to treat as AC ground (typically the input-pair tail node)
    so the input pair contributes ``ro``, not a tail-degenerated cascode.
    ``degen`` maps a net to the series source resistance between it and that AC
    ground, which boosts the device above it by ``1 + gm·R``.  ``levels``
    reads each device at its own quiescent bias (see :func:`_looking_in_drain`).
    """
    by_drain = _by_drain(mosfets)
    return _parallel_rout(by_drain.get(out_net, []), by_drain, model, sizing,
                          stop, degen or {}, levels)


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Stage:
    """One gain stage's small-signal contribution.

    :param gm: signal-device transconductance in A/V, gm-ceiling clamped.
    :param rout: output resistance in Ω at the stage's output node.
    :param ids: the stage's quiescent current in A -- the input pair's tail
        current for the first stage, the signal device's drain current for a
        gain stage.  It is the most current the stage can push into its output
        node's capacitance, so it sets the stage's slew limit.
    """
    gm: float
    rout: float
    ids: float = 0.0


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
        chain has no mirror load or the tech supplies no ``cox`` — see
        :func:`_mirror_pole_hz` for the single-stage load family (resistor
        loads) that deliberately lands there.  The first non-dominant pole of
        a **load**-compensated single-stage OTA, so it sets that topology's
        phase margin; a Miller-compensated chain has its own non-dominant pole
        at the output and ignores this one.
    :param cc_pf: Miller compensation cap, ``None`` when uncompensated.
    :param cc2_pf: second compensation cap (three-stage), ``None`` otherwise.
    :param supply_currents: quiescent current in A of each element drawing
        from ``vdd!`` (power; :func:`vdd_supply_currents`).
    :param swing_headroom: ``(below_vdd, above_vss)`` in V -- how close the
        output can get to each rail (output swing), either entry ``None`` when
        it cannot be placed.  The second stage's ``Vdsat`` per polarity, or,
        behind a follower ``output_stage``, the follower's level shift on top
        of its driving stage; a DDA CMFB's sense range can bind instead
        (:func:`output_swing_headroom`).
    :param gain_measurable: ``False`` when the DC operating point the
        small-signal formulas sit on does not exist, so every gain-derived
        metric is withheld rather than reported optimistically (issue #148).
    :param compensation_scheme: the template's three-stage compensation;
        ``"reversed_nested_miller"`` selects the RNMC full model
        (:func:`~.equations.phase_margin_rnmc_deg`), any other the
        nested-Miller one (:func:`~.equations.nested_miller_loop`).
    :param node_caps_f: capacitance in F on each gain stage's output node,
        input pair first -- two entries on a two-stage chain, three on a
        three-stage one.  The inner entries are the gates of every device
        gated there; the second-stage output's is mostly the third stage's
        wide input gate, comparable to ``Cc2``.  The last is the last gain
        stage's output behind an output buffer -- the follower's gate net,
        its gates (the follower's own aside: ``output_buffer`` models it) plus
        the drain junctions on it, nearly all that node carries (issue #266)
        -- and ``0.0`` without one, where ``CL`` dwarfs it.  The full-model
        GBW and phase margin read it.
    :param second_stage_mirror_pole_hz: current-mirror node pole of a
        non-inverting second stage (nested Miller) in Hz, ``None`` when it
        has no mirror.
    :param third_stage_mirror_pole_hz: current-mirror node pole of a
        non-inverting third stage (RNMC) in Hz, ``None`` when it has no mirror.
    :param output_buffer: ``(gm, cgs, g_out)`` of a source-follower output
        buffer (:func:`_output_buffer`): the last gain stage then drives the
        follower's gate and ``CL`` sits on the follower's output; ``None``
        without one.  Its gain ``gm/(gm + g_out)`` scales the GBW; RNMC's
        full model also reads its ``cgs`` and output pole.
    :param comp_resistors: per half of the amplifier (one entry
        single-ended, ``_p`` then ``_n`` fully differential), whether its
        outer Miller branch and, three-stage, its inner ``Cc2`` branch have a
        series resistor (a nulling or indirect compensation variant).  The
        halves can differ (issues #266, #269).
    :param cmfb_coupling: ``(gm_c, pole_hz)`` of a fully-differential
        amplifier's common-mode feedback (:func:`_cmfb_coupling`): it couples
        halves whose compensation differs in the two-stage and nested-Miller
        full models; ``None`` without a CMFB.
    :param k_slew: fraction of the tail current the first stage can steer into
        its output node -- ``1.0`` when a current-mirror load collects both
        halves of a single-ended pair, ``0.5`` without a mirror or per side of
        a fully-differential pair (the side swings from half the tail to all
        or none of it).
    :param buffer_ids: bias current in A of a source-follower output stage,
        which alone charges ``CL`` behind it; ``None`` without one.
    """
    stages: tuple[Stage, ...]
    k_fs: float = 1.0
    gd_tail: float = 0.0
    gd_output_load: float = 0.0
    mirror_pole_hz: float | None = None
    cc_pf: float | None = None
    cc2_pf: float | None = None
    supply_currents: tuple[float, ...] = ()
    swing_headroom: tuple[float | None, float | None] = (None, None)
    gain_measurable: bool = True
    compensation_scheme: str | None = None
    node_caps_f: tuple[float, ...] = ()
    second_stage_mirror_pole_hz: float | None = None
    third_stage_mirror_pole_hz: float | None = None
    output_buffer: tuple[float, float, float] | None = None
    comp_resistors: tuple[tuple[bool, bool], ...] = ()
    cmfb_coupling: tuple[float, float | None] | None = None
    k_slew: float = 1.0
    buffer_ids: float | None = None

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
_OUTPUT_STAGE_ORDER = ("output_stage", "output_stage_p", "output_stage_n")
assert set(_OUTPUT_STAGE_ORDER) == OUTPUT_STAGE_SLOTS


def _first_present(slot_transistors: dict[str, list[Device]],
                   slots: tuple[str, ...]) -> list[Device]:
    """Devices of the first present slot (SE name before the FD legs)."""
    for name in slots:
        if name in slot_transistors:
            return slot_transistors[name]
    return []


def _tail_current_net(pair_source: str | None,
                      ip_resistors: list[Device]) -> str | None:
    """The net the tail current source's drain actually sits on.

    For a plain differential pair that is the pair's own source net.  A
    **source-degenerated** pair puts a resistor between each device's source
    and the shared tail node, so the pair's source is one hop short of it:
    nothing but a resistor touches that net, and a MOSFET-only walk like
    :func:`node_rout` finds no drain there and reports infinite resistance --
    which read as "no tail" and withheld CMRR for every degenerated pair with
    a transistor tail (issue #224).

    Hopping the series resistor costs 0.02 dB of CMRR to ignore in the other
    direction: the degeneration R is ~0.25% of a simple mirror tail's ``ro``
    (5.1 kΩ against 2.05 MΩ on GF180) and ~0.0006% of a cascode tail's, so the
    series term is dropped and only the node resolution is corrected.
    """
    if pair_source is None:
        return None
    for r in ip_resistors:
        t1, t2 = r.terminals.get("t1"), r.terminals.get("t2")
        if t1 == pair_source and t2:
            return t2
        if t2 == pair_source and t1:
            return t1
    return pair_source


def vdd_supply_currents(view: CircuitView,
                        sizing: dict[str, TransistorSizing]) -> tuple[float, ...]:
    """Planned quiescent current of each element drawing from ``vdd!``, in A.

    KCL at the positive rail: every MOSFET with its source or drain on
    ``vdd!`` contributes its planned ``|IDS|``, and every resistor tied to
    ``vdd!`` the planned current of the MOSFET channels ending on its other
    node (a load or bias-leg resistor feeding one device).  A node no channel
    ends on is a resistor junction, crossed to the resistors' far ends: a
    resistor tail feeds a degenerated pair's sources through its degeneration
    resistors.  Each branch is therefore counted once, whatever slot it sits
    in -- a bias leg's NMOS/PMOS pair, a diode shared by two slots, the CMFB,
    a follower, both branches of a non-inverting stage.  The external
    reference current is not part of it: the bench
    injects ``Iref`` into an NMOS reference diode from ground, so the supply
    current the bench reads (``i(Vsup)``) is this sum too (issue #285).
    """
    mosfets = [d for d, _slot in view.all_transistors.values()]

    def ids(d: Device) -> float:
        s = sizing.get(d.ref)
        return abs(s.ids_a) if s is not None else 0.0

    def channel(d: Device) -> tuple[str | None, str | None]:
        return d.terminals.get("d"), d.terminals.get("s")

    resistors = {r.ref: r for rs in view.slot_resistors.values() for r in rs}

    def far_end(r: Device, net: str) -> str | None:
        t1, t2 = r.terminals.get("t1"), r.terminals.get("t2")
        return t2 if t1 == net else t1

    def fed(net: str | None, via: Device, seen: set[str]) -> float:
        """Current a resistor ``via`` delivers into ``net``."""
        seen.add(via.ref)
        i = sum(ids(d) for d in mosfets if net in channel(d))
        if i or net in RAILS:
            return i
        return sum(fed(far_end(r, net), r, seen) for r in resistors.values()
                   if r.ref not in seen and net in (r.terminals.get("t1"),
                                                    r.terminals.get("t2")))

    currents = [ids(d) for d in mosfets if "vdd!" in channel(d)]
    for r in resistors.values():
        if "vdd!" in (r.terminals.get("t1"), r.terminals.get("t2")):
            currents.append(fed(far_end(r, "vdd!"), r, set()))
    return tuple(currents)


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

    The mirror's reference device (:func:`~.preprocess._mirror_reference`)
    pins its gate node at ``1/gm``; the capacitance that node drives is the
    gate capacitance of every device it gates -- itself and the mirror devices
    copying it -- so the pole sits at ``gm/(2π·ΣCgs)``.  This is the first
    non-dominant pole of a load-compensated single-stage OTA (issue #221).
    A wide-swing mirror closes its diode through a cascode rather than a
    ``g == d`` device, but the node and its ``1/gm`` are the same (issue #241).

    ``None`` when the load has no mirror, when the reference device is
    unsized, or when the technology supplies no ``cox`` for ``Cgs`` -- each a
    case where the pole genuinely cannot be placed, and the caller withholds
    phase margin rather than inventing one.

    **Resistor loads** fall in that hole, deliberately (issue #228): they have
    no internal node at all.  In this model such a stage is genuinely
    single-pole, so the phase margin would be exactly 90° for *every* sizing --
    a statement about the model, not about the design, and one that would pass
    any ``phase_margin_min_deg`` a spec could set.
    """
    ref = _mirror_reference(load_devs)
    if ref is None:
        return None
    diode, net = ref
    s = sizing.get(diode.ref)
    if s is None:
        return None
    gm = model.gm(diode.type, s.w_um, s.l_um, s.ids_a)
    c_f = 0.0
    for d in mosfets:
        sd = sizing.get(d.ref)
        if sd is not None and d.terminals.get("g") == net:
            c_f += model.cgs(d.type, sd.w_um, sd.l_um)
    if gm <= 0.0 or c_f <= 0.0:
        return None
    return gm / (2.0 * math.pi * c_f)


def _output_buffer(slot_transistors: dict[str, list[Device]], model,
                   sizing, spec: SizingSpec) -> tuple[float, float, float] | None:
    """``(gm, cgs, g_out)`` of a source-follower output buffer, or ``None``.

    ``gm``/``cgs`` are the follower device's (the one gated by the signal);
    ``g_out`` sums the other conductances on its output node: every
    output-stage device's ``gds``, plus the follower's body transconductance
    when its bulk sits on a rail rather than on its source.  The source is
    the amplifier output, which the feedback holds at ``spec.vcm``; there an
    NMOS follower's ``gmb`` is ~13 % of its ``gm`` on GF180, so its gain
    ``gm/(gm + g_out)`` is ~0.88, not 1 (issue #274).
    """
    devs = _first_present(slot_transistors, _OUTPUT_STAGE_ORDER)
    fol = next((d for d in devs if is_signal_device(d) and d.ref in sizing), None)
    if fol is None:
        return None
    s = sizing[fol.ref]
    g_out = sum(model.gds(d.type, sizing[d.ref].w_um, sizing[d.ref].l_um,
                          sizing[d.ref].ids_a)
                for d in devs if d.ref in sizing)
    bulk = fol.terminals.get("b")
    if bulk in RAILS and bulk != fol.terminals.get("s"):
        g_out += model.gmb(fol.type, s.w_um, s.l_um, s.ids_a,
                           abs(spec.vcm - spec.rail_v(bulk)))
    return (model.gm(fol.type, s.w_um, s.l_um, s.ids_a),
            model.cgs(fol.type, s.w_um, s.l_um), g_out)


#: Nets the CMFB coupling treats as fixed: the outputs' common-mode reference
#: and the bias generator's rails, beside the supplies.
_CMFB_AC_GROUND_PREFIX = "net_bias"
_FD_OUTPUTS = ("outp", "outn")


def _cmfb_coupling(view: CircuitView, mosfets: list[Device], model, sizing,
                   resistor_ohms: dict[str, float]) -> tuple[float, float | None] | None:
    """``(gm_c, pole_hz)`` of a fully-differential amplifier's CMFB loop, or
    ``None``.

    ``gm_c`` is the current in A/V the CMFB injects into each first-stage
    node per volt of output common mode -- signed, negative feedback when the
    first stage inverts to the output -- and ``pole_hz`` its slowest pole.
    Read off a small-signal network of the ``cmfb`` slot's own devices and
    sense resistors (any variant: resistive or difference-difference sensing,
    either mirror), driven at 1 V on ``outp``/``outn`` with the supplies,
    bias rails and ``vcm_ref`` held; its output is the net that gates devices
    outside the slot (the first-stage load), each of which injects
    ``-gm·v`` into its drain.  With mismatched halves this coupling moves the
    differential phase margin ~6° (issue #274).
    """
    import numpy as np

    from . import equations as eq

    cmfb = [d for d in view.slot_transistors.get("cmfb", []) if d.ref in sizing]
    if not cmfb:
        return None
    own = {d.ref for d in cmfb}
    fixed = set(RAILS) | {"vcm_ref"}

    def kind(net: str | None) -> str:
        if net in _FD_OUTPUTS:
            return "in"
        if net is None or net in fixed or net.startswith(_CMFB_AC_GROUND_PREFIX):
            return "gnd"
        return "node"
    nets = sorted({n for d in cmfb for n in (d.terminals.get(t) for t in "dgs")
                   if kind(n) == "node"}
                  | {n for r in view.slot_resistors.get("cmfb", [])
                     for n in (r.terminals.get("t1"), r.terminals.get("t2"))
                     if kind(n) == "node"})
    idx = {n: i for i, n in enumerate(nets)}
    n_in = len(nets)                       # one more column: the driven outputs
    G = np.zeros((n_in, n_in + 1))
    C = np.zeros((n_in, n_in))

    def col(net):
        k = kind(net)
        return idx[net] if k == "node" else (n_in if k == "in" else None)

    def add(row_net, col_net, x):
        r, c = (idx.get(row_net) if kind(row_net) == "node" else None), col(col_net)
        if r is not None and c is not None:
            G[r, c] += x

    for d in cmfb:
        sd = sizing[d.ref]
        gm = model.gm(d.type, sd.w_um, sd.l_um, sd.ids_a)
        gds = model.gds(d.type, sd.w_um, sd.l_um, sd.ids_a)
        dn, gn, sn = (d.terminals.get(t) for t in "dgs")
        for row, sign in ((dn, 1.0), (sn, -1.0)):   # i_ds leaves d, enters s
            add(row, gn, sign * gm)
            add(row, sn, -sign * (gm + gds))
            add(row, dn, sign * gds)
        if kind(gn) == "node":
            C[idx[gn], idx[gn]] += model.cgg(d.type, sd.w_um, sd.l_um, sd.ids_a)
    for r in view.slot_resistors.get("cmfb", []):
        ohms = resistor_ohms.get(r.ref, 0.0)
        a, b = r.terminals.get("t1"), r.terminals.get("t2")
        if ohms <= 0:              # unsized: the sense net follows the outputs
            ohms = 1.0
        for x, y in ((a, b), (b, a)):
            add(x, x, 1.0 / ohms)
            add(x, y, -1.0 / ohms)
    # The output: the slot net that gates the first-stage load.
    loads = [d for d in mosfets if d.ref not in own and d.ref in sizing
             and d.terminals.get("g") in idx]
    if not loads:
        return None
    out = loads[0].terminals.get("g")
    for d in loads:
        sd = sizing[d.ref]
        C[idx[out], idx[out]] += model.cgg(d.type, sd.w_um, sd.l_um, sd.ids_a)
    Guu, rhs = G[:, :n_in], -G[:, n_in]
    try:
        v_out = np.linalg.solve(Guu, rhs)[idx[out]]
    except np.linalg.LinAlgError:
        return None
    sl = sizing[loads[0].ref]
    gm_load = model.gm(loads[0].type, sl.w_um, sl.l_um, sl.ids_a)
    poles = [abs(p) for p in eq._poles(Guu, C) if abs(p) > 0]
    return -gm_load * v_out, (min(poles) / (2.0 * math.pi) if poles else None)


def _source_degeneration_r(ip_devs: list[Device], ip_resistors: list[Device],
                           resistor_ohms: dict[str, float]) -> dict[str, float]:
    """``{input-pair source net: series R (Ω) down to the tail}``.

    Only degeneration resistors that were actually *sized* count: the
    synthesizer emits every resistor at a 1 kΩ placeholder, and
    :func:`~circuitgenome.sizer.gmid.resistors.size_resistors` leaves that
    placeholder alone when the intent asks for no degeneration, so an unsized
    r1/r2 must not boost anything.
    """
    sources = {d.terminals.get("s") for d in ip_devs}
    out: dict[str, float] = {}
    for r in ip_resistors:
        ohms = resistor_ohms.get(r.ref, 0.0)
        if ohms <= 0.0:
            continue
        for net in (r.terminals.get("t1"), r.terminals.get("t2")):
            if net and net in sources:
                out[net] = ohms
    return out


# --------------------------------------------------------------------------- #
# Output swing
# --------------------------------------------------------------------------- #
#: How far (V) the follower's level-shifted swing edge may miss the spec before
#: :func:`check_follower_swing` rejects the sizing.  The model is conservative
#: there: the swing bench keeps tracking (loop slope ≥ 0.7) a little past the
#: point where the driving device leaves saturation -- measured at up to
#: 0.15 V beyond the modelled edge on gf180 and 0.11 V on ptm45.  Rejecting
#: only clearer misses leaves a near-miss candidate to SPICE, which remains
#: the authority for accepts.
_FOLLOWER_SWING_TOL_V = 0.2

#: How far (V) a DDA CMFB's sense-range swing edge may miss the spec before
#: :func:`check_cmfb_sense_swing` rejects the sizing.  On 24 sampled gf180
#: DDA designs the bench edge landed up to 0.11 V beyond the modelled
#: ``Vocm ± √2·Vdsat`` (and up to 0.13 V short of it): how fast the CM drifts
#: once the pairs saturate depends on the rest of the amplifier.  As with the
#: follower gate, only clearer misses are rejected; SPICE decides the rest.
_CMFB_SENSE_SWING_TOL_V = 0.15


def _is_fully_differential(slot_transistors: dict[str, list[Device]]) -> bool:
    """Whether the circuit has per-output (``_p``/``_n``) second stages."""
    return any(s in slot_transistors for s in ("second_stage_p", "second_stage_n"))


def _fd_symmetric(
    slot_transistors: dict[str, list[Device]],
    hi: float | None,
    lo: float | None,
) -> tuple[float | None, float | None]:
    """``(below_vdd, above_vss)`` with a buffered FD pair's swing symmetry applied.

    The CMFB holds a fully-differential pair's output CM at ``Vocm``, so the
    outputs move as mirror images, ``outn = 2·Vocm − outp``: one output's high
    edge is the other's low edge, and each edge is bounded by
    ``Vocm ± min(Vocm − (Vss + lo), (Vdd − hi) − Vocm)`` (issue #281).  With
    ``Vocm`` at mid-rail, where every testbench rig drives ``vcm_ref``, that is
    the larger headroom on both sides.

    Applied only behind a follower ``output_stage``, whose ``|Vgs|`` shift is
    a hard limit a whole threshold from the rail.  An unbuffered stage's
    ``Vdsat`` limits are soft -- the swing bench keeps tracking almost to the
    rails (gf180: 0.01-0.05 V from each) -- so mirroring the larger of them
    would only make the other edge more pessimistic.  Single-ended and
    unbuffered circuits, and a side left unplaced, come back unchanged.
    """
    if (not _is_fully_differential(slot_transistors)
            or _follower(slot_transistors)[0] is None or hi is None or lo is None):
        return hi, lo
    tight = max(hi, lo)
    return tight, tight


def _vdsat_of(devs: list[Device], sizing, dtype: str) -> float | None:
    """``Vdsat`` in V of the first ``dtype`` device of ``devs`` that is sized."""
    d = next((d for d in devs if d.type == dtype), None)
    s = sizing.get(d.ref) if d is not None else None
    return s.vds_sat_v if s is not None else None


def _follower(
    slot_transistors: dict[str, list[Device]],
) -> tuple[Device | None, list[Device]]:
    """``(follower, its bias source(s))`` of the ``output_stage`` buffer.

    The follower is the buffer's signal device (gate on the amplifier node);
    its partner, gated from a bias net, is the current source biasing it.
    ``(None, [])`` without a buffer.  FD circuits have one per output leg;
    the legs are sized identically, so the ``_p`` leg represents both.
    """
    devs = _first_present(slot_transistors, (
        "output_stage", "output_stage_p", "output_stage_n"))
    fol = next((d for d in devs if is_signal_device(d)), None)
    return fol, [d for d in devs if d is not fol]


def _stage_swing_headroom(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
) -> tuple[float | None, float | None]:
    """``(below_vdd, above_vss)`` the output devices alone allow.

    Without an output buffer the output node is the second stage's drain, and
    each rail-side device leaves saturation ``Vdsat`` from its rail.

    Behind a source-follower ``output_stage`` the output sits one ``|Vgs|``
    away from the node that drives it, so on one side the follower shifts the
    whole swing away from the rail:

    * PMOS follower (``out = in + |Vgs|``, current source to ``Vdd``): the
      output cannot fall below ``Vss + Vdsat_n(driver) + |Vgs_f|``, and cannot
      rise above ``Vdd − Vdsat(current source)``.
    * NMOS follower (``out = in − Vgs``, current sink to ``Vss``): the output
      cannot rise above ``Vdd − Vdsat_p(driver) − Vgs_f``, and cannot fall
      below ``Vss + Vdsat(current sink)``.

    The driver is whatever device pulls the follower's gate net toward that
    rail -- the last gain stage.  ``Vgs_f`` is the sized follower's own
    (LUT or square-law, ``Vsb = 0``) value plus the body-effect rise when its
    bulk is not tied to its source: at the level-shifted edge the source sits
    ``(Vdd − Vss) − headroom`` from the bulk rail, so the headroom is solved as
    a fixed point (the shift shrinks as the headroom grows, so it converges in
    a few steps).  An entry is ``None`` when the device it needs is unsized.
    """
    fol, bias_devs = _follower(view.slot_transistors)
    if fol is None:
        ss_devs = _first_present(view.slot_transistors, _STAGE_SLOT_GROUPS[0])
        return (_vdsat_of(ss_devs, sizing, "pmos"),
                _vdsat_of(ss_devs, sizing, "nmos"))

    s_fol = sizing.get(fol.ref)
    own = _vdsat_of(bias_devs, sizing, fol.type)
    gate = fol.terminals.get("g")
    drivers = [d for d, _slot in view.all_transistors.values()
               if d.terminals.get("d") == gate]
    drv = _vdsat_of(drivers, sizing,
                    "nmos" if fol.type == "pmos" else "pmos")
    shifted = None
    if s_fol is not None and drv is not None:
        base = drv + abs(s_fol.vgs_v)
        body_tied = fol.terminals.get("b") == fol.terminals.get("s")
        shifted = base
        for _ in range(20):
            vsb = 0.0 if body_tied else (spec.vdd - spec.vss) - shifted
            shifted = base + model.body_vth_shift(fol.type, vsb)
    return (own, shifted) if fol.type == "pmos" else (shifted, own)


def cmfb_sense_range(
    view: CircuitView, sizing: dict[str, TransistorSizing],
) -> float | None:
    """How far (V) each output can move from the output CM before a DDA CMFB
    loses it, or ``None`` when the CMFB does not sense the outputs directly.

    A difference-difference CMFB senses each output with its own differential
    pair against ``vcm_ref`` (issue #272).  As the outputs split, each pair
    steers its tail toward one side; once a pair has steered the whole tail,
    its summed branch currents stop responding to the common mode, so the CM
    loop has no gain left and the output CM wanders off.  A square-law pair
    has steered its whole tail at a differential input of
    ``√2 · Vov``, so each output stays CM-regulated within
    ``Vocm ± √2 · Vdsat(sense device)``, with ``Vdsat`` taken at the
    quiescent half-tail current.  A resistive-sense CMFB averages the outputs
    through resistors first, so its pair sees only the common mode and sets
    no such limit.

    The sense devices are the CMFB signal devices gated directly by a net that
    a MOSFET outside the CMFB drives, i.e. an amplifier output.  If several
    are sized, the narrowest range wins.
    """
    cmfb = [d for d in view.slot_transistors.get("cmfb", [])
            if d.type in ("nmos", "pmos")]
    own = {d.ref for d in cmfb}
    driven = {d.terminals.get("d") for d, _slot in view.all_transistors.values()
              if d.ref not in own}
    vdsats = [sizing[d.ref].vds_sat_v for d in cmfb
              if is_signal_device(d) and d.terminals.get("g") in driven
              and d.ref in sizing]
    return math.sqrt(2.0) * min(vdsats) if vdsats else None


def output_swing_headroom(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
) -> tuple[float | None, float | None]:
    """``(below_vdd, above_vss)``: how close to each rail the output can swing.

    The tighter of two limits on each side:

    * the output devices (:func:`_stage_swing_headroom`): the last stage's
      rail-side ``Vdsat``, or a source-follower's level shift on top of it;
    * a DDA CMFB's sense range (:func:`cmfb_sense_range`): beyond
      ``Vocm ± range`` the output CM is no longer held.  ``Vocm`` is
      mid-rail, where every testbench rig drives ``vcm_ref``.

    Behind a follower on a fully-differential circuit the outputs swing
    symmetrically about ``Vocm``, so the follower-shifted side bounds both
    edges (:func:`_fd_symmetric`).  An entry is ``None`` when neither limit can be
    placed on that side.
    """
    hi, lo = _fd_symmetric(view.slot_transistors,
                           *_stage_swing_headroom(view, sizing, model, spec))
    rng = cmfb_sense_range(view, sizing)
    if rng is None:
        return hi, lo
    cm_head = (spec.vdd - spec.vss) / 2.0 - rng
    return (cm_head if hi is None else max(hi, cm_head),
            cm_head if lo is None else max(lo, cm_head))


def check_follower_swing(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
) -> tuple[list[str], bool]:
    """``(warnings, feasible)`` for a follower output stage's level shift.

    ``feasible`` is ``False`` when the level-shifted swing edge
    (:func:`_stage_swing_headroom`) misses its spec bound by more than
    :data:`_FOLLOWER_SWING_TOL_V`.  On a fully-differential circuit the
    outputs swing symmetrically about mid-rail (:func:`_fd_symmetric`), so the
    shift bounds the mirrored edge as well and both are checked.  No other
    sizing knob can recover it: the shift is the follower's ``|Vgs|``, which
    even weak inversion cannot take below roughly ``Vth``.  Circuits without a
    follower always pass.
    """
    fol, _bias = _follower(view.slot_transistors)
    if fol is None:
        return [], True
    hi, lo = _stage_swing_headroom(view, sizing, model, spec)
    shifted = lo if fol.type == "pmos" else hi   # PMOS lifts low, NMOS drops high
    if shifted is None:
        return [], True
    sides = ("low",) if fol.type == "pmos" else ("high",)
    if _is_fully_differential(view.slot_transistors):
        sides = ("high", "low")
    misses = []
    for side in sides:
        if side == "low":
            bound, reached, sign = spec.output_swing_min_v, spec.vss + shifted, 1.0
        else:
            bound, reached, sign = spec.output_swing_max_v, spec.vdd - shifted, -1.0
        if bound is not None and sign * (reached - bound) > _FOLLOWER_SWING_TOL_V:
            misses.append(f"the {side} swing to {reached:.2f} V against the "
                          f"{bound:.2f} V spec")
    if not misses:
        return [], True
    return [f"{fol.ref}: {fol.type} source-follower output stage cannot meet "
            f"the swing spec — its |Vgs| level shift limits "
            f"{' and '.join(misses)}; relax the swing spec, raise the supply "
            f"or use an unbuffered topology."], False


def check_cmfb_sense_swing(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
) -> tuple[list[str], bool]:
    """``(warnings, feasible)`` for a DDA CMFB's sense range (issue #272).

    ``feasible`` is ``False`` when either CM-regulated swing edge,
    ``Vocm ± range`` (:func:`cmfb_sense_range`), misses its spec bound by
    more than :data:`_CMFB_SENSE_SWING_TOL_V`.  The range is set by the sense
    pairs' overdrive, which the sizer does not adjust: a larger overdrive
    would widen it, but only up to about ``Vocm − Vth − Vdsat(tail)`` (the
    tail must stay saturated while one device carries it all) and at the
    cost of CM-loop gain.  Circuits without a DDA CMFB always pass.
    """
    rng = cmfb_sense_range(view, sizing)
    if rng is None:
        return [], True
    vocm = (spec.vdd + spec.vss) / 2.0
    hi, lo = vocm + rng, vocm - rng
    misses = []
    if (spec.output_swing_max_v is not None
            and spec.output_swing_max_v - hi > _CMFB_SENSE_SWING_TOL_V):
        misses.append(f"the high swing to {hi:.2f} V against the "
                      f"{spec.output_swing_max_v:.2f} V spec")
    if (spec.output_swing_min_v is not None
            and lo - spec.output_swing_min_v > _CMFB_SENSE_SWING_TOL_V):
        misses.append(f"the low swing to {lo:.2f} V against the "
                      f"{spec.output_swing_min_v:.2f} V spec")
    if not misses:
        return [], True
    return [f"cmfb: the difference-difference CMFB holds the output common "
            f"mode only within ±{rng:.2f} V of {vocm:.2f} V (beyond it each "
            f"sense pair has steered its whole tail), which limits "
            f"{' and '.join(misses)}; relax the swing spec or use a "
            f"resistive-sense CMFB."], False


def build_stage_chain(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
    *,
    cc_pf: float | None = None,
    cc2_pf: float | None = None,
    gd_load_r: float = 0.0,
    resistor_ohms: dict[str, float] | None = None,
    bias_aware: bool = False,
) -> StageChain:
    """Extract the :class:`StageChain` from a solved sizing.

    ``gd_load_r`` is the first-stage load resistor's conductance in A/V; it
    loads the first stage's output node, which ``node_rout`` — a walk over
    MOSFETs — cannot see.  ``resistor_ohms`` is the sized ``{ref: Ω}`` map;
    the walk reads the input pair's degeneration resistors out of it so a
    degenerated pair gets its ``ro·(1+gm·R)`` boost (issue #226).

    The input pair's source is treated as an AC ground so the pair contributes
    ``ro`` rather than a tail-degenerated cascode — or ``ro·(1+gm·R)`` when it
    reaches that ground through a degeneration resistor.  That net is the tail
    node only for a plain pair; a source-degenerated one reaches its tail
    through a resistor, which :func:`_tail_current_net` hops for the CMRR
    conductance (issue #224).  The first stage's output is the *next* stage's
    signal gate, not the pair's drain: on a folded cascode those are different
    nets.  With no next stage the output node is resolved structurally
    instead — again not the pair's drain, for the same reason (issue #228).

    ``bias_aware`` reads the first stage's output-resistance walk (its
    ``rout`` and, single-stage, the PSRR load) at each device's own quiescent
    ``Vds`` and ``Vsb``, estimated by
    :func:`~.node_levels.estimate_node_levels` from the rails and the input
    common mode (issue #273).  That is where the cascode stacks are.  The
    amplifier output is deliberately left unpinned: a device draining onto it
    reads at the table's own Vdd/2, which on a cascode leans to the safe,
    over-predicting side.  Later gain stages keep the single-point read: their
    ``rout`` is taken at the signal device's drain, which on a non-inverting
    stage is its mirror's diode node rather than its output, and reading those
    devices at their own bias moved GF180 three-stage gains 1-3.5 dB under
    SPICE.  The CMRR tail walk keeps it too: read at the tail's own Vds, the
    predicted CMRR fell ~17 dB below ngspice's median on GF180, where the
    single-point read sits on it.  Off by default, so the requirement-side
    callers (the RNMC re-plan) keep the single-point model and sizing is
    unchanged.
    """
    slot_transistors = view.slot_transistors
    mosfets = [d for d, _slot in view.all_transistors.values()]
    ip_devs = slot_transistors.get("input_pair", [])
    ip_resistors = view.slot_resistors.get("input_pair", [])
    tail_net = ip_devs[0].terminals.get("s") if ip_devs else None
    stop = frozenset({tail_net}) if tail_net else frozenset()
    degen = _source_degeneration_r(ip_devs, ip_resistors, resistor_ohms or {})
    levels: dict[str, float] = {}
    if bias_aware:
        pinned = {net: spec.rail_v(net) for net in RAILS}
        pinned.update({d.terminals.get("g"): spec.vcm for d in ip_devs})
        resistors = list({r.ref: r for rs in view.slot_resistors.values()
                          for r in rs}.values())
        levels = estimate_node_levels(mosfets, resistors, resistor_ohms or {},
                                      sizing, model, pinned)

    def _gm(d: Device) -> float:
        s = sizing.get(d.ref)
        if s is None:
            return 0.0
        return min(model.gm(d.type, s.w_um, s.l_um, s.ids_a),
                   model.gm_ceiling(d.type, s.ids_a, s.l_um))

    def _ids(d: Device | None) -> float:
        s = sizing.get(d.ref) if d is not None else None
        return abs(s.ids_a) if s is not None else 0.0

    def _rout(net: str | None, extra_gd: float = 0.0,
              at_bias: dict[str, float] | None = None) -> float:
        if not net:
            return float("inf")
        r = node_rout(net, mosfets, model, sizing, stop, degen, at_bias)
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
    # The pair's two drain currents add up to the tail current.
    stages = [Stage(gm=_gm(ip_devs[0]) if ip_devs else 0.0,
                    rout=_rout(out1, gd_load_r, levels),
                    ids=sum(_ids(d) for d in ip_devs if is_signal_device(d)))]

    # --- Stages 2 and 3: each numbered gain slot's signal device ---
    for sig in signal_devs:
        stages.append(Stage(gm=_gm(sig) if sig is not None else 0.0,
                            rout=_rout(sig.terminals.get("d") if sig else None),
                            ids=_ids(sig)))

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
            r = _looking_in_drain(d, by_drain, model, sizing, stop, degen, levels)
            gd_output_load = 1.0 / r if 0.0 < r < float("inf") else 0.0
        # A resistor load has no device to read: the output-node conductance is
        # exactly 1/R, which the sizer already solved for and passed in here.
        if gd_output_load == 0.0:
            gd_output_load = gd_load_r

    # --- Tail conductance (CMRR): the full stack down to the rail ---
    # Resolved from the pair's source *through* any degeneration resistor, so
    # the walk starts on the net the tail device's drain is really on (#224).
    # Deliberately not reused for ``stop`` above: that set wants the pair's own
    # source pinned as AC ground, with the hop down to it carried by ``degen``
    # as a ``ro`` boost (#226) alongside the ``with_first_stage_gm`` derate.
    tail_current_net = _tail_current_net(tail_net, ip_resistors)
    gd_tail = 0.0
    if slot_transistors.get("tail_current") and tail_current_net:
        r_tail = node_rout(tail_current_net, mosfets, model, sizing, frozenset())
        gd_tail = 1.0 / r_tail if r_tail and r_tail != float("inf") else 0.0

    # --- Multi-stage: the parasitics the full-model GBW and PM need ---
    follower = next((d for d in _first_present(slot_transistors, _OUTPUT_STAGE_ORDER)
                     if is_signal_device(d)), None)
    node_caps: tuple[float, ...] = ()
    ss_mirror_pole = ts_mirror_pole = None
    comp_resistors: tuple[tuple[bool, bool], ...] = ()
    if slot_devs:
        def cap_on(net: str | None, terminal: str, cap) -> float:
            """``cap(device, sizing)`` summed over the devices whose
            ``terminal`` is on ``net`` (an output buffer's own gate aside)."""
            return sum(cap(d, sizing[d.ref]) for d in mosfets
                       if net and d.ref in sizing and d.terminals.get(terminal) == net
                       and not (terminal == "g" and d is follower))

        def gate(d, sd):
            # RNMC keeps the Cox estimate its compensation sizing was
            # validated against (#266-#270); the others read the gate at its
            # inversion level, which a weak-inversion second stage needs.
            if view.compensation_scheme == RNMC:
                return model.cgs(d.type, sd.w_um, sd.l_um)
            return model.cgg(d.type, sd.w_um, sd.l_um, sd.ids_a)

        def cdb(d, sd):
            return model.cdb(d.type, sd.w_um)
        # An inner node is the next stage's signal gate (a non-inverting
        # stage's signal device drains into its mirror instead).  Behind a
        # buffer the last gain stage's output is the follower's gate.
        inner = [d.terminals.get("g") if d else None for d in signal_devs[1:]]
        last = follower.terminals.get("g") if follower else None
        node_caps = (cap_on(out1, "g", gate), *(cap_on(n, "g", gate) for n in inner),
                     cap_on(last, "g", gate) + cap_on(last, "d", cdb))
        ss_mirror_pole = _mirror_pole_hz(slot_devs[0], mosfets, model, sizing)
        if len(slot_devs) > 1:
            ts_mirror_pole = _mirror_pole_hz(slot_devs[1], mosfets, model, sizing)
            halves = sorted(slot[len("comp1"):] for slot in view.slot_capacitors
                            if slot.startswith("comp1"))
            comp_resistors = tuple(
                (bool(view.slot_resistors.get(f"comp1{h}")),
                 bool(view.slot_resistors.get(f"comp2{h}")))
                for h in halves)
        else:
            comp_resistors = tuple(
                (bool(view.slot_resistors.get(slot)), False)
                for slot in sorted(view.slot_capacitors)
                if slot == "compensation" or slot.startswith("comp_"))

    k_fs = _first_stage_gain_factor(slot_transistors)
    fully_differential = _is_fully_differential(slot_transistors)

    return StageChain(
        stages=tuple(stages),
        k_fs=k_fs,
        gd_tail=gd_tail,
        gd_output_load=gd_output_load,
        mirror_pole_hz=_mirror_pole_hz(
            slot_transistors.get("load", []), mosfets, model, sizing),
        cc_pf=cc_pf,
        cc2_pf=cc2_pf,
        supply_currents=vdd_supply_currents(view, sizing),
        swing_headroom=output_swing_headroom(view, sizing, model, spec),
        compensation_scheme=view.compensation_scheme,
        node_caps_f=node_caps,
        second_stage_mirror_pole_hz=ss_mirror_pole,
        third_stage_mirror_pole_hz=ts_mirror_pole,
        output_buffer=_output_buffer(slot_transistors, model, sizing, spec),
        comp_resistors=comp_resistors,
        cmfb_coupling=(_cmfb_coupling(view, mosfets, model, sizing, resistor_ohms or {})
                       if fully_differential else None),
        k_slew=0.5 if fully_differential else k_fs,
        buffer_ids=_ids(follower) if follower is not None else None,
    )
