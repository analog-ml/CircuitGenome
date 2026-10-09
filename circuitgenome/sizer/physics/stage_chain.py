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
reads only ``model.gm`` and ``model.gds``, so both the Level-1 and gm/Id
backends get the same treatment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from circuitgenome.synthesizer.models import Device

from .circuit_view import CircuitView
from .device_model import DeviceModel
from ..models import SizingSpec, TransistorSizing
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


def _parallel_rout(devices, by_drain, model, sizing, stop, degen) -> float:
    """Parallel combination (Ω) of each device's looking-in drain resistance."""
    g = 0.0
    for d in devices:
        r = _looking_in_drain(d, by_drain, model, sizing, stop, degen)
        if r > 0:
            g += 1.0 / r
    return 1.0 / g if g > 0 else float("inf")


def _looking_in_drain(device, by_drain, model, sizing, stop, degen) -> float:
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
    """
    s = sizing.get(device.ref)
    if s is None:
        return float("inf")
    gds = model.gds(device.type, s.w_um, s.l_um, s.ids_a)
    ro = 1.0 / gds if gds > 0 else float("inf")
    src = device.terminals.get("s")
    r_deg = degen.get(src)
    if r_deg:
        gm = model.gm(device.type, s.w_um, s.l_um, s.ids_a)
        return ro * (1.0 + gm * r_deg)
    if src in RAILS or src in stop or src is None:
        return ro
    below = [d for d in by_drain.get(src, []) if d.ref != device.ref]
    if not below:
        return ro
    gm = model.gm(device.type, s.w_um, s.l_um, s.ids_a)
    r_src = _parallel_rout(below, by_drain, model, sizing, stop, degen)
    return ro * (1.0 + gm * r_src) if r_src != float("inf") else float("inf")


def node_rout(out_net: str, mosfets: list[Device], model, sizing,
              stop: frozenset = frozenset(),
              degen: dict[str, float] | None = None) -> float:
    """Cascode-aware output resistance (Ω) at ``out_net`` = parallel of every
    device whose drain is ``out_net`` (each looking-in, cascode-boosted).

    ``stop`` lists nets to treat as AC ground (typically the input-pair tail node)
    so the input pair contributes ``ro``, not a tail-degenerated cascode.
    ``degen`` maps a net to the series source resistance between it and that AC
    ground, which boosts the device above it by ``1 + gm·R``.
    """
    by_drain = _by_drain(mosfets)
    return _parallel_rout(by_drain.get(out_net, []), by_drain, model, sizing,
                          stop, degen or {})


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
    :param supply_currents: per-branch quiescent currents in A (power).
    :param swing_headroom: ``(below_vdd, above_vss)`` in V -- how close the
        output can get to each rail (output swing), either entry ``None`` when
        it cannot be placed.  The second stage's ``Vdsat`` per polarity, or,
        behind a follower ``output_stage``, the follower's level shift on top
        of its driving stage (:func:`output_swing_headroom`).
    :param gain_measurable: ``False`` when the DC operating point the
        small-signal formulas sit on does not exist, so every gain-derived
        metric is withheld rather than reported optimistically (issue #148).
    :param compensation_scheme: the template's three-stage compensation;
        ``"reversed_nested_miller"`` selects the full-model RNMC phase margin
        (:func:`~.equations.phase_margin_rnmc_deg`).
    :param node_caps_f: gate capacitance in F on each gain stage's output
        node (every device gated there), input pair first.  Only the RNMC
        phase margin reads it: the second-stage output's entry is the third
        stage's wide input gate, comparable to ``Cc2``.
    :param third_stage_mirror_pole_hz: current-mirror node pole of a
        non-inverting third stage in Hz, ``None`` when it has no mirror.
    :param output_buffer: ``(gm, cgs, g_out)`` of a source-follower output
        buffer (RNMC only): the third stage then drives the follower's gate
        and ``CL`` sits on the follower's output; ``None`` without one.
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
    third_stage_mirror_pole_hz: float | None = None
    output_buffer: tuple[float, float, float] | None = None
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
                   sizing) -> tuple[float, float, float] | None:
    """``(gm, cgs, g_out)`` of a source-follower output buffer, or ``None``.

    ``gm``/``cgs`` are the follower device's (the one gated by the signal);
    ``g_out`` sums the other conductances on its output node: every
    output-stage device's ``gds``.
    """
    devs = _first_present(slot_transistors, _OUTPUT_STAGE_ORDER)
    fol = next((d for d in devs if is_signal_device(d) and d.ref in sizing), None)
    if fol is None:
        return None
    s = sizing[fol.ref]
    g_out = sum(model.gds(d.type, sizing[d.ref].w_um, sizing[d.ref].l_um,
                          sizing[d.ref].ids_a)
                for d in devs if d.ref in sizing)
    return (model.gm(fol.type, s.w_um, s.l_um, s.ids_a),
            model.cgs(fol.type, s.w_um, s.l_um), g_out)


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


def output_swing_headroom(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
) -> tuple[float | None, float | None]:
    """``(below_vdd, above_vss)``: how close to each rail the output can swing.

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


