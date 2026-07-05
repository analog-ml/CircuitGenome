"""Size the non-load resistor blocks of a gm/Id-sized op-amp.

The Level-1 sizer (and gm/Id phase 1) only sized rail-referenced ``load``
resistors; `resistor_bias`, `resistor_tail`, source-**degeneration** r1/r2, and
the **compensation** nulling/series resistors kept the 1 kΩ netlist placeholder,
mis-biasing those variants. This module sizes each per its role and reports the
two metric effects (degeneration on gm1, resistor-tail output conductance on
CMRR).

Sized values flow out through ``SizingResult.resistors`` and
``spice_sim._inject_sizes`` replaces the placeholder, exactly like the load
resistors.
"""
from __future__ import annotations

from dataclasses import dataclass

from circuitgenome.synthesizer.models import Device

from ..shared.models import SizingSpec, TechParams, TransistorSizing
from ..shared.taxonomy import RAILS, is_signal_device
from .blocks import OpAmpBlocks
from .intent import GmIdIntent


# Saturation margin (V) added per device when a cascode-rail walk budgets the
# stack's Vdsat sum.  Placing a node exactly at the planned saturation edge is
# a knife edge: the LUT's Vgs/Vdsat are characterized at Vds=Vdd/2, so the
# realized operating point lands a few tens of mV off and the SPICE bias check
# reads the device as triode.
_CASCODE_SAT_MARGIN_V = 0.1


@dataclass(frozen=True)
class MetricModifiers:
    """How the sized resistor network alters the metric evaluation (Phase 5).

    :param gm1_factor: multiplies the input-pair gm for source degeneration
        (``1/(1+gm1·R)`` = ``1/(1+factor)``); ``1.0`` when none.
    :param gd_tail_override: ``1/R`` of a resistor tail (finite output
        conductance for CMRR), or ``None``.
    :param gd_out_extra: output-node conductance added by a resistive-sense
        CMFB averager (``1/R_sense``), loading the FD output; ``0.0`` when none.
    """
    gm1_factor: float = 1.0
    gd_tail_override: float | None = None
    gd_out_extra: float = 0.0


