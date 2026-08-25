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
    :param gd_stage2_load: second-stage load output conductance in A/V (PSRR).
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
    gd_stage2_load: float = 0.0
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
    on a folded cascode those are different nets.
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
    if out1 is None and ip_devs:
        out1 = ip_devs[0].terminals.get("d")   # one-stage: the pair's own drain
    stages = [Stage(gm=_gm(ip_devs[0]) if ip_devs else 0.0,
                    rout=_rout(out1, gd_load_r))]

    # --- Stages 2 and 3: each numbered gain slot's signal device ---
    for sig in signal_devs:
        stages.append(Stage(gm=_gm(sig) if sig is not None else 0.0,
                            rout=_rout(sig.terminals.get("d") if sig else None)))

    # --- Second-stage load conductance (PSRR) ---
    gd_stage2_load = 0.0
    if slot_devs:
        for d in slot_devs[0]:
            s = sizing.get(d.ref)
            if s is not None and not is_signal_device(d):
                gd_stage2_load = model.gds(d.type, s.w_um, s.l_um, s.ids_a)

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
        gd_stage2_load=gd_stage2_load,
        cc_pf=cc_pf,
        cc2_pf=cc2_pf,
        supply_currents=tuple(supply),
        swing_vdsat=(_vdsat("pmos"), _vdsat("nmos")),
    )
