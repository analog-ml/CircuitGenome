"""
Initial Sizing module — Layer 3 of the CircuitGenome pipeline.

:func:`size_circuit` takes a parsed netlist plus its Layer-2
:class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`
and a performance specification, and returns a
:class:`~.models.SizingResult` with per-transistor W/L values computed via
OR-Tools CP-SAT.

Algorithm
---------
1. **Extract** MOSFET devices per topology slot from the FBR result.
2. **Assign IDS**: KCL + ``spec.ibias`` fully determines every transistor's
   quiescent current before any W/L is chosen.
3. **Derive requirements**: Compute the required transconductances (gm1,
   gm2) and VDS_sat upper bounds from the performance spec using textbook
   two-stage op-amp equations (Shichman-Hodges model).
4. **Build CP-SAT model**: Linearised gm/VDS_sat constraints plus symmetry
   and objective (minimise total gate width).
5. **Solve** and extract the integer W/L solution.
6. **Evaluate** all performance metrics and safety margins.
"""
from __future__ import annotations

import math
from pathlib import Path

from ortools.sat.python import cp_model

from circuitgenome.recognizer.models import (
    FunctionalBlockRecognitionResult,
    ParsedNetlist,
    SubcircuitRecognitionResult,
)
from circuitgenome.synthesizer.models import Device, TopologyTemplate

from . import equations as eq
from .constraints import build_model
from .models import SizingResult, SizingSpec, TechParams, TransistorSizing

# Slots that carry iBias/2 per transistor (both sides of the differential pair).
_HALF_BIAS_SLOTS = frozenset({"input_pair", "load"})
# Slots whose transistors each carry the full iBias.
_FULL_BIAS_SLOTS = frozenset({"tail_current", "bias_gen"})
# Slots with no bias assignment (capacitors etc.).
_CAP_SLOTS = frozenset({"compensation", "comp_p", "comp_n"})
# All second-stage slot names (SE: "second_stage"; FD: "second_stage_p"/"second_stage_n").
_SECOND_STAGE_SLOTS = frozenset({"second_stage", "second_stage_p", "second_stage_n"})

# External supply / bias net names — gate connected to these → current-source load.
_BIAS_NETS = frozenset({"vdd!", "vss!", "gnd!", "ibias"})


def _extract_slot_transistors(
    fbr_result: FunctionalBlockRecognitionResult,
) -> dict[str, list[Device]]:
    """Return {slot_name: [mosfet_Device, ...]} from the FBR assignments."""
    result: dict[str, list[Device]] = {}
    for slot_name, sa in fbr_result.slot_assignments.items():
        mosfets = [d for d in sa.structure.devices if d.type in ("nmos", "pmos")]
        if mosfets:
            result[slot_name] = mosfets
    return result


def _deduplicate(
    slot_transistors: dict[str, list[Device]],
) -> dict[str, tuple[Device, str]]:
    """Return {ref: (Device, slot_name)} with each ref appearing once.

    When a transistor appears in multiple slots (e.g. the tail mirror
    reference appears in both ``tail_current`` and ``bias_gen``), the
    *first* slot encountered wins for the purpose of iDS assignment.
    Priority order: input_pair > load > tail_current > second_stage > bias_gen.
    """
    priority = ["input_pair", "load", "tail_current",
                "second_stage", "second_stage_p", "second_stage_n", "bias_gen"]
    ordered = sorted(
        slot_transistors.keys(),
        key=lambda s: priority.index(s) if s in priority else len(priority),
    )
    seen: dict[str, tuple[Device, str]] = {}
    for slot in ordered:
        for d in slot_transistors[slot]:
            if d.ref not in seen:
                seen[d.ref] = (d, slot)
    return seen


