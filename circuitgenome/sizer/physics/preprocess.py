"""Model-independent pre-sizing: IDS assignment from KCL + ``spec.ibias``,
load-resistor sizing, and the gm/VDS_sat requirements derived from the
performance spec.  The circuit's *structure* comes from
:mod:`~circuitgenome.sizer.physics.circuit_view`.

Shared by both the Level-1 analytical sizer and the gm/Id pipeline.  Output
conductances come through a :class:`~.device_model.DeviceModel`, so the gm/Id
path gets LUT-accurate ``gds`` while Level-1 reproduces ``λ·Id`` exactly.
"""
from __future__ import annotations

import math

from circuitgenome.synthesizer.models import Device

from . import equations as eq
from . import rnmc
from .rnmc import RNMC
from .device_model import CURRENT_SOURCE, SIGNAL, DeviceModel
from ..models import SizingSpec, TechParams
from .taxonomy import (
    FULL_BIAS_SLOTS,
    HALF_BIAS_SLOTS,
    RAILS,
    SECOND_STAGE_SLOTS,
    THIRD_STAGE_SLOTS,
    is_signal_device,
)


_RESISTOR_LOAD_OVERDRIVE = 0.15

# Miller-compensation stability floor: Cc ≥ this fraction of CL keeps the
# pole split wide enough for a ~60° phase-margin budget (Allen-Holberg's
# Cc > 0.22·CL, rounded up for margin).
_CC_STABILITY_RATIO = 0.25

# Three-stage nested-Miller inner loop (issue #208 follow-up): floor on
# gm3/gm2 and the damping Cc2 is sized for.  gf180 SPICE (open-loop kick)
# rings at gm3/gm2 ≈ 2 for any Cc2, and at ζ ≲ 0.2 even with gm3/gm2 ≈ 3.8.
_NMC_GM3_OVER_GM2 = 3.5
_NMC_INNER_ZETA = 0.3


