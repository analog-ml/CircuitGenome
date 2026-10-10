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


# Fraction of the full gate-oxide capacitance W·L·Cox that appears as Cgs when
# the device is saturated (the channel pinches off at the drain, so the inversion
# layer couples to the source): the standard long-channel 2/3.
_CGS_SAT_FRACTION = 2.0 / 3.0


def cgs(cox_f_per_um2: float | None, w_um: float, l_um: float) -> float:
    """Gate-source capacitance in F (saturation).

    Cgs = (2/3)·W·L·Cox

    The only capacitance the sizer models. It exists to place the mirror pole
    of a load-compensated single-stage OTA (issue #221), where the dominant
    pole is set by the external ``CL`` and the *non*-dominant one by the gate
    capacitance on the mirror node. Overlap and fringe capacitance are ignored,
    so this under-estimates Cgs at short L.

    :param cox_f_per_um2: Gate-oxide capacitance per unit area in F/µm², or
        ``None`` for a tech that does not supply one — which returns ``0.0``,
        the caller's signal that no pole can be placed.
    """
    if not cox_f_per_um2:
        return 0.0
    return _CGS_SAT_FRACTION * w_um * l_um * cox_f_per_um2


def vds_sat(mu_cox: float, w_um: float, l_um: float, ids_a: float) -> float:
    r"""Minimum \|VDS\| for saturation in V.

    VDS_sat = VGS − Vth = √(2·\|IDS\|·L / (µCox·W))

    :returns: Always positive.
    """
    return math.sqrt(2.0 * abs(ids_a) * l_um / (mu_cox * w_um))


def body_vth_shift(gamma: float, two_phi: float, vsb: float) -> float:
    r"""Threshold-voltage rise from body effect in V.

    ΔVth = γ·(√(2φF + Vsb) − √(2φF))

    :param gamma: Body-effect coefficient γ in √V (``0`` → no shift).
    :param two_phi: Surface potential 2φF in V.
    :param vsb: Source-to-bulk reverse bias \|Vsb\| in V; negative values
        (forward bias) are clamped to ``0``.
    :returns: Always ≥ 0.
    """
    if gamma <= 0.0:
        return 0.0
    return gamma * (math.sqrt(two_phi + max(vsb, 0.0)) - math.sqrt(two_phi))


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


def phase_margin_single_stage_deg(gbw_hz: float, mirror_pole_hz: float) -> float:
    """Phase margin in degrees for a **load**-compensated single-stage OTA.

    PM ≈ 90° − arctan(GBW / f_mirror)

    A single-stage OTA has no Miller cap: its dominant pole is the output node
    itself, ``1/(2π·Rout·CL)``, which alone would put the phase margin at
    exactly 90°. The lag that pulls it below comes from the *next* pole — the
    current-mirror node, whose low ``1/gm`` resistance works against the mirror
    devices' gate capacitance. ``CL`` is normally orders of magnitude larger
    than that gate capacitance, so a healthy single-stage OTA lands just under
    90°; a design that pushes GBW toward the mirror pole is what this formula
    is here to catch.

    Contrast :func:`phase_margin_two_stage_deg`, whose dominant pole is the
    Miller cap and whose non-dominant pole is the *output* — the roles of
    ``CL`` and the internal node are exchanged, which is why the two cannot
    share a formula.

    :param gbw_hz: Unity-gain bandwidth in Hz (``gm1/(2π·CL)``).
    :param mirror_pole_hz: Current-mirror node pole in Hz.
    """
    return 90.0 - math.degrees(math.atan(gbw_hz / mirror_pole_hz))


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
    """Phase margin in degrees for three-stage NMC (two non-dominant poles).

    PM ≈ 90° − arctan(ωt·Cc2/gm2) − arctan(ωt·CL/gm3)
    where ωt = gm1/Cc1.

    Reversed nested Miller does not fit this model — its non-dominant poles
    are a complex pair that can sit in the right half plane — and uses
    :func:`phase_margin_rnmc_deg` instead.

    :param gm1: Input-pair transconductance in A/V.
    :param gm2: Second-stage signal transistor gm in A/V.
    :param gm3: Third-stage signal transistor gm in A/V.
    :param cc1_f: Outer (primary) compensation capacitor in F.
    :param cc2_f: Inner compensation capacitor in F (Cc1/4 for RNMC; sized
        for the inner pole-pair damping for NMC — see
        :func:`~.preprocess.compute_requirements`).
    :param cl_f: Output load capacitance in F.
    """
    wt = gm1 / cc1_f
    lag2 = math.degrees(math.atan(wt * cc2_f / gm2))
    lag3 = math.degrees(math.atan(wt * cl_f / gm3))
    return 90.0 - lag2 - lag3


