"""
Level-1 MOSFET equations (Shichman-Hodges) and op-amp performance formulas.

All functions operate on SI units unless the name suffix states otherwise.
Consistent units: currents in A, voltages in V, transconductances in A/V (S),
dimensions (W, L) in µm (dimensionless ratio W/L is used where needed),
frequencies in Hz, power in W.
"""
from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Level-1 MOSFET small-signal parameters
# ---------------------------------------------------------------------------

def gm(mu_cox: float, w_um: float, l_um: float, ids_a: float) -> float:
    r"""Transconductance gm in A/V.

    gm = √(2·µCox·(W/L)·\|IDS\|)

    :param mu_cox: Process transconductance µCox in A/V².
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Drain-source current in A (sign ignored).
    """
    return math.sqrt(2.0 * mu_cox * (w_um / l_um) * abs(ids_a))


# Maximum transconductance efficiency gm/IDS in 1/V (weak-inversion ceiling).
# gm is physically bounded by gm ≤ IDS/(n·φt); with n ≈ 1.5 and φt ≈ 0.0259 V at
# 300 K this is ≈ 25.8 /V (matches measured gm/Id ≈ 25 for the PTM devices). The
# square-law gm formula ignores this ceiling and would otherwise let the sizer
# promise a gm the device can only reach by sliding into weak inversion.
_GM_OVER_ID_MAX = 25.0


def gm_ceiling(ids_a: float) -> float:
    """Physical upper bound on gm in A/V (weak-inversion limit)."""
    return _GM_OVER_ID_MAX * abs(ids_a)


def gd(lam: float, ids_a: float) -> float:
    r"""Output conductance gd in A/V.

    gd = λ·\|IDS\|

    :param lam: Channel-length modulation coefficient λ in 1/V (positive).
    :param ids_a: Drain-source current in A.
    """
    return lam * abs(ids_a)


def rout(gd_top: float, gd_bot: float) -> float:
    """Stage output resistance in Ω.

    Rout = 1 / (gd_top + gd_bot)

    :param gd_top: Output conductance of the upper (load) transistor in A/V.
    :param gd_bot: Output conductance of the lower (drive) transistor in A/V.
    """
    total = gd_top + gd_bot
    return 1.0 / total if total > 0.0 else float("inf")


def vgs_from_ids(
    mu_cox: float, w_um: float, l_um: float, ids_a: float, vth: float
) -> float:
    """Gate-source voltage in V (saturation, λ=0 approximation).

    Solves IDS = (µCox/2)·(W/L)·(VGS−Vth)² for VGS.
    Returns a signed value: positive for NMOS, negative for PMOS.

    :param vth: Threshold voltage in V (negative for PMOS).
    """
    overdrive = math.sqrt(2.0 * abs(ids_a) * l_um / (mu_cox * w_um))
    return math.copysign(abs(vth) + overdrive, vth)


def vds_sat(mu_cox: float, w_um: float, l_um: float, ids_a: float) -> float:
    r"""Minimum \|VDS\| for saturation in V.

    VDS_sat = VGS − Vth = √(2·\|IDS\|·L / (µCox·W))

    :returns: Always positive.
    """
    return math.sqrt(2.0 * abs(ids_a) * l_um / (mu_cox * w_um))


# ---------------------------------------------------------------------------
# Op-amp performance metrics
# ---------------------------------------------------------------------------

def open_loop_gain_db(stage_gains: list[float]) -> float:
    """Total open-loop DC gain in dB.

    A0 = Π(gm_j · Rout_j), converted to dB.

    :param stage_gains: List of per-stage voltage gains (dimensionless).
    """
    product = math.prod(stage_gains)
    return 20.0 * math.log10(abs(product)) if product != 0.0 else -math.inf


# Empirical open-loop DC-gain ceiling for the ngspice open-loop AC bench (dB).
# The analytical ``gain_db`` is an un-derated cascade product; above this ceiling
# the open-loop DC operating point cannot hold the output at mid-rail without
# feedback, so the measured open-loop AC gain rails to ~0 dB even when the
# per-device DC bias is sound.  Calibrated to observed GF180 behaviour (#155):
# measurable two-stage designs top out ~133 dB, whereas three-stage cascades at
# ≥175 dB rail on every PVT corner.  150 dB sits safely between the two bands to
# minimise false positives.
OPEN_LOOP_GAIN_CEILING_DB = 150.0


