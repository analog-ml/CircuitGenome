"""Model-independent pre-sizing: slot extraction, IDS assignment from KCL +
``spec.ibias``, topology checks, load-resistor sizing, and the gm/VDS_sat
requirements derived from the performance spec.

Shared by both the Level-1 analytical sizer and the gm/Id pipeline.  Output
conductances come through a :class:`~.device_model.DeviceModel`, so the gm/Id
path gets LUT-accurate ``gds`` while Level-1 reproduces ``λ·Id`` exactly.
"""
from __future__ import annotations

import math

from circuitgenome.recognizer.models import FunctionalBlockRecognitionResult
from circuitgenome.synthesizer.models import Device

from . import equations as eq
from .device_model import CURRENT_SOURCE, SIGNAL, DeviceModel
from .models import SizingSpec, TechParams
from .taxonomy import (
    FULL_BIAS_SLOTS,
    HALF_BIAS_SLOTS,
    RAILS,
    SECOND_STAGE_SLOTS,
    STAGE_SLOTS,
    THIRD_STAGE_SLOTS,
    is_signal_device,
)


def extract_slot_transistors(
    fbr_result: FunctionalBlockRecognitionResult,
) -> dict[str, list[Device]]:
    """Return {slot_name: [mosfet_Device, ...]} from the FBR assignments."""
    result: dict[str, list[Device]] = {}
    for slot_name, sa in fbr_result.slot_assignments.items():
        mosfets = [d for d in sa.structure.devices if d.type in ("nmos", "pmos")]
        if mosfets:
            result[slot_name] = mosfets
    return result