# ---------------------------------------------------------------------------
# Reversed nested Miller (RNMC) three-stage compensation (PR #249)
# ---------------------------------------------------------------------------
# RNMC puts both caps on the first-stage output: Cc1 to the output (around
# gm2·gm3) and Cc2 to the second-stage output (around gm2 only).  With stage 2
# inverting and stage 3 non-inverting, the half-circuit nodal equations are
#
#   V1: (g1 + s(C1+Cc1+Cc2))·V1 − s·Cc2·V2 − s·Cc1·Vo = −gm1·Vin
#   V2: (gm2 − s·Cc2)·V1 + (g2 + s(C2+Cc2))·V2        = 0
#   Vo: −s·Cc1·V1 − gm3·V2 + (g3 + s(CL+Cc1))·Vo      = 0
#
# and, for high stage gains, the denominator is
#
#   s·Cc1·gm2·gm3 · [1 + s·a1 + s²·a2]
#   a1 = Cc2·((CL+Cc1)·gm2 − Cc1·gm3) / (Cc1·gm2·gm3)
#   a2 = CL·Cc2·K / (gm2·gm3),   K = 1 + C2·(1/Cc2 + 1/Cc1 + 1/CL)
#
# C2 is the parasitic on the second-stage output — in practice the third
# stage's input gate, which is wide because gm3 is large.  a1 > 0 is the
# stability condition gm3 < gm2·(1 + CL/Cc1): past it the non-dominant pair
# sits in the right half plane.  ζ = a1/(2·√a2) is its damping.


def rnmc_stable(gm2: float, gm3: float, cc1_f: float, cl_f: float) -> bool:
    """``True`` when the RNMC non-dominant pole pair is in the left half plane.

    Condition: ``gm3 < gm2·(1 + CL/Cc1)``.  With the default ``Cc1 = CL/4``
    that is ``gm3 < 5·gm2`` — a third stage much stronger than the second
    pushes the inner pole pair into the right half plane.
    """
    return gm3 < gm2 * (1.0 + cl_f / cc1_f)


def rnmc_inner_damping(gm2: float, gm3: float, cc1_f: float, cc2_f: float,
                       cl_f: float, c2_f: float = 0.0) -> float:
    """Damping factor ζ of the RNMC non-dominant pole pair.

    ``ζ = a1 / (2·√a2)`` with ``a1``/``a2`` from the module comment above
    (``c2_f`` is the parasitic on the second-stage output).  Negative when the
    pair is in the right half plane (:func:`rnmc_stable` is ``False``); ``0.7``
    is the usual "fast, barely any peaking" target.
    """
    k = 1.0 + c2_f * (1.0 / cc2_f + 1.0 / cc1_f + 1.0 / cl_f)
    a1 = cc2_f * ((cl_f + cc1_f) * gm2 - cc1_f * gm3) / (cc1_f * gm2 * gm3)
    a2 = cl_f * cc2_f * k / (gm2 * gm3)
    return a1 / (2.0 * math.sqrt(a2))


def rnmc_min_gm2(gm3: float, cc1_f: float, cc2_f: float, cl_f: float,
                 c2_f: float = 0.0, zeta: float = 0.7) -> float:
    """Smallest ``gm2`` (A/V) that damps the RNMC inner pole pair to ``zeta``.

    Solves ``ζ(gm2) = zeta`` from :func:`rnmc_inner_damping` in closed form.
    With ``v = √gm2`` the condition ``a1² ≥ 4ζ²·a2`` is the quadratic
    ``(CL+Cc1)·v² − b·v − Cc1·gm3 ≥ 0`` with ``b = 2ζ·Cc1·√(CL·K·gm3/Cc2)``,
    whose positive root is the floor.  It always exceeds the bare stability
    floor ``gm3·Cc1/(CL+Cc1)``.
    """
    k = 1.0 + c2_f * (1.0 / cc2_f + 1.0 / cc1_f + 1.0 / cl_f)
    b = 2.0 * zeta * cc1_f * math.sqrt(cl_f * k * gm3 / cc2_f)
    a = cl_f + cc1_f
    v = (b + math.sqrt(b * b + 4.0 * a * cc1_f * gm3)) / (2.0 * a)
    return v * v


