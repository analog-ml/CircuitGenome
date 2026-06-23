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
from .device_model import CURRENT_SOURCE, SIGNAL, DeviceModel, build_device_model
from .gmid_geometry import assign_geometry_gmid
from .models import SizingResult, SizingSpec, TechParams, TransistorSizing

# Slots that carry iBias/2 per transistor (both sides of the differential pair).
_HALF_BIAS_SLOTS = frozenset({"input_pair", "load"})
# Slots whose transistors each carry the full iBias.
_FULL_BIAS_SLOTS = frozenset({"tail_current", "bias_gen"})
# Slots with no bias assignment (capacitors etc.).
_CAP_SLOTS = frozenset({
    "compensation", "comp_p", "comp_n",
    "comp1", "comp2", "comp1_p", "comp1_n", "comp2_p", "comp2_n",
})
# All second-stage slot names (SE: "second_stage"; FD: "second_stage_p"/"second_stage_n").
_SECOND_STAGE_SLOTS = frozenset({"second_stage", "second_stage_p", "second_stage_n"})
# All third-stage slot names (SE: "third_stage"; FD: "third_stage_p"/"third_stage_n").
_THIRD_STAGE_SLOTS = frozenset({"third_stage", "third_stage_p", "third_stage_n"})

# External supply / bias net names — gate connected to these → current-source load.
_BIAS_NETS = frozenset({"vdd!", "vss!", "gnd!", "ibias"})

# All gain-stage slot names (used by the topology-mismatch guard).
_STAGE_SLOTS = _SECOND_STAGE_SLOTS | _THIRD_STAGE_SLOTS


def _is_signal_dev(device: Device) -> bool:
    """True if ``device``'s gate is driven by a signal net (not a bias rail).

    The signal transistor of a gain stage is the one whose gate is the previous
    stage's output; the partner device is a current-source load (gate on a bias
    net). Used to pick the gm-contributing device regardless of NMOS/PMOS polarity.
    """
    gate = device.terminals.get("g", "")
    return bool(gate) and gate not in _BIAS_NETS and not gate.startswith("net_bias")


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


def _extract_slot_resistors(
    fbr_result: FunctionalBlockRecognitionResult,
) -> dict[str, list[Device]]:
    """Return {slot_name: [resistor_Device, ...]} from the FBR assignments."""
    result: dict[str, list[Device]] = {}
    for slot_name, sa in fbr_result.slot_assignments.items():
        rs = [d for d in sa.structure.devices if d.type == "resistor"]
        if rs:
            result[slot_name] = rs
    return result


# Overdrive (V) used when sizing a load resistor so its DC drop biases the
# driven device into conduction: V_node ≈ Vth + this.
_RESISTOR_LOAD_OVERDRIVE = 0.15


def _size_load_resistors(
    slot_resistors: dict[str, list[Device]], spec: SizingSpec, tech: TechParams,
) -> dict[str, float]:
    """Size resistor-load devices so the first-stage output biases on.

    Each load resistor carries the input-pair branch current ``ibias/2``.  The
    value is chosen so the DC drop places the first-stage output node at a level
    that turns the driven device on:

    * resistor to **gnd** (PMOS-input loads): ``V_node = vth_n + Vov`` →
      ``R = V_node / (ibias/2)``.
    * resistor from **vdd** (NMOS-input loads): drop ``= |vth_p| + Vov`` →
      ``R = drop / (ibias/2)``.

    Only the ``load`` slot is sized; other resistor roles keep their netlist
    value.  Returns ``{ref: ohms}``.
    """
    out: dict[str, float] = {}
    branch_i = spec.ibias / 2.0
    if branch_i <= 0:
        return out
    vov = _RESISTOR_LOAD_OVERDRIVE
    for r in slot_resistors.get("load", []):
        nets = [str(n).lower() for n in r.terminals.values()]
        if any("gnd" in n or n in ("0", "vss!") for n in nets):
            v = tech.nmos.vth + vov
        elif any("vdd" in n for n in nets):
            v = abs(tech.pmos.vth) + vov
        else:
            continue  # not a rail-referenced load resistor
        if v > 0:
            out[r.ref] = v / branch_i
    return out