def check_follower_swing(
    view: CircuitView,
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    spec: SizingSpec,
) -> tuple[list[str], bool]:
    """``(warnings, feasible)`` for a follower output stage's level shift.

    ``feasible`` is ``False`` when the level-shifted swing edge
    (:func:`output_swing_headroom`) misses its spec bound by more than
    :data:`_FOLLOWER_SWING_TOL_V`.  No other sizing knob can recover it: the
    shift is the follower's ``|Vgs|``, which even weak inversion cannot take
    below roughly ``Vth``.  Circuits without a follower always pass.
    """
    fol, _bias = _follower(view.slot_transistors)
    if fol is None:
        return [], True
    hi, lo = output_swing_headroom(view, sizing, model, spec)
    if fol.type == "pmos":           # shifts the low edge up
        side, bound, sign = "low", spec.output_swing_min_v, 1.0
        reached = None if lo is None else spec.vss + lo
    else:                            # shifts the high edge down
        side, bound, sign = "high", spec.output_swing_max_v, -1.0
        reached = None if hi is None else spec.vdd - hi
    if (bound is None or reached is None
            or sign * (reached - bound) <= _FOLLOWER_SWING_TOL_V):
        return [], True
    return [f"{fol.ref}: {fol.type} source-follower output stage cannot meet "
            f"the swing spec — its |Vgs| level shift limits the {side} swing "
            f"to {reached:.2f} V against the {bound:.2f} V spec; relax the "
            f"swing spec, raise the supply or use an unbuffered topology."], False


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
    """
    slot_transistors = view.slot_transistors
    mosfets = [d for d, _slot in view.all_transistors.values()]
    ip_devs = slot_transistors.get("input_pair", [])
    ip_resistors = view.slot_resistors.get("input_pair", [])
    tail_net = ip_devs[0].terminals.get("s") if ip_devs else None
    stop = frozenset({tail_net}) if tail_net else frozenset()
    degen = _source_degeneration_r(ip_devs, ip_resistors, resistor_ohms or {})

    def _gm(d: Device) -> float:
        s = sizing.get(d.ref)
        if s is None:
            return 0.0
        return min(model.gm(d.type, s.w_um, s.l_um, s.ids_a),
                   model.gm_ceiling(d.type, s.ids_a, s.l_um))

    def _ids(d: Device | None) -> float:
        s = sizing.get(d.ref) if d is not None else None
        return abs(s.ids_a) if s is not None else 0.0

    def _rout(net: str | None, extra_gd: float = 0.0) -> float:
        if not net:
            return float("inf")
        r = node_rout(net, mosfets, model, sizing, stop, degen)
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
                    rout=_rout(out1, gd_load_r),
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
            r = _looking_in_drain(d, by_drain, model, sizing, stop, degen)
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

    # --- RNMC only: the parasitics its full-model phase margin needs ---
    node_caps: tuple[float, ...] = ()
    ts_mirror_pole = None
    buffer = None
    if view.compensation_scheme == RNMC:
        out_nets = [out1] + [sig.terminals.get("d") if sig else None
                             for sig in signal_devs]
        node_caps = tuple(
            sum(model.cgs(d.type, sizing[d.ref].w_um, sizing[d.ref].l_um)
                for d in mosfets
                if net and d.ref in sizing and d.terminals.get("g") == net)
            for net in out_nets)
        if len(slot_devs) > 1:
            ts_mirror_pole = _mirror_pole_hz(slot_devs[1], mosfets, model, sizing)
        buffer = _output_buffer(slot_transistors, model, sizing)

    k_fs = _first_stage_gain_factor(slot_transistors)
    fully_differential = any(s in slot_transistors
                             for s in ("second_stage_p", "second_stage_n"))
    follower = next((d for d in _first_present(slot_transistors, _OUTPUT_STAGE_ORDER)
                     if is_signal_device(d)), None)

    return StageChain(
        stages=tuple(stages),
        k_fs=k_fs,
        gd_tail=gd_tail,
        gd_output_load=gd_output_load,
        mirror_pole_hz=_mirror_pole_hz(
            slot_transistors.get("load", []), mosfets, model, sizing),
        cc_pf=cc_pf,
        cc2_pf=cc2_pf,
        supply_currents=tuple(supply),
        swing_headroom=output_swing_headroom(view, sizing, model, spec),
        compensation_scheme=view.compensation_scheme,
        node_caps_f=node_caps,
        third_stage_mirror_pole_hz=ts_mirror_pole,
        output_buffer=buffer,
        k_slew=0.5 if fully_differential else k_fs,
        buffer_ids=_ids(follower) if follower is not None else None,
    )