def _poly_det(m):
    """Determinant of a square matrix of polynomials (Laplace expansion)."""
    from numpy.polynomial import polynomial as P

    if len(m) == 1:
        return m[0][0]
    total = [0.0]
    for j, entry in enumerate(m[0]):
        minor = [row[:j] + row[j + 1:] for row in m[1:]]
        term = P.polymul(entry, _poly_det(minor))
        total = P.polyadd(total, term) if j % 2 == 0 else P.polysub(total, term)
    return total


def _rnmc_polys(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f, g1, g2, g3,
                mirror_pole_hz, w_ref, buffer=None):
    """``(num, den)`` coefficient arrays (low → high) of the RNMC open loop.

    Frequency is normalised, ``s = w_ref·x``, so the coefficients stay well
    conditioned.  A third-stage current mirror is a pole on gm3 alone:
    ``gm3/(1 + s/ωm)``; the third-stage row is multiplied through by
    ``(1 + s/ωm)`` to keep every entry polynomial.

    ``buffer = (gm_f, cgs_f, g_f)`` adds a source-follower output buffer: the
    third stage then drives only the follower's gate (``cgs_f`` to the
    output), and ``cl_f`` sits on the follower's output, whose conductance is
    ``gm_f + g_f``.  A follower driving a capacitor shows a *negative* input
    resistance above ``gm_f/CL``, which is inside the Cc1 loop.
    """
    from numpy.polynomial import polynomial as P

    def lin(c0, c1):
        return [c0, c1 * w_ref]

    def mul(a, b):
        return list(P.polymul(a, b))

    m = [1.0, w_ref / (2.0 * math.pi * mirror_pole_hz)] if mirror_pole_hz else [1.0]
    c3 = 0.0 if buffer else cl_f
    rows = [
        [lin(g1, c1_f + cc1_f + cc2_f), lin(0.0, -cc2_f), lin(0.0, -cc1_f)],
        [lin(gm2, -cc2_f), lin(g2, c2_f + cc2_f), [0.0]],
        [mul(lin(0.0, -cc1_f), m), [-gm3], mul(lin(g3, c3 + cc1_f), m)],
    ]
    b = [[-gm1], [0.0], [0.0]]
    if buffer:
        gm_f, cgs_f, g_f = buffer
        rows[0].append([0.0])
        rows[1].append([0.0])
        rows[2][2] = list(P.polyadd(rows[2][2], mul(lin(0.0, cgs_f), m)))
        rows[2].append(mul(lin(0.0, -cgs_f), m))
        rows.append([[0.0], [0.0], lin(-gm_f, -cgs_f), lin(gm_f + g_f, cgs_f + cl_f)])
        b.append([0.0])
    den = _poly_det(rows)
    out = len(rows) - 1
    num = _poly_det([row[:out] + [b[i]] for i, row in enumerate(rows)])
    return num, den


def _rnmc_loop(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f, g1, g2, g3,
               mirror_pole_hz, buffer):
    """``(num, den, poles)`` of the RNMC open loop in normalised frequency.

    A zero conductance makes the DC gain infinite; a negligible one keeps
    the response anchored at a finite, real DC gain without moving anything
    that matters near crossover.
    """
    from numpy.polynomial import polynomial as P

    g1, g2, g3 = (max(g, 1e-9 * gm) for g, gm in ((g1, gm1), (g2, gm2), (g3, gm3)))
    num, den = _rnmc_polys(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                           g1, g2, g3, mirror_pole_hz, gm1 / cc1_f, buffer)
    return num, den, P.polyroots(den)