def extract_slot_resistors(
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


def size_load_resistors(
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


def check_topology_match(
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
    for slot in sorted(STAGE_SLOTS):
        devs = slot_transistors.get(slot)
        if devs and not any(is_signal_device(d) for d in devs):
            warnings.append(
                f"stage slot '{slot}' has no signal transistor — the netlist may "
                f"not match topology '{topology_name}' (check --topology, e.g. "
                f"single-ended vs fully-differential)."
            )
    return warnings


def deduplicate_devices(
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


def _cascode_load_current_plan(
    slot_transistors: dict[str, list[Device]], spec: SizingSpec,
) -> dict[str, float]:
    """Per-device IDS for folded/telescopic cascode loads, from KCL at the fold.

    The generic HALF_BIAS rule (``ibias/n`` per same-type device) starves
    cascode loads: at the folding node the bottom sinks must absorb the
    input-pair branch current *plus* the cascode branch current, or the pair's
    excess current has nowhere to go and the whole load rails.  Structure,
    read from the assembled netlist:

    * **folding devices** — source on a supply rail, drain on an input-pair
      drain net.  They carry ``ibias/2`` (pair branch) + ``I_casc``.
    * **cascode devices** — source on an input-pair drain net (stacked on the
      folding node).  They and every other load device carry the cascode
      branch current ``I_casc``, chosen as ``ibias/2``.
    * **telescopic** (cascode devices but no folding devices): no folding
      node — the whole stack carries the pair branch current ``ibias/2``.

    Simple loads (nothing stacked on the pair drains) return ``{}`` and keep
    the generic rule.
    """
    load = [d for d in slot_transistors.get("load", [])
            if d.type in ("nmos", "pmos")]
    pair = [d for d in slot_transistors.get("input_pair", [])
            if d.type in ("nmos", "pmos")]
    if not load or not pair:
        return {}
    pair_drains = {d.terminals.get("d") for d in pair}
    cascode = {d.ref for d in load if d.terminals.get("s") in pair_drains}
    if not cascode:
        return {}
    folding = {d.ref for d in load
               if d.terminals.get("s") in RAILS
               and d.terminals.get("d") in pair_drains}
    i_pair = spec.ibias / 2.0
    if not folding:
        return {d.ref: i_pair for d in load}  # telescopic
    i_casc = spec.ibias / 2.0
    return {d.ref: (i_pair + i_casc if d.ref in folding else i_casc)
            for d in load}


def assign_ids(
    slot_transistors: dict[str, list[Device]],
    all_transistors: dict[str, tuple[Device, str]],
    spec: SizingSpec,
) -> dict[str, float]:
    """Assign quiescent IDS to each transistor from KCL + spec.ibias."""
    ids_2 = spec.ibias * spec.second_stage_current_ratio
    cascode_load = _cascode_load_current_plan(slot_transistors, spec)
    ids_map: dict[str, float] = {}
    for ref, (device, slot) in all_transistors.items():
        if slot == "load" and ref in cascode_load:
            ids_map[ref] = cascode_load[ref]
        elif slot in HALF_BIAS_SLOTS:
            # Each transistor in a 2-transistor group carries ibias/2.
            # For n devices in the slot (e.g. degenerated pairs), divide equally.
            n = len([d for d in slot_transistors[slot] if d.type == device.type])
            ids_map[ref] = spec.ibias / max(n, 1)
        elif slot in FULL_BIAS_SLOTS:
            ids_map[ref] = spec.ibias
        elif slot in SECOND_STAGE_SLOTS:
            ids_map[ref] = ids_2
        elif slot in THIRD_STAGE_SLOTS:
            ids_map[ref] = spec.ibias * spec.third_stage_current_ratio
        else:
            ids_map[ref] = spec.ibias  # conservative default
    return ids_map


def _first_stage_gain_factor(slot_transistors: dict[str, list[Device]]) -> float:
    """First-stage transconductance/gain factor ``k_fs``.

    A differential pair tapped **single-ended** only delivers ``gm1·Rout1/2``
    (and a Miller loop transconductance of ``gm1/2``) unless an active
    **current-mirror** load combines both branches to recover the full
    ``gm1·Rout1``. So:

    * ``1.0`` — current-mirror load (a ``load`` device is diode-connected,
      ``g == d``), or a fully-differential output (both branches feed the
      next stage), and
    * ``0.5`` — single-ended output with a resistor or plain current-source
      (non-mirror) load.

    Applied to the first-stage gain and to ``gm1``'s role in GBW/PM (not to the
    raw-device ``gm1`` used for CMRR).
    """
    if any(s in slot_transistors for s in
           ("second_stage_p", "second_stage_n", "third_stage_p", "third_stage_n")):
        return 1.0  # fully-differential: both branches drive the next stage
    mosfets = [d for d in slot_transistors.get("load", []) if d.type in ("nmos", "pmos")]
    is_mirror = any(d.terminals.get("g") == d.terminals.get("d") for d in mosfets)
    return 1.0 if is_mirror else 0.5


def compute_requirements(
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
    is_three_stage = any(s in slot_transistors for s in THIRD_STAGE_SLOTS)
    has_second_stage = (
        any(s in slot_transistors for s in SECOND_STAGE_SLOTS) or is_three_stage
    )
    ids_2 = spec.ibias * spec.second_stage_current_ratio

    # --- Output conductances at the operating point ---
    ip_devices = slot_transistors.get("input_pair", [])
    ld_devices = slot_transistors.get("load", [])

    def _gds_est(device: Device, ids: float) -> float:
        """Pre-geometry gds estimate, role-aware (signal vs current source)."""
        role = SIGNAL if is_signal_device(device) else CURRENT_SOURCE
        return model.gds_estimate(device.type, ids, role)

    gd_ip = _gds_est(ip_devices[0], spec.ibias / 2) if ip_devices else 0.0
    gd_ld = _gds_est(ld_devices[0], spec.ibias / 2) if ld_devices else 0.0
    # Resistor-load conductance (1/R) loads the first-stage output node.
    rout1 = eq.rout(gd_ip, gd_ld + gd_load_r)
    # Single-ended non-mirror loads halve the first-stage gm/gain (see helper).
    k_fs = _first_stage_gain_factor(slot_transistors)

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
        # From GBW: GBW = k_fs·gm1 / (2π·Cc)  (with the SR-bounded Cc).
        # This is the primary gm1 driver; Cc stays within the SR bound because
        # we do NOT inflate Cc here to accommodate gain.  A non-mirror load
        # (k_fs<1) needs a proportionally larger device gm1.
        if spec.gbw_min_hz:
            gm1_req = max(gm1_req, 2.0 * math.pi * spec.gbw_min_hz * cc_f / k_fs)

        # Recompute Cc only if CMRR pushed gm1 above the GBW baseline.
        if spec.gbw_min_hz and gm1_req > 0.0:
            cc_f = max(cc_f, k_fs * gm1_req / (2.0 * math.pi * spec.gbw_min_hz))
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
                gm1_loop = k_fs * gm1_req  # transconductance into the loop (ωt = gm1_loop/Cc1)
                gm2_req = max(gm2_req, gm1_loop * cc2_f / (cc_f * t))
                gm3_req = max(gm3_req, gm1_loop * spec.cl / (cc_f * t))

            # Gain: A0 = k_fs·gm1·Rout1·gm2·Rout2·gm3·Rout3.
            # With gm2_req now determined, solve for the gm3 needed for gain.
            if (spec.gain_min_db
                    and rout1 < float("inf")
                    and rout2 < float("inf")
                    and rout3 < float("inf")
                    and gm1_req > 0.0
                    and gm2_req > 0.0):
                A0 = 10.0 ** (spec.gain_min_db / 20.0)
                gm3_from_gain = A0 / (k_fs * gm1_req * rout1 * gm2_req * rout2 * rout3)
                gm3_req = max(gm3_req, gm3_from_gain)

        else:
            # Two-stage: gain A0 = k_fs·gm1·Rout1·gm2·Rout2.
            if spec.gain_min_db and rout1 < float("inf") and rout2 < float("inf"):
                A0 = 10.0 ** (spec.gain_min_db / 20.0)
                if gm1_req > 0.0 and rout1 * rout2 > 0:
                    gm2_from_gain = A0 / (k_fs * gm1_req * rout1 * rout2)
                    gm2_req = max(gm2_req, gm2_from_gain)
                else:
                    # Split gain evenly; k_fs applies to the first stage only.
                    per_stage = math.sqrt(A0 / (k_fs * rout1 * rout2))
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
                    k_fs * gm1_eff * spec.cl / (cc_f * math.tan(math.pi / 2.0 - pm_rad)),
                )

        cc_pf = cc_f * 1e12

    else:
        # One-stage: gain = k_fs·gm1·Rout1
        if spec.gain_min_db and rout1 < float("inf"):
            A0 = 10.0 ** (spec.gain_min_db / 20.0)
            gm1_req = max(gm1_req, A0 / (k_fs * rout1))

    # --- Cap gm requirements at the physical (weak-inversion) ceiling ---
    # The square-law model has no gm ceiling, so without this the sizer would size
    # for a gm the device can only reach by sliding into weak inversion (where the
    # real gm is far lower).  Clamp each requirement to gm ≤ gm_ceiling(IDS); a
    # binding clamp means the spec needs more bias current than the device can
    # physically deliver, surfaced as a warning (the shortfall also shows in the
    # reported margins).
    def _ceil(ids: float, devs: list[Device]) -> float:
        sig = next((d for d in devs if is_signal_device(d)), devs[0] if devs else None)
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
        elif slot in SECOND_STAGE_SLOTS:
            # Only the signal transistor (gate driven by first-stage output)
            # needs a gm requirement; the load transistor is a current source.
            gm_req_map[ref] = gm2_req if is_signal_device(device) else 0.0
        elif slot in THIRD_STAGE_SLOTS:
            gm_req_map[ref] = gm3_req if is_signal_device(device) else 0.0
        # All other slots: no explicit gm requirement (sized by min W/L)

    # --- VDS_sat upper bounds from output swing specs ---
    vdd = spec.vdd
    vss = spec.vss

    # All second- and third-stage device lists (constrain output swing on every path).
    all_ss_device_lists = [
        slot_transistors[s]
        for s in (*SECOND_STAGE_SLOTS, *THIRD_STAGE_SLOTS)
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