def size_resistors(
    blocks: OpAmpBlocks,
    slot_resistors: dict[str, list],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    model,
    spec: SizingSpec,
    tech: TechParams,
    intent: GmIdIntent,
    cc_pf: float | None = None,
    cc2_pf: float | None = None,
) -> tuple[dict[str, float], MetricModifiers]:
    """Return ``(extra_resistors, metric_modifiers)``.

    ``extra_resistors`` is ``{ref: ohms}`` for the degeneration / tail / bias /
    cmfb / compensation resistors; the :class:`MetricModifiers` carry their
    metric effects into the evaluation phase.  ``cc_pf``/``cc2_pf`` are the
    planned compensation cap(s) the compensation-slot resistors pair with.
    """
    out: dict[str, float] = {}
    gm1_factor = 1.0
    gd_tail_override: float | None = None
    gd_out_extra = 0.0
    vcm = (spec.vdd + spec.vss) / 2.0

    # --- Source degeneration (input-pair-slot resistors) ---
    ip = blocks.input_pair
    ip_res = slot_resistors.get("input_pair", [])
    if ip_res and intent.degeneration_factor > 0 and ip and ip.mosfets:
        d = ip.mosfets[0]
        s = sizing.get(d.ref)
        if s:
            gm1 = model.gm(d.type, s.w_um, s.l_um, s.ids_a)
            if gm1 > 0:
                r_val = intent.degeneration_factor / gm1
                for r in ip_res:
                    out[r.ref] = r_val
                gm1_factor = 1.0 / (1.0 + intent.degeneration_factor)

    # --- Resistor tail (tail_current-slot resistors): set the tail current ---
    tail_res = slot_resistors.get("tail_current", [])
    if tail_res and ip and ip.mosfets:
        ip_dev = ip.mosfets[0]
        s = sizing.get(ip_dev.ref)
        vgs = abs(model.vgs(ip_dev.type, s.w_um, s.l_um, s.ids_a)) if s else 0.0
        if ip_dev.type == "pmos":           # source toward vdd
            v_tail, vrail = vcm + vgs, spec.vdd
        else:                                # source toward vss
            v_tail, vrail = vcm - vgs, spec.vss
        i_tail = spec.ibias
        r_val = abs(vrail - v_tail) / i_tail if i_tail > 0 else 0.0
        for r in tail_res:
            out[r.ref] = r_val
        if r_val > 0:
            gd_tail_override = 1.0 / r_val

    # --- Tunable bias legs (bias_gen-slot resistors): out_i = ibias·R_i ---
    # Each leg's rail level is derived from what actually consumes the rail
    # (issue #100): a cascode consumer needs Vgs above its stack's saturation
    # floor, a supply-referenced gate needs one Vgs from its supply. Rails
    # whose consumers give no derivable level keep the old representative
    # value.
    bias_res = slot_resistors.get("bias_gen", [])
    if bias_res and spec.ibias > 0:
        consumers = [d for name, b in blocks.blocks.items() if name != "bias_gen"
                     for d in b.mosfets]
        v_fallback = _representative_bias_vgs(blocks, sizing) or 0.5 * (spec.vdd - spec.vss)
        for r in bias_res:
            rail = next((t for t in (r.terminals.get("t1"), r.terminals.get("t2"))
                         if t and t not in RAILS), None)
            v_abs = (_bias_rail_target_v(rail, consumers, sizing, spec)
                     if rail else None)
            v_gate = v_abs - spec.vss if v_abs is not None and v_abs > spec.vss \
                else v_fallback
            out[r.ref] = v_gate / spec.ibias

    # --- CMFB resistive-sense averager (cmfb-slot resistors): large, low-load ---
    cmfb_res = slot_resistors.get("cmfb", [])
    if cmfb_res:
        for r in cmfb_res:
            out[r.ref] = intent.cmfb_sense_r
        # Each output node is loaded by one sense resistor to the (virtual-ground)
        # sense node → adds 1/R_sense to the differential output conductance.
        gd_out_extra = 1.0 / intent.cmfb_sense_r if intent.cmfb_sense_r > 0 else 0.0

    # --- Compensation-slot resistors (nulling / indirect series R) ---
    # The synthesizer emits every resistor at a 1 kΩ placeholder; the series
    # R of `miller_cap_with_nulling_resistor` / `indirect_compensation` must
    # instead track the stage it bridges: R = (Cc+CL)/(gm_out·Cc) places the
    # compensation zero on the output pole (issue #108).
    comp_slots = [(name, rs) for name, rs in slot_resistors.items()
                  if name.startswith("comp")]
    if comp_slots and cc_pf:
        gm_out = _output_stage_gm(blocks, sizing, model)
        if gm_out > 0:
            for name, rs in comp_slots:
                slot_cc_f = (cc2_pf if "comp2" in name and cc2_pf else cc_pf) * 1e-12
                r_val = (slot_cc_f + spec.cl) / (gm_out * slot_cc_f)
                for r in rs:
                    out[r.ref] = r_val

    return out, MetricModifiers(gm1_factor=gm1_factor,
                                gd_tail_override=gd_tail_override,
                                gd_out_extra=gd_out_extra)


def _output_stage_gm(blocks: OpAmpBlocks, sizing: dict[str, TransistorSizing],
                     model) -> float:
    """Realized gm of the output gain stage's signal device (0.0 if unknown).

    The compensation network wraps the last gain stage, so its resistor is
    sized against that stage's gm — third stage when present, else second.
    """
    for name in ("third_stage", "third_stage_p", "third_stage_n",
                 "second_stage", "second_stage_p", "second_stage_n"):
        b = blocks.blocks.get(name)
        for d in (b.mosfets if b else []):
            s = sizing.get(d.ref)
            if s and is_signal_device(d):
                g = model.gm(d.type, s.w_um, s.l_um, s.ids_a)
                if g > 0:
                    return g
    return 0.0