def rnmc_pole_damping(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
) -> float:
    """Worst damping ``ζ = −Re(p)/|p|`` over the RNMC non-dominant poles.

    The numerical counterpart of :func:`rnmc_inner_damping`, from the roots
    of the full open loop (same arguments as :func:`phase_margin_rnmc_deg`),
    so it also sees the parasitics, the third-stage mirror and an output
    buffer.  The dominant (lowest-frequency) pole is excluded; a real LHP
    pole counts as ``1``, and any RHP pole makes the result negative.
    """
    _num, _den, poles = _rnmc_loop(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                                   g1, g2, g3, mirror_pole_hz, buffer)
    rest = sorted(poles, key=abs)[1:]
    return float(min((-p.real / abs(p) for p in rest if abs(p) > 0), default=1.0))


def phase_margin_rnmc_deg(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
) -> float | None:
    """Phase margin in degrees of a reversed-nested-Miller three-stage amp.

    Evaluates the full open-loop transfer function of the half circuit (see
    the RNMC comment above: both caps from the first-stage output, parasitic
    ``c1_f``/``c2_f`` on the first/second-stage outputs, stage output
    conductances ``g1..g3``, an optional third-stage mirror pole and an
    optional source-follower output ``buffer``) on a frequency sweep,
    instead of the two-real-poles formula of
    :func:`phase_margin_three_stage_deg` — which cannot see the complex inner
    pole pair RNMC creates and is optimistic by tens of degrees.

    The margin is the **worst** over every 0 dB crossing, not just the first:
    a lightly damped inner pair can lift the gain back above 0 dB after the
    phase has passed −180°, which is a negative gain margin and is reported as
    a negative phase margin here.  An open loop with right-half-plane poles
    (:func:`rnmc_stable` false) reports ``0.0``: there is no margin to speak
    of.  ``None`` when the gain never reaches 0 dB.

    :param gm1: Input-pair transconductance seen by the loop, in A/V.
    :param gm2: Second-stage (inverting) signal gm in A/V.
    :param gm3: Third-stage (non-inverting) effective gm in A/V.
    :param cc1_f: Outer cap, first-stage output → third-stage output, in F.
    :param cc2_f: Inner cap, first-stage output → second-stage output, in F.
    :param cl_f: Load capacitance in F — on the third-stage output, or on the
        buffer's output when ``buffer`` is given.
    :param buffer: ``(gm_f, cgs_f, g_f)`` of a source-follower output buffer
        (its gm, gate-source capacitance and output-node conductance besides
        its own gm), or ``None``.
    """
    import numpy as np
    from numpy.polynomial import polynomial as P

    num, den, poles = _rnmc_loop(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                                 g1, g2, g3, mirror_pole_hz, buffer)
    if any(p.real > 1e-9 * abs(p) for p in poles):
        return 0.0
    # Sweep band: every root, plus the dominant pole ``den0/den1`` (lost to
    # round-off in ``polyroots`` when the DC gain is huge) and ``x = 1``,
    # the nominal crossover ωt = gm1/Cc1.
    mags = [abs(r) for r in np.concatenate([poles, P.polyroots(num)]) if abs(r) > 0]
    mags += [1.0] + ([den[0] / den[1]] if den[1] and den[0] / den[1] > 0 else [])
    x = np.logspace(math.log10(min(mags)) - 2, math.log10(max(mags)) + 2, 4000)
    a = P.polyval(1j * x, num) / P.polyval(1j * x, den)
    a = a * np.sign((num[0] / den[0]).real)
    mag_db = 20.0 * np.log10(np.abs(a))
    phase = np.degrees(np.unwrap(np.angle(a)))
    idx = np.nonzero(np.diff(np.sign(mag_db)))[0]
    if len(idx) == 0:
        return None
    pms = []
    for i in idx:
        t = mag_db[i] / (mag_db[i] - mag_db[i + 1])
        pms.append(180.0 + phase[i] + t * (phase[i + 1] - phase[i]))
    return float(min(pms))


def slew_rate_vps(current_a: float, cap_f: float) -> float:
    """Slew rate in V/s of one current-limited node.

    SR = I / C  (a fixed current charging/discharging the node's capacitance)

    An amplifier slews at the *slowest* of its nodes -- e.g. the tail current
    into the Miller cap, or the last stage's bias current into ``CL`` plus the
    Miller cap -- which the metric evaluation takes as the minimum of this over
    every node.

    :param current_a: The current available to the node in A (e.g. the tail current).
    :param cap_f: The capacitance it charges in F (e.g. the compensation capacitor).
    """
    return current_a / cap_f


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