def open_loop_measurable(
    gain_db: float | None, ceiling_db: float = OPEN_LOOP_GAIN_CEILING_DB
) -> bool:
    """Whether the analytical open-loop gain is measurable on an open-loop bench.

    ``open_loop_gain_db`` returns an un-derated single-point upper bound; above
    ``ceiling_db`` the open-loop DC operating point cannot be held at mid-rail,
    so ngspice's open-loop AC gain rails to ~0 dB even when the DC bias is sound.
    This is an **advisory** heuristic, not a hard prune — the analytical
    ``gain_db`` is still reported and SPICE remains the authority.  See
    :data:`OPEN_LOOP_GAIN_CEILING_DB`.

    :param gain_db: Analytical open-loop DC gain in dB, or ``None`` when no gain
        was computed (single-stage / gated).  ``None`` → measurable (no evidence
        of railing).
    :returns: ``True`` when measurable, ``False`` when predicted to rail.
    """
    return gain_db is None or gain_db <= ceiling_db


def unity_gain_bw(gm1_a_v: float, cc_f: float) -> float:
    """Unity-gain bandwidth (GBW) in Hz.

    GBW = gm1 / (2π·Cc)

    :param gm1_a_v: Input-pair transconductance in A/V.
    :param cc_f: Compensation capacitor in F.
    """
    return gm1_a_v / (2.0 * math.pi * cc_f)


def phase_margin_two_stage_deg(
    gm1: float, gm2: float, cc_f: float, cl_f: float
) -> float:
    """Phase margin in degrees (dominant-pole approximation).

    PM ≈ 90° − arctan(gm1·CL / (gm2·Cc))

    Valid when the dominant pole is at 1/(Rout1·Cc) and the non-dominant
    pole is at gm2/CL (internal mirror pole neglected).

    :param gm1: Input-pair gm in A/V.
    :param gm2: Second-stage signal transistor gm in A/V.
    :param cc_f: Compensation capacitor in F.
    :param cl_f: Output load capacitance in F.
    """
    return 90.0 - math.degrees(math.atan(gm1 * cl_f / (gm2 * cc_f)))


def phase_margin_three_stage_deg(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
) -> float:
    """Phase margin in degrees for three-stage NMC/RNMC (two non-dominant poles).

    PM ≈ 90° − arctan(ωt·Cc2/gm2) − arctan(ωt·CL/gm3)
    where ωt = gm1/Cc1.

    :param gm1: Input-pair transconductance in A/V.
    :param gm2: Second-stage signal transistor gm in A/V.
    :param gm3: Third-stage signal transistor gm in A/V.
    :param cc1_f: Outer (primary) compensation capacitor in F.
    :param cc2_f: Inner compensation capacitor in F (Cc2 = Cc1/4 by default).
    :param cl_f: Output load capacitance in F.
    """
    wt = gm1 / cc1_f
    lag2 = math.degrees(math.atan(wt * cc2_f / gm2))
    lag3 = math.degrees(math.atan(wt * cl_f / gm3))
    return 90.0 - lag2 - lag3


def slew_rate_vps(ibias_a: float, cc_f: float) -> float:
    """Slew rate in V/s.

    SR = IBias / Cc  (limited by tail-current charging/discharging Cc)

    :param ibias_a: Tail bias current in A.
    :param cc_f: Compensation capacitor in F.
    """
    return ibias_a / cc_f


def quiescent_power(vdd: float, vss: float, supply_currents_a: list[float]) -> float:
    """Total quiescent power in W.

    P = (VDD − VSS) · Σ|IDS_supply|

    :param supply_currents_a: Currents drawn from the positive supply
        (before summing, absolute values are taken).
    """
    return (vdd - vss) * sum(abs(i) for i in supply_currents_a)


def cmrr_db(gm1: float, gd_tail: float) -> float:
    """Common-mode rejection ratio in dB (first-order approximation).

    CMRR ≈ gm1 / (2·gd_tail)

    :param gm1: Input-pair transconductance in A/V.
    :param gd_tail: Output conductance of the tail current source in A/V.
    """
    if gd_tail == 0.0:
        return math.inf
    return 20.0 * math.log10(gm1 / (2.0 * gd_tail))


def psrr_db_approx(gm2: float, gd_bias: float) -> float:
    """Positive-supply PSRR rough approximation in dB.

    PSRR+ ≈ gm2 / gd_bias_mirror

    This is a first-order estimate valid for simple two-stage opamps;
    accurate PSRR requires simulation.

    :param gm2: Second-stage signal transistor gm in A/V.
    :param gd_bias: Output conductance of the second-stage bias transistor in A/V.
    """
    if gd_bias == 0.0:
        return math.inf
    return 20.0 * math.log10(gm2 / gd_bias)