def size_load_resistors(
    slot_resistors: dict[str, list[Device]], spec: SizingSpec, tech: TechParams,
    seed_v: dict[str, float] | None = None,
) -> dict[str, float]:
    """Size resistor-load devices so the first-stage output biases on.

    Each load resistor carries the input-pair branch current ``ibias/2``.  The
    value is chosen so the DC drop places the first-stage output node at a level
    that turns the driven device on:

    * resistor to **gnd** (PMOS-input loads): ``V_node`` = the NMOS driven
      device's turn-on gate voltage → ``R = V_node / (ibias/2)``.
    * resistor from **vdd** (NMOS-input loads): drop = the PMOS driven device's
      ``|Vgs|`` → ``R = drop / (ibias/2)``.

    ``seed_v`` supplies that turn-on voltage per polarity (``{"nmos": …,
    "pmos": …}``, magnitudes in V). The gm/Id path passes the driven signal
    device's ``Vgs`` read from the LUT (issue #158); when omitted (Level-1 path),
    it falls back to the analytical ``|vth| + Vov`` estimate.

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
            v = seed_v["nmos"] if seed_v else tech.nmos.vth + vov
        elif any("vdd" in n for n in nets):
            v = seed_v["pmos"] if seed_v else abs(tech.pmos.vth) + vov
        else:
            continue  # not a rail-referenced load resistor
        if v > 0:
            out[r.ref] = v / branch_i
    return out


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


def _cmfb_current_plan(
    slot_transistors: dict[str, list[Device]], spec: SizingSpec,
) -> dict[str, float]:
    """Per-device IDS inside the CMFB amp, by KCL from its tail(s).

    The CMFB's output is a diode that mirrors into the load's CMFB-gated
    devices (issue #208), so the diode's planned current *sets* the load
    current through the mirror ratio (the gm/Id geometry pass sizes the load
    as ``W_diode · I_load / I_diode``) and must be the current it really
    carries.  Structure, read from the assembled netlist:

    * **tails** — gate on a bias rail (``net_bias*``): ``ibias`` each, like
      the bias-generator leg they mirror.
    * **pair devices** — source on a tail's drain: split that tail's current.
    * **everything else** — the sum of the known currents entering its drain
      net (a diode fed by its branch), or else a copy of the diode it mirrors
      (same gate, same type).

    Returns ``{}`` when there is no CMFB.
    """
    devs = [d for d in slot_transistors.get("cmfb", []) if d.type in ("nmos", "pmos")]
    tails = [d for d in devs if d.terminals.get("g", "").startswith("net_bias")]
    ids = {d.ref: spec.ibias for d in tails}
    for t in tails:
        pair = [d for d in devs if d.terminals.get("s") == t.terminals.get("d")]
        for d in pair:
            ids[d.ref] = spec.ibias / len(pair)
    for _ in devs:   # fixed point: each pass settles at least one device
        for d in devs:
            if d.ref in ids:
                continue
            fed = [ids[o.ref] for o in devs if o is not d and o.ref in ids
                   and o.terminals.get("d") == d.terminals.get("d")]
            if fed:
                ids[d.ref] = sum(fed)
                continue
            ref = next((o for o in devs if o is not d and o.ref in ids
                        and o.type == d.type
                        and o.terminals.get("g") == d.terminals.get("g")
                        == o.terminals.get("d")), None)
            if ref is not None:
                ids[d.ref] = ids[ref.ref]
    return ids


def assign_ids(
    slot_transistors: dict[str, list[Device]],
    all_transistors: dict[str, tuple[Device, str]],
    spec: SizingSpec,
) -> dict[str, float]:
    """Assign quiescent IDS to each transistor from KCL + spec.ibias."""
    ids_2 = spec.ibias * spec.second_stage_current_ratio
    cascode_load = _cascode_load_current_plan(slot_transistors, spec)
    cmfb = _cmfb_current_plan(slot_transistors, spec)
    ids_map: dict[str, float] = {}
    for ref, (device, slot) in all_transistors.items():
        if slot == "load" and ref in cascode_load:
            ids_map[ref] = cascode_load[ref]
        elif slot == "cmfb" and ref in cmfb:
            ids_map[ref] = cmfb[ref]
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


def _mirror_reference(load_devs: list[Device]) -> tuple[Device, str] | None:
    """``(reference device, mirror gate net)`` of a current-mirror load, or ``None``.

    The reference device is the one whose gate is closed back onto its own
    current path, so its gate net sits at ``1/gm`` and every device on that net
    copies its current.  Two shapes close that loop:

    * **diode-connected** -- the device's gate is its own drain (``g == d``);
    * **closed through a cascode** -- the device is gated by the *drain* of the
      cascode stacked on it (the cascode's source is the device's drain).  This
      is the wide-swing (Sooch) mirror, whose cascodes are gated from a level
      rail instead of a second diode (issue #241).

    A plain diode wins when both are present, so the loads that already had
    one keep the device they were always measured from.
    """
    mos = [d for d in load_devs if d.type in ("nmos", "pmos")]
    for d in mos:
        g = d.terminals.get("g")
        if g and g == d.terminals.get("d"):
            return d, g
    for d in mos:
        g = d.terminals.get("g")
        if g and any(c.terminals.get("d") == g
                     and c.terminals.get("s") == d.terminals.get("d")
                     for c in mos if c is not d):
            return d, g
    return None


def _first_stage_gain_factor(slot_transistors: dict[str, list[Device]]) -> float:
    """First-stage transconductance/gain factor ``k_fs``.

    A differential pair tapped **single-ended** only delivers ``gm1·Rout1/2``
    (and a Miller loop transconductance of ``gm1/2``) unless an active
    **current-mirror** load combines both branches to recover the full
    ``gm1·Rout1``. So:

    * ``1.0`` — current-mirror load (see :func:`_mirror_reference`, which
      also recognises a diode closed through a cascode), or a
      fully-differential output (both branches feed the next stage), and
    * ``0.5`` — single-ended output with a resistor or plain current-source
      (non-mirror) load.

    Applied to the first-stage gain and to ``gm1``'s role in GBW/PM (not to the
    raw-device ``gm1`` used for CMRR).
    """
    if any(s in slot_transistors for s in
           ("second_stage_p", "second_stage_n", "third_stage_p", "third_stage_n")):
        return 1.0  # fully-differential: both branches drive the next stage
    is_mirror = _mirror_reference(slot_transistors.get("load", [])) is not None
    return 1.0 if is_mirror else 0.5


def compute_requirements(
    slot_transistors: dict[str, list[Device]],
    all_transistors: dict[str, tuple[Device, str]],
    ids_map: dict[str, float],
    tech: TechParams,
    spec: SizingSpec,
    model: DeviceModel,
    gd_load_r: float = 0.0,
    compensation_scheme: str | None = None,
) -> tuple[dict[str, float], dict[str, float], float | None, float | None, list[str]]:
    """Compute required gm and max VDS_sat per transistor; also Cc1 and Cc2.

    Returns ``(gm_req_map, vod_max_map, cc_pf, cc2_pf, warnings)`` where
    ``warnings`` lists any gm requirement clamped to the weak-inversion
    ceiling (the spec cannot be met at this bias current).  Output conductances
    come through ``model`` so the gm/Id path uses LUT-accurate gds; the Level-1
    model reproduces the geometry-free ``λ·Id`` exactly.

    ``compensation_scheme`` is the template's three-stage compensation
    (``topology.config["compensation_scheme"]``):

    * ``"nested_miller"`` (NMC) — its inner loop (Cc2 around the third stage)
      is sized for damping: ``gm3 ≥ _NMC_GM3_OVER_GM2·gm2`` and Cc2 for
      ``_NMC_INNER_ZETA`` — instead of the ``Cc2 = Cc1/4`` default.
    * ``"reversed_nested_miller"`` (RNMC) — gm2, Cc2 and gm3 are re-planned
      with :func:`~.rnmc.design_rnmc` (raise gm2, then Cc2, then cap gm3) so
      the inner pole pair is damped and in the left half plane; its
      advisories join ``warnings``.  Only the Level-1 sizer asks for this:
      the gm/Id pipeline's geometry moves gm1 far from this estimate (bias
      repair), so it re-plans RNMC on the sized circuit instead
      (:mod:`~circuitgenome.sizer.gmid.rnmc_refine`).
    """
    nested_miller = compensation_scheme == "nested_miller"
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

        # Pick the *smallest* stable Cc: the pole-split floor ~0.25·CL keeps
        # the output pole clear of GBW for a ~60° PM budget.  The slew rate
        # only *upper*-bounds Cc (SR = iBias/Cc — a smaller Cc slews faster),
        # and every pF above the floor inflates the GBW-side gm1 requirement
        # 2π·GBW·Cc/k_fs toward the weak-inversion ceiling (issue #108).
        cc_ub_f = cc_max_f
        if spec.slew_rate_min_vps:
            cc_ub_f = min(cc_ub_f, spec.ibias / spec.slew_rate_min_vps)
        cc_f = max(cc_min_f, min(_CC_STABILITY_RATIO * spec.cl, cc_ub_f))

    # Cc2 for three-stage (see below); None for two-stage and one-stage.
    cc2_pf: float | None = None
    cc2_f: float = 0.0

    if has_second_stage and cc_f > 0:
        # From GBW: GBW = k_fs·gm1 / (2π·Cc)  (with the stability-floor Cc).
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
            # Inner cap = Cc1/4, except NMC: its non-dominant poles form a pair
            # with damping ζ = (r−1)/2·√(Cc2/(r·CL)), r = gm3/gm2 (Leung &
            # Mok), so size Cc2 for ζ = _NMC_INNER_ZETA at the gm3/gm2 floor
            # enforced below (a larger realized r only damps it more).
            cc2_f = cc_f / 4.0
            if nested_miller:
                r = _NMC_GM3_OVER_GM2
                cc2_f = min(cc_f, 4.0 * _NMC_INNER_ZETA ** 2 * r * spec.cl / (r - 1.0) ** 2)
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

            # The gm3/gm2 floor the Cc2 above assumes, against the gm2 the
            # second stage will really deliver.  At gm3 ≈ 2·gm2 the inner loop
            # rings above GBW, which the open-loop AC bench cannot see (#208).
            ss_sig = next((d for d in ss_devices if is_signal_device(d)), None)
            if nested_miller and ss_sig is not None:
                gm2_real = model.realized_gm(ss_sig.type, gm2_req, ids_2)
                gm3_req = max(gm3_req, _NMC_GM3_OVER_GM2 * gm2_real)

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
                # gm2 follows the gm1 the pair will *actually* have, which the
                # model knows: Level-1 overshoots to the integer W grid, gm/Id
                # delivers the request.
                ip_dev = ip_devices[0]
                ids_ip = spec.ibias / max(
                    len([d for d in ip_devices if d.type == ip_dev.type]), 1
                )
                gm1_eff = model.realized_gm(ip_dev.type, gm1_req, ids_ip)
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

    # --- RNMC: damp the inner pole pair (raise gm2, then Cc2, cap gm3) ---
    # A gm3 cap lands on the third stage's input device only (see below).
    gm3_in_req: float | None = None
    if (compensation_scheme == RNMC and is_three_stage and cc2_f > 0
            and gm1_req > 0 and gm3_req > 0 and ip_devices):
        ip_dev = ip_devices[0]
        gm1_loop = k_fs * model.realized_gm(ip_dev.type, gm1_req, spec.ibias / 2.0)
        ts_sig = next((d for d in ts_devices if is_signal_device(d)), None)
        c2_f = (rnmc.signal_cgs_estimate(model, ts_sig.type, gm3_req, ids_3, tech)
                if ts_sig is not None else 0.0)
        g = tuple(1.0 / r if r < float("inf") else 0.0 for r in (rout1, rout2, rout3))
        design = rnmc.design_rnmc(
            gm1_loop, max(gm2_req, 1e-12), gm3_req, cc1_f=cc_f, cc2_f=cc2_f,
            cl_f=spec.cl, gm2_max=gm2_ceil,
            pm_min_deg=spec.phase_margin_min_deg, c2_f=c2_f, g=g)
        gm_ceiling_warnings += rnmc.design_warnings(
            design, gm3_req, spec.phase_margin_min_deg)
        gm2_req, gm3_in_req = design.gm2, design.gm3
        cc2_pf = design.cc2_f * 1e12

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
    if gm3_in_req is not None:
        # RNMC: the capped gm3 applies to the device gated by a second-stage
        # output; a non-inverting stage's mirror keeps its (swing) sizing.
        stage2_out = {d.terminals.get("d") for devs in (
            slot_transistors.get(s, []) for s in SECOND_STAGE_SLOTS)
            for d in devs if is_signal_device(d)}
        for ref, (device, slot) in all_transistors.items():
            if slot in THIRD_STAGE_SLOTS and device.terminals.get("g") in stage2_out:
                gm_req_map[ref] = gm3_in_req

    # --- VDS_sat upper bounds from output swing specs ---
    vdd = spec.vdd
    vss = spec.vss

    # Second- and third-stage device lists (constrain output swing on every
    # path).  NMC skips the second stage: it never drives the output, and a
    # swing floor there only inflates gm2 (gm/Id ↑ at fixed Id), squeezing the
    # gm3/gm2 ratio its inner loop needs.  (RNMC is left as is: its inner
    # loop wraps the second stage, and gf180 SPICE rings without the floor.)
    swing_slots = (THIRD_STAGE_SLOTS if nested_miller
                   else (*SECOND_STAGE_SLOTS, *THIRD_STAGE_SLOTS))
    all_ss_device_lists = [
        slot_transistors[s]
        for s in swing_slots
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