def _check_topology_match(
    slot_transistors: dict[str, list[Device]], topology_name: str
) -> list[str]:
    """Warn when the netlist does not realise the chosen topology.

    Every gain-stage slot of a valid circuit holds exactly one signal
    transistor. A stage slot with **no** signal device (e.g. bias-generator
    leftovers shoehorned into ``second_stage_p`` when a single-ended netlist is
    sized against a fully-differential topology) signals a ``--topology``
    mismatch — which would otherwise silently drop the gain/PM/PSRR metrics.
    """
    warnings: list[str] = []
    for slot in sorted(_STAGE_SLOTS):
        devs = slot_transistors.get(slot)
        if devs and not any(_is_signal_dev(d) for d in devs):
            warnings.append(
                f"stage slot '{slot}' has no signal transistor — the netlist may "
                f"not match topology '{topology_name}' (check --topology, e.g. "
                f"single-ended vs fully-differential)."
            )
    return warnings


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
                "second_stage", "second_stage_p", "second_stage_n",
                "third_stage", "third_stage_p", "third_stage_n", "bias_gen"]
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
        elif slot in _THIRD_STAGE_SLOTS:
            ids_map[ref] = spec.ibias * spec.third_stage_current_ratio
        else:
            ids_map[ref] = spec.ibias  # conservative default
    return ids_map


