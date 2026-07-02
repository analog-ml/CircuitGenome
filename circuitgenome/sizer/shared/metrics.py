"""Performance-metric evaluation from a solved sizing.

Small-signal parameters come through a :class:`~.device_model.DeviceModel`
evaluated at the *solved* geometry, so the same code is exact for both Level-1
(geometry-free ``λ·Id``) and gm/Id (LUT).  Shared by both sizers.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from . import equations as eq
from .device_model import DeviceModel
from .models import SizingSpec, TechParams, TransistorSizing
from .preprocess import _first_stage_gain_factor
from .taxonomy import SECOND_STAGE_SLOTS, THIRD_STAGE_SLOTS, is_signal_device


def _evaluate_metrics(
    transistor_sizing: dict[str, TransistorSizing],
    slot_transistors: dict[str, list[Device]],
    cc_pf: float | None,
    tech: TechParams,
    spec: SizingSpec,
    model: DeviceModel,
    cc2_pf: float | None = None,
    gd_load_r: float = 0.0,
    rout1_override: float | None = None,
    rout2_override: float | None = None,
    rout3_override: float | None = None,
    gm1_factor: float = 1.0,
    gd_tail_override: float | None = None,
    gd_out_extra: float = 0.0,
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute performance metrics and safety margins from the solution.

    Small-signal parameters come through ``model`` evaluated at the *solved*
    geometry — exact for both Level-1 (geometry-free λ·Id) and gm/Id (LUT).

    ``rout{1,2,3}_override`` let a caller (the gm/Id pipeline) supply
    cascode-aware stage output resistances; when ``None`` (the Level-1 default)
    the single-device-gds estimate is used unchanged.
    """
    metrics: dict[str, float] = {}
    margins: dict[str, float] = {}
    is_three_stage = any(s in slot_transistors for s in THIRD_STAGE_SLOTS)
    has_second_stage = (
        any(s in slot_transistors for s in SECOND_STAGE_SLOTS) or is_three_stage
    )

    def _sz(ref: str) -> TransistorSizing | None:
        return transistor_sizing.get(ref)

    def _gm(d: Device, s: TransistorSizing) -> float:
        return min(model.gm(d.type, s.w_um, s.l_um, s.ids_a),
                   model.gm_ceiling(d.type, s.ids_a, s.l_um))

    def _gds(d: Device, s: TransistorSizing) -> float:
        return model.gds(d.type, s.w_um, s.l_um, s.ids_a)

    # --- Input pair gm (gm1_factor < 1 for source degeneration) ---
    ip_devs = slot_transistors.get("input_pair", [])
    gm1 = 0.0
    s_ip = _sz(ip_devs[0].ref) if ip_devs else None
    if s_ip:
        gm1 = _gm(ip_devs[0], s_ip) * gm1_factor

    # --- Load ---
    ld_devs = slot_transistors.get("load", [])
    gd_ld = 0.0
    if ld_devs:
        s = _sz(ld_devs[0].ref)
        if s:
            gd_ld = _gds(ld_devs[0], s)

    gd_ip = _gds(ip_devs[0], s_ip) if s_ip else 0.0
    if rout1_override is not None:
        rout1 = rout1_override
    else:
        rout1 = (eq.rout(gd_ip, gd_ld + gd_load_r)
                 if (gd_ip + gd_ld + gd_load_r) > 0 else float("inf"))

    # --- Tail current ---
    tc_devs = slot_transistors.get("tail_current", [])
    gd_tail = 0.0
    if tc_devs:
        s = _sz(tc_devs[0].ref)
        if s:
            gd_tail = _gds(tc_devs[0], s)
    if gd_tail_override is not None:   # resistor tail: gd_tail = 1/R
        gd_tail = gd_tail_override

    # --- Second stage (SE: "second_stage"; FD: use second_stage_p as representative) ---
    ss_devs = (
        slot_transistors.get("second_stage")
        or slot_transistors.get("second_stage_p")
        or slot_transistors.get("second_stage_n")
        or []
    )
    gm2 = 0.0
    gd_ss_n, gd_ss_p = 0.0, 0.0
    ss_load_gd = 0.0  # output conductance of the current-source load (for PSRR)
    ids_2 = spec.ibias * spec.second_stage_current_ratio

    if has_second_stage:
        # gm2 comes from the signal transistor, which may be the NMOS (NMOS-CS
        # stage) or the PMOS (PMOS-CS stage); the partner device is the load.
        for d in ss_devs:
            s = _sz(d.ref)
            if not s:
                continue
            g_d = _gds(d, s)
            if d.type == "nmos":
                gd_ss_n = g_d
            else:
                gd_ss_p = g_d
            if is_signal_device(d):
                gm2 = _gm(d, s)
            else:
                ss_load_gd = g_d
        rout2 = eq.rout(gd_ss_n, gd_ss_p) if (gd_ss_n + gd_ss_p) > 0 else float("inf")
        if rout2_override is not None:
            rout2 = rout2_override
        # Two-stage FD: the resistive-sense CMFB averager loads the output.
        if (not is_three_stage and gd_out_extra > 0.0 and rout2 < float("inf")):
            rout2 = 1.0 / (1.0 / rout2 + gd_out_extra)
    else:
        rout2 = float("inf")

    # --- Third stage (SE: "third_stage"; FD: "third_stage_p" representative) ---
    ts_devs = (
        slot_transistors.get("third_stage")
        or slot_transistors.get("third_stage_p")
        or slot_transistors.get("third_stage_n")
        or []
    )
    gm3 = 0.0
    gd_ts_n, gd_ts_p = 0.0, 0.0
    ids_3 = spec.ibias * spec.third_stage_current_ratio
    if is_three_stage:
        # gm3 comes from the signal transistor (NMOS-CS or PMOS-CS output stage).
        for d in ts_devs:
            s = _sz(d.ref)
            if not s:
                continue
            g_d = _gds(d, s)
            if d.type == "nmos":
                gd_ts_n = g_d
            else:
                gd_ts_p = g_d
            if is_signal_device(d):
                gm3 = _gm(d, s)
        rout3 = eq.rout(gd_ts_n, gd_ts_p) if (gd_ts_n + gd_ts_p) > 0 else float("inf")
        if rout3_override is not None:
            rout3 = rout3_override
        # Three-stage FD: CMFB averager loads the (third-stage) output.
        if gd_out_extra > 0.0 and rout3 < float("inf"):
            rout3 = 1.0 / (1.0 / rout3 + gd_out_extra)
    else:
        rout3 = float("inf")

    # --- Gain ---
    # Single-ended non-mirror first stage delivers k_fs·gm1·Rout1 (k_fs=0.5);
    # mirror / fully-differential first stage delivers the full gm1·Rout1.
    k_fs = _first_stage_gain_factor(slot_transistors)
    if is_three_stage and rout2 < float("inf") and rout3 < float("inf"):
        stage_gains = [k_fs * gm1 * rout1, gm2 * rout2, gm3 * rout3]
    elif has_second_stage and rout2 < float("inf"):
        stage_gains = [k_fs * gm1 * rout1, gm2 * rout2]
    else:
        stage_gains = [k_fs * gm1 * rout1]
    if all(g > 0 for g in stage_gains):
        gain_db = eq.open_loop_gain_db(stage_gains)
        metrics["gain_db"] = gain_db
        if spec.gain_min_db is not None:
            margins["gain_db"] = gain_db - spec.gain_min_db  # +ve → meets spec

    # --- GBW, PM, SR ---
    cc_f = (cc_pf * 1e-12) if cc_pf else None
    cc2_f = (cc2_pf * 1e-12) if cc2_pf else None
    if has_second_stage and cc_f and gm1 > 0:
        # k_fs·gm1 is the transconductance into the Miller loop (halved for a
        # single-ended non-mirror first stage).
        gm1_loop = k_fs * gm1
        gbw = eq.unity_gain_bw(gm1_loop, cc_f)
        metrics["gbw_hz"] = gbw
        if spec.gbw_min_hz is not None:
            margins["gbw_hz"] = gbw - spec.gbw_min_hz

        if is_three_stage and gm2 > 0 and gm3 > 0 and cc2_f:
            pm = eq.phase_margin_three_stage_deg(gm1_loop, gm2, gm3, cc_f, cc2_f, spec.cl)
        elif gm2 > 0:
            pm = eq.phase_margin_two_stage_deg(gm1_loop, gm2, cc_f, spec.cl)
        else:
            pm = None
        if pm is not None:
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
        n_ss = sum(1 for s in SECOND_STAGE_SLOTS if s in slot_transistors)
        supply_currents.append(ids_2 * n_ss)
    if is_three_stage:
        n_ts = sum(1 for s in THIRD_STAGE_SLOTS if s in slot_transistors)
        supply_currents.append(ids_3 * n_ts)
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
    if has_second_stage and gm2 > 0 and ss_load_gd > 0:
        psrr = eq.psrr_db_approx(gm2, ss_load_gd)
        metrics["psrr_db"] = psrr
        if spec.psrr_min_db is not None:
            margins["psrr_db"] = psrr - spec.psrr_min_db

    return metrics, margins