def _assign_ids(
    slot_transistors: dict[str, list[Device]],
    all_transistors: dict[str, tuple[Device, str]],
    spec: SizingSpec,
) -> dict[str, float]:
    """Assign quiescent IDS to each transistor from KCL + spec.ibias."""
    ids_2 = spec.ibias * spec.second_stage_current_ratio
    ids_map: dict[str, float] = {}
    for ref, (device, slot) in all_transistors.items():
        if slot in _HALF_BIAS_SLOTS:
            # Each transistor in a 2-transistor group carries ibias/2.
            # For n devices in the slot (e.g. degenerated pairs), divide equally.
            n = len([d for d in slot_transistors[slot] if d.type == device.type])
            ids_map[ref] = spec.ibias / max(n, 1)
        elif slot in _FULL_BIAS_SLOTS:
            ids_map[ref] = spec.ibias
        elif slot in _SECOND_STAGE_SLOTS:
            ids_map[ref] = ids_2
        else:
            ids_map[ref] = spec.ibias  # conservative default
    return ids_map


def _compute_requirements(
    slot_transistors: dict[str, list[Device]],
    all_transistors: dict[str, tuple[Device, str]],
    ids_map: dict[str, float],
    tech: TechParams,
    spec: SizingSpec,
) -> tuple[dict[str, float], dict[str, float], float | None]:
    """Compute required gm and max VDS_sat per transistor; also Cc.

    Returns ``(gm_req_map, vod_max_map, cc_pf)``.
    """
    is_fd = ("second_stage_p" in slot_transistors or "second_stage_n" in slot_transistors)
    has_second_stage = ("second_stage" in slot_transistors) or is_fd
    ids_2 = spec.ibias * spec.second_stage_current_ratio

    # --- Output conductances at the operating point ---
    ip_devices = slot_transistors.get("input_pair", [])
    ld_devices = slot_transistors.get("load", [])

    def _lam(device: Device) -> float:
        return tech.nmos.lam if device.type == "nmos" else tech.pmos.lam

    gd_ip = eq.gd(_lam(ip_devices[0]), spec.ibias / 2) if ip_devices else 0.0
    gd_ld = eq.gd(_lam(ld_devices[0]), spec.ibias / 2) if ld_devices else 0.0
    rout1 = eq.rout(gd_ip, gd_ld)

    # For FD topologies use second_stage_p as the representative path (symmetric).
    ss_devices = (
        slot_transistors.get("second_stage")
        or slot_transistors.get("second_stage_p")
        or slot_transistors.get("second_stage_n")
        or []
    )
    ss_nmos = next((d for d in ss_devices if d.type == "nmos"), None)
    ss_pmos = next((d for d in ss_devices if d.type == "pmos"), None)
    gd_n2 = eq.gd(tech.nmos.lam, ids_2) if ss_nmos else 0.0
    gd_p2 = eq.gd(tech.pmos.lam, ids_2) if ss_pmos else 0.0
    rout2 = eq.rout(gd_n2, gd_p2) if (gd_n2 + gd_p2) > 0 else float("inf")

    # --- gm1 lower bound from CMRR (independent of Cc — compute first) ---
    gm1_req = 0.0
    gm2_req = 0.0
    if spec.cmrr_min_db:
        tc_devices = slot_transistors.get("tail_current", [])
        if tc_devices:
            gd_tail = eq.gd(_lam(tc_devices[0]), spec.ibias)
            cmrr_lin = 10.0 ** (spec.cmrr_min_db / 20.0)
            gm1_req = max(gm1_req, cmrr_lin * 2.0 * gd_tail)

    # --- Compensation cap determination (two-stage only) ---
    cc_pf: float | None = None
    cc_f: float = 0.0
    if has_second_stage:
        cc_min_f = tech.cap.min * 1e-12
        cc_max_f = tech.cap.max * 1e-12

        # From slew rate: Cc = iBias / SR
        cc_from_sr = (
            spec.ibias / spec.slew_rate_min_vps
            if spec.slew_rate_min_vps
            else cc_min_f
        )
        cc_f = max(cc_min_f, min(cc_from_sr, cc_max_f))

    if has_second_stage and cc_f > 0:
        # From GBW: gm1 = 2π·GBW·Cc  (with the SR-bounded Cc).
        # This is the primary gm1 driver for two-stage; Cc stays within the
        # SR bound because we do NOT inflate Cc here to accommodate gain.
        if spec.gbw_min_hz:
            gm1_req = max(gm1_req, 2.0 * math.pi * spec.gbw_min_hz * cc_f)

        # Cc is now fixed (from SR and initial GBW-with-SR Cc).  Recompute
        # only if CMRR pushed gm1 above the GBW baseline, which would require
        # a larger Cc to maintain GBW.
        if spec.gbw_min_hz and gm1_req > 0.0:
            cc_f = max(cc_f, gm1_req / (2.0 * math.pi * spec.gbw_min_hz))
            cc_f = min(cc_f, cc_max_f)

        # From gain: A0 = gm1·Rout1·gm2·Rout2.
        # gm1 is now fixed; solve for the gm2 needed to meet total gain.
        if spec.gain_min_db and rout1 < float("inf") and rout2 < float("inf"):
            A0 = 10.0 ** (spec.gain_min_db / 20.0)
            if gm1_req > 0.0 and rout1 * rout2 > 0:
                # Derive gm2 from the gain that gm1 hasn't covered.
                gm2_from_gain = A0 / (gm1_req * rout1 * rout2)
                gm2_req = max(gm2_req, gm2_from_gain)
            else:
                # Fallback: equal-gain split when gm1 isn't yet known.
                per_stage = math.sqrt(A0 / (rout1 * rout2))
                gm1_req = max(gm1_req, per_stage)
                gm2_req = max(gm2_req, per_stage)

        # From PM: gm2 = gm1·CL / (Cc·tan(90°−PM)).
        # Use the worst-case (ceiling) gm1 that the integer W grid can produce,
        # so that gm2 stays large enough even when gm1 is rounded up.
        if spec.phase_margin_min_deg and gm1_req > 0.0 and ip_devices:
            ip_dev = ip_devices[0]
            ip_params = tech.nmos if ip_dev.type == "nmos" else tech.pmos
            ids_ip = spec.ibias / max(
                len([d for d in ip_devices if d.type == ip_dev.type]), 1
            )
            lhs = 2.0 * ip_params.mu_cox * ids_ip  # coefficient of W in gm² constraint
            l_min_int = round(tech.length.min / tech.length.step)
            w_ceil_int = math.ceil(gm1_req ** 2 * l_min_int / lhs)
            w_ceil_int = min(w_ceil_int, round(tech.width.max / tech.width.step))
            gm1_worst = math.sqrt(lhs * w_ceil_int / l_min_int)
            pm_rad = math.radians(spec.phase_margin_min_deg)
            gm2_req = max(
                gm2_req,
                gm1_worst * spec.cl / (cc_f * math.tan(math.pi / 2.0 - pm_rad)),
            )

        cc_pf = cc_f * 1e12

    else:
        # One-stage: gain = gm1·Rout1
        if spec.gain_min_db and rout1 < float("inf"):
            A0 = 10.0 ** (spec.gain_min_db / 20.0)
            gm1_req = max(gm1_req, A0 / rout1)

    # --- Map requirements to individual transistors ---
    gm_req_map: dict[str, float] = {}
    vod_max_map: dict[str, float] = {}

    for ref, (device, slot) in all_transistors.items():
        if slot == "input_pair":
            gm_req_map[ref] = gm1_req
        elif slot in _SECOND_STAGE_SLOTS:
            # Only the signal transistor (gate driven by first-stage output)
            # needs a gm requirement; the load transistor is a current source.
            gate = device.terminals.get("g", "")
            is_signal = gate and gate not in _BIAS_NETS and not gate.startswith("net_bias")
            gm_req_map[ref] = gm2_req if is_signal else 0.0
        # All other slots: no explicit gm requirement (sized by min W/L)

    # --- VDS_sat upper bounds from output swing specs ---
    vdd = spec.vdd
    vss = spec.vss

    # All second-stage device lists (one for SE, two for FD).
    all_ss_device_lists = [
        slot_transistors[s] for s in _SECOND_STAGE_SLOTS if s in slot_transistors
    ]

    if spec.output_swing_max_v is not None:
        vds_sat_max = vdd - spec.output_swing_max_v
        if vds_sat_max > 0.0:
            # Load transistors constrain the high-side swing.
            for d in ld_devices:
                vod_max_map[d.ref] = vds_sat_max
            # Second-stage PMOS (current-source load) constrains swing on every path.
            for devs in all_ss_device_lists:
                for d in devs:
                    if d.type == "pmos":
                        vod_max_map[d.ref] = min(
                            vod_max_map.get(d.ref, float("inf")), vds_sat_max
                        )

    if spec.output_swing_min_v is not None:
        vds_sat_max_low = spec.output_swing_min_v - vss
        if vds_sat_max_low > 0.0:
            # Second-stage NMOS constrains the low-side swing on every path.
            for devs in all_ss_device_lists:
                for d in devs:
                    if d.type == "nmos":
                        vod_max_map[d.ref] = min(
                            vod_max_map.get(d.ref, float("inf")), vds_sat_max_low
                        )

    return gm_req_map, vod_max_map, cc_pf