def _compute_requirements(
    slot_transistors: dict[str, list[Device]],
    all_transistors: dict[str, tuple[Device, str]],
    ids_map: dict[str, float],
    tech: TechParams,
    spec: SizingSpec,
    model: DeviceModel,
    gd_load_r: float = 0.0,
) -> tuple[dict[str, float], dict[str, float], float | None, float | None]:
    """Compute required gm and max VDS_sat per transistor; also Cc1 and Cc2.

    Returns ``(gm_req_map, vod_max_map, cc_pf, cc2_pf)``.  Output conductances
    come through ``model`` so the gm/Id path uses LUT-accurate gds; the Level-1
    model reproduces the geometry-free ``λ·Id`` exactly.
    """
    is_three_stage = any(s in slot_transistors for s in _THIRD_STAGE_SLOTS)
    has_second_stage = (
        any(s in slot_transistors for s in _SECOND_STAGE_SLOTS) or is_three_stage
    )
    ids_2 = spec.ibias * spec.second_stage_current_ratio

    # --- Output conductances at the operating point ---
    ip_devices = slot_transistors.get("input_pair", [])
    ld_devices = slot_transistors.get("load", [])

    def _gds_est(device: Device, ids: float) -> float:
        """Pre-geometry gds estimate, role-aware (signal vs current source)."""
        role = SIGNAL if _is_signal_dev(device) else CURRENT_SOURCE
        return model.gds_estimate(device.type, ids, role)

    gd_ip = _gds_est(ip_devices[0], spec.ibias / 2) if ip_devices else 0.0
    gd_ld = _gds_est(ld_devices[0], spec.ibias / 2) if ld_devices else 0.0
    # Resistor-load conductance (1/R) loads the first-stage output node.
    rout1 = eq.rout(gd_ip, gd_ld + gd_load_r)

    # For FD topologies use second_stage_p as the representative path (symmetric).
    ss_devices = (
        slot_transistors.get("second_stage")
        or slot_transistors.get("second_stage_p")
        or slot_transistors.get("second_stage_n")
        or []
    )
    ss_nmos = next((d for d in ss_devices if d.type == "nmos"), None)
    ss_pmos = next((d for d in ss_devices if d.type == "pmos"), None)
    gd_n2 = _gds_est(ss_nmos, ids_2) if ss_nmos else 0.0
    gd_p2 = _gds_est(ss_pmos, ids_2) if ss_pmos else 0.0
    rout2 = eq.rout(gd_n2, gd_p2) if (gd_n2 + gd_p2) > 0 else float("inf")

    # Third-stage output conductances (three-stage topologies only).
    ids_3 = spec.ibias * spec.third_stage_current_ratio
    ts_devices = (
        slot_transistors.get("third_stage")
        or slot_transistors.get("third_stage_p")
        or slot_transistors.get("third_stage_n")
        or []
    )
    ts_nmos = next((d for d in ts_devices if d.type == "nmos"), None)
    ts_pmos = next((d for d in ts_devices if d.type == "pmos"), None)
    gd_n3 = _gds_est(ts_nmos, ids_3) if ts_nmos else 0.0
    gd_p3 = _gds_est(ts_pmos, ids_3) if ts_pmos else 0.0
    rout3 = eq.rout(gd_n3, gd_p3) if (gd_n3 + gd_p3) > 0 else float("inf")

    # --- gm1 lower bound from CMRR (independent of Cc — compute first) ---
    gm1_req = 0.0
    gm2_req = 0.0
    gm3_req = 0.0
    if spec.cmrr_min_db:
        tc_devices = slot_transistors.get("tail_current", [])
        if tc_devices:
            gd_tail = _gds_est(tc_devices[0], spec.ibias)
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

    # Cc2 for three-stage (inner cap = Cc1/4); None for two-stage and one-stage.
    cc2_pf: float | None = None
    cc2_f: float = 0.0

    if has_second_stage and cc_f > 0:
        # From GBW: gm1 = 2π·GBW·Cc  (with the SR-bounded Cc).
        # This is the primary gm1 driver; Cc stays within the SR bound because
        # we do NOT inflate Cc here to accommodate gain.
        if spec.gbw_min_hz:
            gm1_req = max(gm1_req, 2.0 * math.pi * spec.gbw_min_hz * cc_f)

        # Recompute Cc only if CMRR pushed gm1 above the GBW baseline.
        if spec.gbw_min_hz and gm1_req > 0.0:
            cc_f = max(cc_f, gm1_req / (2.0 * math.pi * spec.gbw_min_hz))
            cc_f = min(cc_f, cc_max_f)

        if is_three_stage:
            # Three-stage: inner cap = Cc1/4.
            cc2_f = cc_f / 4.0
            cc2_pf = cc2_f * 1e12

            # Phase margin (split phase budget equally between two non-dominant poles).
            # Inner pole: ωp2 ≈ gm2/Cc2; output pole: ωp3 ≈ gm3/CL.
            if spec.phase_margin_min_deg and gm1_req > 0.0:
                half_lag = math.radians((90.0 - spec.phase_margin_min_deg) / 2.0)
                t = math.tan(half_lag)
                gm2_req = max(gm2_req, gm1_req * cc2_f / (cc_f * t))
                gm3_req = max(gm3_req, gm1_req * spec.cl / (cc_f * t))

            # Gain: A0 = gm1·Rout1·gm2·Rout2·gm3·Rout3.
            # With gm2_req now determined, solve for the gm3 needed for gain.
            if (spec.gain_min_db
                    and rout1 < float("inf")
                    and rout2 < float("inf")
                    and rout3 < float("inf")
                    and gm1_req > 0.0
                    and gm2_req > 0.0):
                A0 = 10.0 ** (spec.gain_min_db / 20.0)
                gm3_from_gain = A0 / (gm1_req * rout1 * gm2_req * rout2 * rout3)
                gm3_req = max(gm3_req, gm3_from_gain)

        else:
            # Two-stage: gain A0 = gm1·Rout1·gm2·Rout2.
            if spec.gain_min_db and rout1 < float("inf") and rout2 < float("inf"):
                A0 = 10.0 ** (spec.gain_min_db / 20.0)
                if gm1_req > 0.0 and rout1 * rout2 > 0:
                    gm2_from_gain = A0 / (gm1_req * rout1 * rout2)
                    gm2_req = max(gm2_req, gm2_from_gain)
                else:
                    per_stage = math.sqrt(A0 / (rout1 * rout2))
                    gm1_req = max(gm1_req, per_stage)
                    gm2_req = max(gm2_req, per_stage)

            # From PM: gm2 = gm1·CL / (Cc·tan(90°−PM)).
            if spec.phase_margin_min_deg and gm1_req > 0.0 and ip_devices:
                if model.is_gmid:
                    # Procedural geometry snaps then evaluates PM from the actual
                    # geometry, so use gm1_req directly (no grid-ceiling inflation).
                    gm1_eff = gm1_req
                else:
                    # Level-1 + CP-SAT: anticipate W rounding up to the grid by
                    # using the worst-case (ceiling) gm1 from the integer W grid.
                    ip_dev = ip_devices[0]
                    ip_params = tech.nmos if ip_dev.type == "nmos" else tech.pmos
                    ids_ip = spec.ibias / max(
                        len([d for d in ip_devices if d.type == ip_dev.type]), 1
                    )
                    lhs = 2.0 * ip_params.mu_cox * ids_ip
                    l_min_int = round(tech.length.min / tech.length.step)
                    w_ceil_int = math.ceil(gm1_req ** 2 * l_min_int / lhs)
                    w_ceil_int = min(w_ceil_int, round(tech.width.max / tech.width.step))
                    gm1_eff = math.sqrt(lhs * w_ceil_int / l_min_int)
                pm_rad = math.radians(spec.phase_margin_min_deg)
                gm2_req = max(
                    gm2_req,
                    gm1_eff * spec.cl / (cc_f * math.tan(math.pi / 2.0 - pm_rad)),
                )

        cc_pf = cc_f * 1e12

    else:
        # One-stage: gain = gm1·Rout1
        if spec.gain_min_db and rout1 < float("inf"):
            A0 = 10.0 ** (spec.gain_min_db / 20.0)
            gm1_req = max(gm1_req, A0 / rout1)

    # --- Cap gm requirements at the physical (weak-inversion) ceiling ---
    # The square-law model has no gm ceiling, so without this the sizer would size
    # for a gm the device can only reach by sliding into weak inversion (where the
    # real gm is far lower).  Clamp each requirement to gm ≤ gm_ceiling(IDS); a
    # binding clamp means the spec needs more bias current than the device can
    # physically deliver, surfaced as a warning (the shortfall also shows in the
    # reported margins).
    def _ceil(ids: float, devs: list[Device]) -> float:
        sig = next((d for d in devs if _is_signal_dev(d)), devs[0] if devs else None)
        dtype = sig.type if sig else "nmos"
        return model.gm_ceiling(dtype, ids, tech.length.min)

    gm_ceiling_warnings: list[str] = []
    gm1_ceil = _ceil(spec.ibias / 2.0, ip_devices)
    if gm1_req > gm1_ceil:
        gm1_req = gm1_ceil
        gm_ceiling_warnings.append(
            "input-pair gm requirement exceeds the weak-inversion ceiling at "
            "ibias/2 — increase ibias or relax GBW/gain (the design will fall short).")
    gm2_ceil = _ceil(spec.ibias * spec.second_stage_current_ratio, ss_devices)
    if gm2_req > gm2_ceil:
        gm2_req = gm2_ceil
        gm_ceiling_warnings.append(
            "second-stage gm requirement exceeds the weak-inversion ceiling — "
            "increase second_stage_current_ratio/ibias or relax gain.")
    gm3_ceil = _ceil(spec.ibias * spec.third_stage_current_ratio, ts_devices)
    if gm3_req > gm3_ceil:
        gm3_req = gm3_ceil
        gm_ceiling_warnings.append(
            "third-stage gm requirement exceeds the weak-inversion ceiling — "
            "increase third_stage_current_ratio/ibias or relax gain.")

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
        elif slot in _THIRD_STAGE_SLOTS:
            gate = device.terminals.get("g", "")
            is_signal = gate and gate not in _BIAS_NETS and not gate.startswith("net_bias")
            gm_req_map[ref] = gm3_req if is_signal else 0.0
        # All other slots: no explicit gm requirement (sized by min W/L)

    # --- VDS_sat upper bounds from output swing specs ---
    vdd = spec.vdd
    vss = spec.vss

    # All second- and third-stage device lists (constrain output swing on every path).
    all_ss_device_lists = [
        slot_transistors[s]
        for s in (*_SECOND_STAGE_SLOTS, *_THIRD_STAGE_SLOTS)
        if s in slot_transistors
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

    return gm_req_map, vod_max_map, cc_pf, cc2_pf, gm_ceiling_warnings


def _evaluate_metrics(
    transistor_sizing: dict[str, TransistorSizing],
    slot_transistors: dict[str, list[Device]],
    cc_pf: float | None,
    tech: TechParams,
    spec: SizingSpec,
    model: DeviceModel,
    cc2_pf: float | None = None,
    gd_load_r: float = 0.0,
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute performance metrics and safety margins from the solution.

    Small-signal parameters come through ``model`` evaluated at the *solved*
    geometry — exact for both Level-1 (geometry-free λ·Id) and gm/Id (LUT).
    """
    metrics: dict[str, float] = {}
    margins: dict[str, float] = {}
    is_three_stage = any(s in slot_transistors for s in _THIRD_STAGE_SLOTS)
    has_second_stage = (
        any(s in slot_transistors for s in _SECOND_STAGE_SLOTS) or is_three_stage
    )

    def _sz(ref: str) -> TransistorSizing | None:
        return transistor_sizing.get(ref)

    def _gm(d: Device, s: TransistorSizing) -> float:
        return min(model.gm(d.type, s.w_um, s.l_um, s.ids_a),
                   model.gm_ceiling(d.type, s.ids_a, s.l_um))

    def _gds(d: Device, s: TransistorSizing) -> float:
        return model.gds(d.type, s.w_um, s.l_um, s.ids_a)

    # --- Input pair gm ---
    ip_devs = slot_transistors.get("input_pair", [])
    gm1 = 0.0
    s_ip = _sz(ip_devs[0].ref) if ip_devs else None
    if s_ip:
        gm1 = _gm(ip_devs[0], s_ip)

    # --- Load ---
    ld_devs = slot_transistors.get("load", [])
    gd_ld = 0.0
    if ld_devs:
        s = _sz(ld_devs[0].ref)
        if s:
            gd_ld = _gds(ld_devs[0], s)

    gd_ip = _gds(ip_devs[0], s_ip) if s_ip else 0.0
    rout1 = (eq.rout(gd_ip, gd_ld + gd_load_r)
             if (gd_ip + gd_ld + gd_load_r) > 0 else float("inf"))

    # --- Tail current ---
    tc_devs = slot_transistors.get("tail_current", [])
    gd_tail = 0.0
    if tc_devs:
        s = _sz(tc_devs[0].ref)
        if s:
            gd_tail = _gds(tc_devs[0], s)

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
            if _is_signal_dev(d):
                gm2 = _gm(d, s)
            else:
                ss_load_gd = g_d
        rout2 = eq.rout(gd_ss_n, gd_ss_p) if (gd_ss_n + gd_ss_p) > 0 else float("inf")
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
            if _is_signal_dev(d):
                gm3 = _gm(d, s)
        rout3 = eq.rout(gd_ts_n, gd_ts_p) if (gd_ts_n + gd_ts_p) > 0 else float("inf")
    else:
        rout3 = float("inf")

    # --- Gain ---
    if is_three_stage and rout2 < float("inf") and rout3 < float("inf"):
        stage_gains = [gm1 * rout1, gm2 * rout2, gm3 * rout3]
    elif has_second_stage and rout2 < float("inf"):
        stage_gains = [gm1 * rout1, gm2 * rout2]
    else:
        stage_gains = [gm1 * rout1]
    if all(g > 0 for g in stage_gains):
        gain_db = eq.open_loop_gain_db(stage_gains)
        metrics["gain_db"] = gain_db
        if spec.gain_min_db is not None:
            margins["gain_db"] = gain_db - spec.gain_min_db  # +ve → meets spec

    # --- GBW, PM, SR ---
    cc_f = (cc_pf * 1e-12) if cc_pf else None
    cc2_f = (cc2_pf * 1e-12) if cc2_pf else None
    if has_second_stage and cc_f and gm1 > 0:
        gbw = eq.unity_gain_bw(gm1, cc_f)
        metrics["gbw_hz"] = gbw
        if spec.gbw_min_hz is not None:
            margins["gbw_hz"] = gbw - spec.gbw_min_hz

        if is_three_stage and gm2 > 0 and gm3 > 0 and cc2_f:
            pm = eq.phase_margin_three_stage_deg(gm1, gm2, gm3, cc_f, cc2_f, spec.cl)
        elif gm2 > 0:
            pm = eq.phase_margin_two_stage_deg(gm1, gm2, cc_f, spec.cl)
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
        n_ss = sum(1 for s in _SECOND_STAGE_SLOTS if s in slot_transistors)
        supply_currents.append(ids_2 * n_ss)
    if is_three_stage:
        n_ts = sum(1 for s in _THIRD_STAGE_SLOTS if s in slot_transistors)
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
    topology_warnings = _check_topology_match(slot_transistors, topology.name)
    all_transistors = _deduplicate(slot_transistors)
    ids_map = _assign_ids(slot_transistors, all_transistors, spec)
    # Size resistor loads (deterministic) and model them in the first-stage Rout.
    resistors = _size_load_resistors(_extract_slot_resistors(fbr_result), spec, tech)
    gd_load_r = (1.0 / min(resistors.values())) if resistors else 0.0

    # gm/Id model for PTM nodes (LUT present); Level-1 otherwise.
    dev_model = build_device_model(tech)
    gm_req_map, vod_max_map, cc_pf, cc2_pf, gm_ceiling_warnings = _compute_requirements(
        slot_transistors, all_transistors, ids_map, tech, spec, dev_model, gd_load_r
    )
    all_warnings = topology_warnings + gm_ceiling_warnings

    if dev_model.is_gmid:
        # Procedural forward pass — geometry is computed, not searched.
        role_map = {
            ref: (SIGNAL if _is_signal_dev(device) else CURRENT_SOURCE)
            for ref, (device, _slot) in all_transistors.items()
        }
        transistor_sizing, geom_warnings = assign_geometry_gmid(
            dev_model, all_transistors, slot_transistors, ids_map,
            role_map, gm_req_map, tech,
        )
        all_warnings = all_warnings + geom_warnings
        status_name = "GMID"
    else:
        # Level-1: discrete W/L via CP-SAT.
        cp_mdl, W_vars, L_vars = build_model(
            all_transistors, slot_transistors, ids_map, gm_req_map, vod_max_map, tech
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        status = solver.solve(cp_mdl)
        status_name = solver.status_name(status)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return SizingResult(
                transistors={},
                cc_pf=cc_pf,
                metrics={},
                margins={},
                solver_status=status_name,
                cc2_pf=cc2_pf,
                warnings=all_warnings,
                resistors=resistors,
            )

        # Extract solution: convert integer step-units back to µm.
        w_step = tech.width.step
        l_step = tech.length.step
        transistor_sizing = {}
        for ref, (device, _slot) in all_transistors.items():
            w_um = solver.value(W_vars[ref]) * w_step
            l_um = solver.value(L_vars[ref]) * l_step
            ids_a = ids_map[ref]
            transistor_sizing[ref] = TransistorSizing(
                ref=ref, w_um=w_um, l_um=l_um, ids_a=ids_a,
                vgs_v=dev_model.vgs(device.type, w_um, l_um, ids_a),
                vds_sat_v=dev_model.vds_sat(device.type, w_um, l_um, ids_a),
            )

    metrics, margins = _evaluate_metrics(
        transistor_sizing, slot_transistors, cc_pf, tech, spec, dev_model,
        cc2_pf=cc2_pf, gd_load_r=gd_load_r,
    )
    return SizingResult(
        transistors=transistor_sizing,
        cc_pf=cc_pf,
        metrics=metrics,
        margins=margins,
        solver_status=status_name,
        cc2_pf=cc2_pf,
        warnings=all_warnings,
        resistors=resistors,
    )