def _bias_rail_target_v(rail_net: str, consumers: list[Device],
                        sizing: dict[str, TransistorSizing],
                        spec: SizingSpec) -> float | None:
    """Absolute voltage the consumers of *rail_net* need it to sit at.

    Per consumer MOSFET gated by the rail (diode-connected consumers are a
    current interface, not a voltage demand — skipped):

    - source on a supply: one planned ``|Vgs|`` from that supply;
    - source on an internal node (cascode gates): the stack's saturation
      floor/ceiling (:func:`_stack_node_v`) plus the consumer's ``|Vgs|``.

    Returns the mean over the derivable consumers (a shared rail with
    conflicting demands gets the compromise a single resistor can offer),
    or ``None`` when no consumer yields a level.
    """
    targets: list[float] = []
    for dev in consumers:
        if dev.terminals.get("g") != rail_net or dev.terminals.get("d") == rail_net:
            continue
        s = sizing.get(dev.ref)
        src = dev.terminals.get("s")
        if not (s and s.vgs_v and src):
            continue
        sign = 1.0 if dev.type == "nmos" else -1.0
        if src in RAILS:
            base = spec.vdd if src == "vdd!" else spec.vss
        else:
            base = _stack_node_v(src, dev, consumers, sizing, spec)
            if base is None:
                continue
        targets.append(base + sign * abs(s.vgs_v))
    return sum(targets) / len(targets) if targets else None


def _stack_node_v(node: str, consumer: Device, mosfets: list[Device],
                  sizing: dict[str, TransistorSizing],
                  spec: SizingSpec) -> float | None:
    """Saturation floor (NMOS) / ceiling (PMOS) of internal *node*.

    Walks the same-type stack from *node* toward the consumer's back rail,
    summing each device's planned ``Vdsat`` plus a saturation margin
    (:data:`_CASCODE_SAT_MARGIN_V`) so everything below (NMOS) / above (PMOS)
    the cascode stays saturated with slack. A signal device in the stack
    (telescopic loads: the cascode sits on the input pair) anchors the walk
    a margin inside its own saturation edge with the gate at the input common
    mode, ``Vcm ∓ (|Vgs| - |Vdsat| - margin)``. Returns ``None`` when the
    stack cannot be traced (no device below, sizing missing, or wrong
    terminating rail).
    """
    sign = 1.0 if consumer.type == "nmos" else -1.0
    vcm = (spec.vdd + spec.vss) / 2.0
    acc = 0.0
    seen: set[str] = set()
    while node not in RAILS:
        if node in seen:
            return None
        seen.add(node)
        dev = next((d for d in mosfets
                    if d.type == consumer.type and d.ref != consumer.ref
                    and d.terminals.get("d") == node), None)
        s = sizing.get(dev.ref) if dev else None
        if not (dev and s and s.vgs_v and s.vds_sat_v):
            return None
        # The Vcm anchor is for an input-pair device riding the tail node; it
        # only applies when the signal device's source is an internal node.
        # A current-mirror's bottom device is gated by its self-biased mirror
        # node (also a non-bias net, so is_signal_device misfires) but sources
        # straight to a supply -- the wide-swing telescopic mirror (issue #129):
        # fall through and terminate at that rail with its Vdsat floor.
        if is_signal_device(dev) and dev.terminals.get("s") not in RAILS:
            return (vcm
                    - sign * (abs(s.vgs_v) - abs(s.vds_sat_v)
                              - _CASCODE_SAT_MARGIN_V)
                    + sign * acc)
        acc += abs(s.vds_sat_v) + _CASCODE_SAT_MARGIN_V
        node = dev.terminals.get("s")
    if (consumer.type == "nmos") == (node == "vdd!"):
        return None  # stack terminated on the wrong supply
    return (spec.vdd if node == "vdd!" else spec.vss) + sign * acc


def _representative_bias_vgs(blocks: OpAmpBlocks,
                             sizing: dict[str, TransistorSizing]) -> float | None:
    """A current-source |Vgs| to bias the resistor-bias rails to (approximate)."""
    bg = blocks.blocks.get("bias_gen")
    devs = bg.mosfets if bg else []
    for d in devs:
        s = sizing.get(d.ref)
        if s and s.vgs_v:
            return abs(s.vgs_v)
    return None