def _evaluate_metrics(
    transistor_sizing: dict[str, TransistorSizing],
    slot_transistors: dict[str, list[Device]],
    cc_pf: float | None,
    tech: TechParams,
    spec: SizingSpec,
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute performance metrics and safety margins from the solution."""
    metrics: dict[str, float] = {}
    margins: dict[str, float] = {}
    is_fd = ("second_stage_p" in slot_transistors or "second_stage_n" in slot_transistors)
    has_second_stage = ("second_stage" in slot_transistors) or is_fd

    def _sz(ref: str) -> TransistorSizing | None:
        return transistor_sizing.get(ref)

    def _mu(device: Device) -> float:
        return tech.nmos.mu_cox if device.type == "nmos" else tech.pmos.mu_cox

    def _lam(device: Device) -> float:
        return tech.nmos.lam if device.type == "nmos" else tech.pmos.lam

    # --- Input pair gm ---
    ip_devs = slot_transistors.get("input_pair", [])
    gm1 = 0.0
    if ip_devs:
        d = ip_devs[0]
        s = _sz(d.ref)
        if s:
            gm1 = eq.gm(_mu(d), s.w_um, s.l_um, s.ids_a)

    # --- Load ---
    ld_devs = slot_transistors.get("load", [])
    gd_ld = 0.0
    if ld_devs:
        d = ld_devs[0]
        s = _sz(d.ref)
        if s:
            gd_ld = eq.gd(_lam(d), s.ids_a)

    gd_ip = eq.gd(
        tech.nmos.lam if ip_devs and ip_devs[0].type == "nmos" else tech.pmos.lam,
        spec.ibias / 2,
    ) if ip_devs else 0.0
    rout1 = eq.rout(gd_ip, gd_ld) if (gd_ip + gd_ld) > 0 else float("inf")

    # --- Tail current ---
    tc_devs = slot_transistors.get("tail_current", [])
    gd_tail = 0.0
    if tc_devs:
        d = tc_devs[0]
        gd_tail = eq.gd(_lam(d), spec.ibias)

    # --- Second stage (SE: "second_stage"; FD: use second_stage_p as representative) ---
    ss_devs = (
        slot_transistors.get("second_stage")
        or slot_transistors.get("second_stage_p")
        or slot_transistors.get("second_stage_n")
        or []
    )
    gm2 = 0.0
    gd_ss_n, gd_ss_p = 0.0, 0.0
    ss_pmos_bias_gd = 0.0
    ids_2 = spec.ibias * spec.second_stage_current_ratio

    if has_second_stage:
        ss_nmos = next((d for d in ss_devs if d.type == "nmos"), None)
        ss_pmos = next((d for d in ss_devs if d.type == "pmos"), None)
        if ss_nmos:
            s = _sz(ss_nmos.ref)
            if s:
                gate = ss_nmos.terminals.get("g", "")
                is_signal = gate and gate not in _BIAS_NETS and not gate.startswith("net_bias")
                if is_signal:
                    gm2 = eq.gm(_mu(ss_nmos), s.w_um, s.l_um, s.ids_a)
                gd_ss_n = eq.gd(tech.nmos.lam, s.ids_a)
        if ss_pmos:
            s = _sz(ss_pmos.ref)
            if s:
                gd_ss_p = eq.gd(tech.pmos.lam, s.ids_a)
                ss_pmos_bias_gd = gd_ss_p
        rout2 = eq.rout(gd_ss_n, gd_ss_p) if (gd_ss_n + gd_ss_p) > 0 else float("inf")
    else:
        rout2 = float("inf")

    # --- Gain ---
    if has_second_stage and rout2 < float("inf"):
        stage_gains = [gm1 * rout1, gm2 * rout2]
    else:
        stage_gains = [gm1 * rout1]
    if all(g > 0 for g in stage_gains):
        gain_db = eq.open_loop_gain_db(stage_gains)
        metrics["gain_db"] = gain_db
        if spec.gain_min_db is not None:
            margins["gain_db"] = gain_db - spec.gain_min_db  # +ve → meets spec

    # --- GBW, PM, SR (two-stage only) ---
    cc_f = (cc_pf * 1e-12) if cc_pf else None
    if has_second_stage and cc_f and gm1 > 0:
        gbw = eq.unity_gain_bw(gm1, cc_f)
        metrics["gbw_hz"] = gbw
        if spec.gbw_min_hz is not None:
            margins["gbw_hz"] = gbw - spec.gbw_min_hz

        if gm2 > 0:
            pm = eq.phase_margin_two_stage_deg(gm1, gm2, cc_f, spec.cl)
            metrics["phase_margin_deg"] = pm
            if spec.phase_margin_min_deg is not None:
                margins["phase_margin_deg"] = pm - spec.phase_margin_min_deg

        sr = eq.slew_rate_vps(spec.ibias, cc_f)
        metrics["slew_rate_vps"] = sr
        if spec.slew_rate_min_vps is not None:
            margins["slew_rate_vps"] = sr - spec.slew_rate_min_vps

    # --- Power ---
    # Supply currents: tail (ibias), second stage (ids_2), bias_gen (ibias approx)
    bg_devs = slot_transistors.get("bias_gen", [])
    n_bias_legs = len([d for d in bg_devs if d.type in ("nmos", "pmos")])
    supply_currents = [spec.ibias]  # tail
    if has_second_stage:
        n_ss = sum(1 for s in _SECOND_STAGE_SLOTS if s in slot_transistors)
        supply_currents.append(ids_2 * n_ss)
    supply_currents.append(spec.ibias * max(n_bias_legs, 1))  # bias gen approx
    power = eq.quiescent_power(spec.vdd, spec.vss, supply_currents)
    metrics["power_w"] = power
    if spec.power_max_w is not None:
        margins["power_w"] = spec.power_max_w - power  # +ve → meets spec

    # --- Output swing (from VDS_sat of second-stage or load transistors) ---
    if spec.output_swing_max_v is not None and ss_devs:
        sp = next((d for d in ss_devs if d.type == "pmos"), None)
        if sp and _sz(sp.ref):
            s = _sz(sp.ref)
            assert s is not None
            vout_max = spec.vdd - s.vds_sat_v
            metrics["output_swing_max_v"] = vout_max
            margins["output_swing_max_v"] = vout_max - spec.output_swing_max_v

    if spec.output_swing_min_v is not None and ss_devs:
        sn = next((d for d in ss_devs if d.type == "nmos"), None)
        if sn and _sz(sn.ref):
            s = _sz(sn.ref)
            assert s is not None
            vout_min = spec.vss + s.vds_sat_v
            metrics["output_swing_min_v"] = vout_min
            margins["output_swing_min_v"] = spec.output_swing_min_v - vout_min

    # --- CMRR ---
    if gm1 > 0 and gd_tail > 0:
        cmrr = eq.cmrr_db(gm1, gd_tail)
        metrics["cmrr_db"] = cmrr
        if spec.cmrr_min_db is not None:
            margins["cmrr_db"] = cmrr - spec.cmrr_min_db

    # --- PSRR (approximate, two-stage) ---
    if has_second_stage and gm2 > 0 and ss_pmos_bias_gd > 0:
        psrr = eq.psrr_db_approx(gm2, ss_pmos_bias_gd)
        metrics["psrr_db"] = psrr
        if spec.psrr_min_db is not None:
            margins["psrr_db"] = psrr - spec.psrr_min_db

    return metrics, margins


def size_circuit(
    parsed: ParsedNetlist,
    sr_result: SubcircuitRecognitionResult,
    fbr_result: FunctionalBlockRecognitionResult,
    topology: TopologyTemplate,
    tech: TechParams,
    spec: SizingSpec,
    *,
    time_limit_s: float = 30.0,
) -> SizingResult:
    """Compute initial transistor W/L values satisfying ``spec``.

    :param parsed: Layer-0 parsed netlist.
    :param sr_result: Layer-1 subcircuit recognition result (unused directly
        but kept for API symmetry with the pipeline).
    :param fbr_result: Layer-2 FBR result from
        :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`.
        Must use **topology mode** (not group-by-category).
    :param topology: Topology template corresponding to ``fbr_result``.
    :param tech: Technology parameters (from :func:`~.loader.load_tech`).
    :param spec: Performance specification.
    :param time_limit_s: CP-SAT solver time limit in seconds.
    :returns: :class:`~.models.SizingResult` with per-transistor sizing,
        compensation cap, computed metrics, and safety margins.
    """
    slot_transistors = _extract_slot_transistors(fbr_result)
    all_transistors = _deduplicate(slot_transistors)
    ids_map = _assign_ids(slot_transistors, all_transistors, spec)
    gm_req_map, vod_max_map, cc_pf = _compute_requirements(
        slot_transistors, all_transistors, ids_map, tech, spec
    )

    model, W_vars, L_vars = build_model(
        all_transistors, slot_transistors, ids_map, gm_req_map, vod_max_map, tech
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.solve(model)
    status_name = solver.status_name(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SizingResult(
            transistors={},
            cc_pf=cc_pf,
            metrics={},
            margins={},
            solver_status=status_name,
        )

    # Extract solution: convert integer step-units back to µm.
    w_step = tech.width.step
    l_step = tech.length.step
    transistor_sizing: dict[str, TransistorSizing] = {}
    for ref, (device, _slot) in all_transistors.items():
        w_um = solver.value(W_vars[ref]) * w_step
        l_um = solver.value(L_vars[ref]) * l_step
        ids_a = ids_map[ref]
        params = tech.nmos if device.type == "nmos" else tech.pmos
        vgs = eq.vgs_from_ids(params.mu_cox, w_um, l_um, ids_a, params.vth)
        vds = eq.vds_sat(params.mu_cox, w_um, l_um, ids_a)
        transistor_sizing[ref] = TransistorSizing(
            ref=ref, w_um=w_um, l_um=l_um, ids_a=ids_a, vgs_v=vgs, vds_sat_v=vds
        )

    metrics, margins = _evaluate_metrics(
        transistor_sizing, slot_transistors, cc_pf, tech, spec
    )
    return SizingResult(
        transistors=transistor_sizing,
        cc_pf=cc_pf,
        metrics=metrics,
        margins=margins,
        solver_status=status_name,
    )
