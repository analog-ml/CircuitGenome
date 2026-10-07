"""
Level-1 MOSFET equations (Shichman-Hodges) and op-amp performance formulas.

Most functions are closed-form textbook formulas.  The exception is the
reversed-nested-Miller (RNMC) three-stage loop, whose phase margin and pole
damping come from a small nodal model solved numerically — see
:doc:`/theory/rnmc_stability`.

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

    Square law only: it ignores the weak-inversion ceiling, see
    :func:`gm_ceiling`.

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
    """Physical upper bound on gm in A/V (weak-inversion limit).

    gm_max = 25 /V · \\|IDS\\|

    The square-law :func:`gm` keeps growing with W/L; a real device cannot
    exceed gm/IDS = 1/(n·φt) ≈ 25 /V, reached in weak inversion.

    :param ids_a: Drain-source current in A (sign ignored).
    """
    return _GM_OVER_ID_MAX * abs(ids_a)


def gd(lam: float, ids_a: float) -> float:
    r"""Output conductance gd in A/V.

    gd = λ·\|IDS\|

    :param lam: Channel-length modulation coefficient λ in 1/V (positive).
    :param ids_a: Drain-source current in A (sign ignored).
    """
    return lam * abs(ids_a)


def rout(gd_top: float, gd_bot: float) -> float:
    """Stage output resistance in Ω.

    Rout = 1 / (gd_top + gd_bot)

    :param gd_top: Output conductance of the upper (load) transistor in A/V.
    :param gd_bot: Output conductance of the lower (drive) transistor in A/V.
    :returns: ``inf`` when both conductances are zero (an ideal stage).
    """
    total = gd_top + gd_bot
    return 1.0 / total if total > 0.0 else float("inf")


def vgs_from_ids(
    mu_cox: float, w_um: float, l_um: float, ids_a: float, vth: float
) -> float:
    """Gate-source voltage in V (saturation, λ=0 approximation).

    VGS = Vth ± √(2·\\|IDS\\|·L / (µCox·W))

    Solves IDS = (µCox/2)·(W/L)·(VGS−Vth)² for VGS.

    :param mu_cox: Process transconductance µCox in A/V².
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Drain-source current in A (sign ignored).
    :param vth: Threshold voltage in V (negative for PMOS).
    :returns: Signed like ``vth``: positive for NMOS, negative for PMOS.
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

    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
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

    :param mu_cox: Process transconductance µCox in A/V².
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Drain-source current in A (sign ignored).
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

    A0 = 20·log10(\\|Π A_j\\|)

    :param stage_gains: Per-stage voltage gains A_j = gm_j·Rout_j
        (dimensionless, sign ignored).
    :returns: ``-inf`` when any stage gain is zero.
    """
    product = math.prod(stage_gains)
    return 20.0 * math.log10(abs(product)) if product != 0.0 else -math.inf


#: Empirical open-loop DC-gain ceiling for the ngspice open-loop AC bench (dB).
#: The analytical ``gain_db`` is an un-derated cascade product; above this ceiling
#: the open-loop DC operating point cannot hold the output at mid-rail without
#: feedback, so the measured open-loop AC gain rails to ~0 dB even when the
#: per-device DC bias is sound.  Calibrated to observed GF180 behaviour (#155):
#: measurable two-stage designs top out ~133 dB, whereas three-stage cascades at
#: ≥175 dB rail on every PVT corner.  150 dB sits safely between the two bands to
#: minimise false positives.
OPEN_LOOP_GAIN_CEILING_DB = 150.0


def open_loop_gain_measurable(
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
    """Phase margin in degrees for a two-stage Miller op-amp.

    PM ≈ 90° − arctan(gm1·CL / (gm2·Cc))

    Valid when the dominant pole is at 1/(Rout1·Cc) and the non-dominant
    pole is at gm2/CL (internal mirror pole neglected).  The Miller cap
    splits the two poles far apart, so the non-dominant one is real and a
    single lag term is the whole story — there is no pole pair to ring.

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
    """Phase margin in degrees for a nested-Miller (NMC) three-stage op-amp.

    PM ≈ 90° − arctan(ωt·Cc2/gm2) − arctan(ωt·CL/gm3)
    where ωt = gm1/Cc1.

    Treats the two non-dominant poles as separate real poles at gm2/Cc2 and
    gm3/CL.  They are really a pole pair, which can ring however healthy
    this margin looks; NMC sizing guards that with a damping rule instead
    (:func:`~.preprocess.compute_requirements`).  Reversed nested Miller,
    whose pair can also sit in the right half plane, uses
    :func:`phase_margin_rnmc_deg`.

    :param gm1: Input-pair transconductance in A/V.
    :param gm2: Second-stage signal transistor gm in A/V.
    :param gm3: Third-stage signal transistor gm in A/V.
    :param cc1_f: Outer compensation capacitor in F.
    :param cc2_f: Inner compensation capacitor in F.
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
# gm2·gm3) and Cc2 to the second-stage output (around gm2 only).  That couples
# the two non-dominant poles into a pole pair, which can ring or sit in the
# right half plane — a two-real-poles formula like
# phase_margin_three_stage_deg cannot see either.  The full model below solves
# the half circuit's nodal equations instead; docs/theory/rnmc_stability.rst
# walks through it.  For high stage gains its denominator reduces to
#
#   s·Cc1·gm2·gm3 · [1 + s·a1 + s²·a2]
#   a1 = Cc2·((CL+Cc1)·gm2 − Cc1·gm3) / (Cc1·gm2·gm3)
#   a2 = CL·Cc2·K / (gm2·gm3),   K = 1 + C2·(1/Cc2 + 1/Cc1 + 1/CL)
#
# with C2 the parasitic on the second-stage output (in practice the third
# stage's wide input gate).  Matching 1 + a1·s + a2·s² to the standard form
# 1 + (2ζ/ωn)·s + s²/ωn² gives the pair's damping ζ = a1/(2·√a2): it is
# positive — the pair stable — only while gm3 < gm2·(1 + CL/Cc1).


def rnmc_min_gm2(gm3: float, cc1_f: float, cc2_f: float, cl_f: float,
                 c2_f: float = 0.0, zeta: float = 0.7) -> float:
    """Smallest ``gm2`` in A/V that damps the RNMC pole pair to ``zeta``.

    Closed form, from the simplified pole-pair quadratic
    ``1 + a1·s + a2·s²`` (defined in the RNMC section comment of this module
    and in :doc:`/theory/rnmc_stability`), whose damping is
    ``ζ = a1/(2·√a2)``.  It ignores the stage output conductances, the
    first-stage parasitic, a third-stage mirror and an output buffer, which
    :func:`rnmc_pole_damping` includes.  With ``v = √gm2`` the condition
    ``a1² ≥ 4ζ²·a2`` is the quadratic::

        (CL+Cc1)·v² − b·v − Cc1·gm3 ≥ 0,   b = 2ζ·Cc1·√(CL·K·gm3/Cc2)

    whose positive root is the floor.  It always exceeds the bare stability
    floor ``gm3·Cc1/(CL+Cc1)``.  ``zeta = 0.7`` is the Butterworth placement
    (no resonance peak).

    :param gm3: Third-stage gm in A/V.
    :param cc1_f: Outer cap, first-stage output → output, in F.
    :param cc2_f: Inner cap, first-stage output → second-stage output, in F.
    :param cl_f: Load capacitance in F.
    :param c2_f: Parasitic on the second-stage output in F.
    :param zeta: Target damping of the pole pair.
    """
    k = 1.0 + c2_f * (1.0 / cc2_f + 1.0 / cc1_f + 1.0 / cl_f)
    b = 2.0 * zeta * cc1_f * math.sqrt(cl_f * k * gm3 / cc2_f)
    a = cl_f + cc1_f
    v = (b + math.sqrt(b * b + 4.0 * a * cc1_f * gm3)) / (2.0 * a)
    return v * v


def _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f, g1, g2, g3,
                  mirror_pole_hz, buffer):
    """Nodal equations ``(G + s·C)·v = b`` of the RNMC half circuit (Vin = 1).

    ``v`` holds the node voltages: ``v[0..2]`` the first-, second- and
    third-stage outputs, then the third-stage mirror node and the buffer's
    output when present.  Returns ``(G, C, b, out)``, ``out`` being the index
    of the node that drives ``CL``.

    A zero output conductance would make the DC gain infinite; flooring it at
    ``1e-9·gm`` keeps the DC gain finite without moving anything near
    crossover.
    """
    import numpy as np

    g1, g2, g3 = (max(g, 1e-9 * gm) for g, gm in ((g1, gm1), (g2, gm2), (g3, gm3)))
    V1, V2, V3 = 0, 1, 2
    n = 3 + bool(mirror_pole_hz) + bool(buffer)
    G, C, b = np.zeros((n, n)), np.zeros((n, n)), np.zeros(n)

    def cap(i, j, c):
        """Capacitor ``c`` between nodes ``i`` and ``j`` (``j=None``: ground)."""
        C[i, i] += c
        if j is not None:
            C[j, j] += c
            C[i, j] -= c
            C[j, i] -= c

    def vccs(i, ctrl, gm):
        """Current ``gm·v[ctrl]`` drawn out of node ``i``."""
        G[i, ctrl] += gm

    # Stage 1 (inverting): gm1·Vin out of V1.  Both Miller caps hang off V1.
    b[V1] = -gm1
    G[V1, V1] += g1
    cap(V1, None, c1_f)
    cap(V1, V3, cc1_f)
    cap(V1, V2, cc2_f)

    # Stage 2 (inverting): gm2·V1 out of V2.
    vccs(V2, V1, gm2)
    G[V2, V2] += g2
    cap(V2, None, c2_f)

    # Stage 3 (non-inverting): gm3·V2 into V3 — through a current mirror when
    # there is one, an extra node with v_m·(1 + s/ωm) = V2.
    drive = V2
    if mirror_pole_hz:
        m = 3
        vccs(m, V2, -1.0)
        G[m, m] += 1.0
        cap(m, None, 1.0 / (2.0 * math.pi * mirror_pole_hz))
        drive = m
    vccs(V3, drive, -gm3)
    G[V3, V3] += g3

    # Load: CL on V3, or on a source-follower buffer's output, which the
    # follower charges with gm_f·(V3 − v_out).
    if buffer is None:
        cap(V3, None, cl_f)
        return G, C, b, V3
    gm_f, cgs_f, g_f = buffer
    out = n - 1
    cap(V3, out, cgs_f)
    vccs(out, V3, -gm_f)
    G[out, out] += gm_f + g_f
    cap(out, None, cl_f)
    return G, C, b, out


def _poles(G, C):
    """Roots of ``det(G + s·C) = 0`` in rad/s: the eigenvalues of ``−C⁻¹G``."""
    import numpy as np

    return np.linalg.eigvals(-np.linalg.solve(C, G))


def _open_loop_gain(G, C, b, out, w):
    """``A(jω) = v[out]`` at each angular frequency ``w``, signed so ``A(0) > 0``."""
    import numpy as np

    rhs = np.broadcast_to(b[:, None], (len(w), len(b), 1))
    v = np.linalg.solve(G + 1j * w[:, None, None] * C, rhs)
    return v[:, out, 0] * np.sign(np.linalg.solve(G, b)[out])


def rnmc_pole_damping(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
) -> float:
    """Worst damping ``ζ`` over the RNMC loop's non-dominant poles.

    ζ = −Re(p) / \\|p\\|

    — the cosine of a pole's angle from the negative real axis.  Read off
    the poles of the full model (arguments as for
    :func:`phase_margin_rnmc_deg`), so it sees the parasitics, the
    third-stage mirror and an output buffer.  The dominant (smallest) pole is
    skipped; a real pole counts as ``1``.  The sizer aims for ``0.7``, the
    Butterworth placement (no resonance peak).

    :returns: Negative when any non-dominant pole is in the right half plane.
    """
    G, C, _, _ = _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                               g1, g2, g3, mirror_pole_hz, buffer)
    non_dominant = sorted(_poles(G, C), key=abs)[1:]
    return float(min((-p.real / abs(p) for p in non_dominant if abs(p) > 0),
                     default=1.0))


def phase_margin_rnmc_deg(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
) -> float | None:
    """Phase margin in degrees of a reversed-nested-Miller three-stage amp.

    PM = min over every 0 dB crossing of (180° + ∠A(jω))

    ``A(jω)`` is the open loop of the full model — the half circuit's nodal
    equations solved on a frequency sweep — not the two-real-poles formula of
    :func:`phase_margin_three_stage_deg`, which cannot see the RNMC pole pair
    and is optimistic by tens of degrees.  The **worst** crossing counts: a
    lightly damped pair can lift the gain back above 0 dB after the phase has
    passed −180°, which is reported as a negative margin.

    :param gm1: Input-pair transconductance seen by the loop, in A/V.
    :param gm2: Second-stage (inverting) signal gm in A/V.
    :param gm3: Third-stage (non-inverting) effective gm in A/V.
    :param cc1_f: Outer cap, first-stage output → third-stage output, in F.
    :param cc2_f: Inner cap, first-stage output → second-stage output, in F.
    :param cl_f: Load capacitance in F — on the third-stage output, or on the
        buffer's output when ``buffer`` is given.
    :param c1_f: Parasitic on the first-stage output in F.
    :param c2_f: Parasitic on the second-stage output in F.
    :param g1: First-stage output conductance in A/V.
    :param g2: Second-stage output conductance in A/V.
    :param g3: Third-stage output conductance in A/V.
    :param mirror_pole_hz: Pole of a current mirror inside the third stage, in
        Hz, or ``None`` when there is none.
    :param buffer: ``(gm_f, cgs_f, g_f)`` of a source-follower output buffer
        between the third stage and ``CL`` — its gm, its gate-source
        capacitance, and its output-node conductance besides its own gm — or
        ``None``.
    :returns: ``0.0`` when the open loop has a right-half-plane pole (no
        margin to speak of); ``None`` when the gain never reaches 0 dB.
    """
    import numpy as np

    G, C, b, out = _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                                 g1, g2, g3, mirror_pole_hz, buffer)
    poles = _poles(G, C)
    if any(p.real > 1e-9 * abs(p) for p in poles):
        return 0.0
    # Sweep two decades past every pole and the nominal crossover gm1/Cc1.
    corners = [abs(p) for p in poles if abs(p) > 0] + [gm1 / cc1_f]
    w = np.logspace(math.log10(min(corners)) - 2, math.log10(max(corners)) + 2, 4000)
    a = _open_loop_gain(G, C, b, out, w)
    mag_db = 20.0 * np.log10(np.abs(a))
    phase = np.degrees(np.unwrap(np.angle(a)))
    crossings = np.nonzero(np.diff(np.sign(mag_db)))[0]
    if len(crossings) == 0:
        return None
    pms = []
    for i in crossings:   # interpolate linearly to the exact 0 dB point
        t = mag_db[i] / (mag_db[i] - mag_db[i + 1])
        pms.append(180.0 + phase[i] + t * (phase[i + 1] - phase[i]))
    return float(min(pms))


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

    :param vdd: Positive supply in V.
    :param vss: Negative supply in V.
    :param supply_currents_a: Currents drawn from the positive supply in A
        (sign ignored).
    """
    return (vdd - vss) * sum(abs(i) for i in supply_currents_a)


def cmrr_db(gm1: float, gd_tail: float) -> float:
    """Common-mode rejection ratio in dB (first-order approximation).

    CMRR ≈ gm1 / (2·gd_tail)

    :param gm1: Input-pair transconductance in A/V.
    :param gd_tail: Output conductance of the tail current source in A/V.
    :returns: ``inf`` for an ideal tail (``gd_tail == 0``).
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
    :returns: ``inf`` for an ideal bias device (``gd_bias == 0``).
    """
    if gd_bias == 0.0:
        return math.inf
    return 20.0 * math.log10(gm2 / gd_bias)
