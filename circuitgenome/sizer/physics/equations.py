"""
Square-law MOSFET equations (Shichman-Hodges, SPICE ``level=1``) and op-amp
performance formulas.

Every function here is a closed-form textbook formula, in three groups:

1. **Square-law transistor**: one device's gm, output conductance, voltages
   and capacitances.
2. **Closed-form amplifier metrics**: quick estimates of gain, bandwidth,
   phase margin, slew rate, power, CMRR and PSRR.
3. **Compensation design rules**: the value a compensation part should take.

Multi-stage GBW, phase margin and pole damping are read off the
:mod:`.open_loop_response` instead, which solves the circuit numerically.

Units are SI unless the name says otherwise: currents in A, voltages in V,
transconductances in A/V, capacitances in F, frequencies in Hz, power in W.
Widths and lengths are in µm.
"""
from __future__ import annotations

import math

from ..verify.measure import AC_LOOP_LIMIT_DB, AC_START_HZ


# ---------------------------------------------------------------------------
# 1. Square-law transistor
# ---------------------------------------------------------------------------

def gm(mu_cox: float, w_um: float, l_um: float, ids_a: float) -> float:
    r"""Transconductance in A/V: how strongly the gate voltage steers the
    drain current.

    gm = √(2·µCox·(W/L)·\|IDS\|)

    The square law has no upper limit; see :func:`gm_ceiling` for the real
    one.

    :param mu_cox: Process transconductance µCox in A/V².
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Drain current in A (sign ignored).
    """
    return math.sqrt(2.0 * mu_cox * (w_um / l_um) * abs(ids_a))


# Highest gm/IDS a real device reaches, in 1/V (weak-inversion limit
# IDS/(n·φt), n ≈ 1.5, φt ≈ 0.0259 V at 300 K; measured ≈ 25 on PTM).
_GM_OVER_ID_MAX = 25.0


def gm_ceiling(ids_a: float) -> float:
    """Highest gm in A/V a device can reach at this current.

    gm_max = 25 /V · \\|IDS\\|

    :func:`gm` keeps growing with W/L, but a real device tops out at
    gm/IDS ≈ 25 /V, in weak inversion.  Without this cap the sizer would
    promise gm the device cannot deliver.

    :param ids_a: Drain current in A (sign ignored).
    """
    return _GM_OVER_ID_MAX * abs(ids_a)


def gd(lam: float, ids_a: float) -> float:
    r"""Output conductance in A/V: how much drain current changes per volt
    of drain voltage.

    gd = λ·\|IDS\|

    :param lam: Channel-length modulation λ in 1/V (positive).
    :param ids_a: Drain current in A (sign ignored).
    """
    return lam * abs(ids_a)


def rout(gd_top: float, gd_bot: float) -> float:
    """Output resistance in Ω of a stage with two devices on its output.

    Rout = 1 / (gd_top + gd_bot)

    :param gd_top: Output conductance of the upper (load) device in A/V.
    :param gd_bot: Output conductance of the lower (drive) device in A/V.
    :returns: ``inf`` when both are zero (an ideal stage).
    """
    total = gd_top + gd_bot
    return 1.0 / total if total > 0.0 else float("inf")


def vgs_from_ids(
    mu_cox: float, w_um: float, l_um: float, ids_a: float, vth: float
) -> float:
    """Gate-source voltage in V that makes the device carry ``ids_a``.

    VGS = Vth ± √(2·\\|IDS\\|·L / (µCox·W))

    Solves IDS = (µCox/2)·(W/L)·(VGS−Vth)² for VGS, in saturation and
    ignoring λ.

    :param mu_cox: Process transconductance µCox in A/V².
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Drain current in A (sign ignored).
    :param vth: Threshold voltage in V (negative for PMOS).
    :returns: Signed like ``vth``: positive for NMOS, negative for PMOS.
    """
    overdrive = math.sqrt(2.0 * abs(ids_a) * l_um / (mu_cox * w_um))
    return math.copysign(abs(vth) + overdrive, vth)


def vds_sat(mu_cox: float, w_um: float, l_um: float, ids_a: float) -> float:
    r"""Smallest \|VDS\| in V that keeps the device in saturation.

    VDS_sat = VGS − Vth = √(2·\|IDS\|·L / (µCox·W))

    :param mu_cox: Process transconductance µCox in A/V².
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Drain current in A (sign ignored).
    :returns: Always positive.
    """
    return math.sqrt(2.0 * abs(ids_a) * l_um / (mu_cox * w_um))


# Share of the full gate-oxide capacitance W·L·Cox that appears as Cgs in
# saturation (the channel pinches off at the drain): the long-channel 2/3.
_CGS_SAT_FRACTION = 2.0 / 3.0


def cgs(cox_f_per_um2: float | None, w_um: float, l_um: float) -> float:
    """Gate-source capacitance in F, in saturation.

    Cgs = (2/3)·W·L·Cox

    Overlap and fringe capacitance are ignored, so this reads low at short L.

    :param cox_f_per_um2: Gate-oxide capacitance per area in F/µm², or
        ``None`` when the tech gives none.
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :returns: ``0.0`` without ``cox_f_per_um2``: the caller's signal that no
        pole can be placed.
    """
    if not cox_f_per_um2:
        return 0.0
    return _CGS_SAT_FRACTION * w_um * l_um * cox_f_per_um2


def cdb(cdb_f_per_um: float | None, w_um: float) -> float:
    """Drain-bulk junction capacitance in F.

    Cdb = W·cdb

    The drain's area grows with the width, not the length.  Only the RNMC
    open-loop model reads it: behind an output buffer the third stage's drains
    are most of what its output node carries (#266).

    :param cdb_f_per_um: Junction capacitance per µm of width in F/µm, or
        ``None`` when the tech gives none (returns ``0.0``).
    :param w_um: The device's total gate width in µm.
    """
    return (cdb_f_per_um or 0.0) * w_um


def body_vth_shift(gamma: float, two_phi: float, vsb: float) -> float:
    r"""Threshold-voltage rise in V when the source sits above the bulk
    (body effect).

    ΔVth = γ·(√(2φF + Vsb) − √(2φF))

    :param gamma: Body-effect coefficient γ in √V (``0`` gives no shift).
    :param two_phi: Surface potential 2φF in V.
    :param vsb: Source-to-bulk voltage \|Vsb\| in V; negative (forward bias)
        counts as ``0``.
    :returns: Always ≥ 0.
    """
    if gamma <= 0.0:
        return 0.0
    return gamma * (math.sqrt(two_phi + max(vsb, 0.0)) - math.sqrt(two_phi))


# ---------------------------------------------------------------------------
# 2. Closed-form amplifier metrics
# ---------------------------------------------------------------------------

def open_loop_gain_db(stage_gains: list[float]) -> float:
    """Total open-loop DC gain in dB: the product of the stage gains.

    A0 = 20·log10(\\|Π A_j\\|)

    :param stage_gains: Each stage's voltage gain ``gm·Rout`` (sign ignored).
    :returns: ``-inf`` when any stage gain is zero.
    """
    product = math.prod(stage_gains)
    return 20.0 * math.log10(abs(product)) if product != 0.0 else -math.inf


#: Highest DC gain in dB the open-loop AC bench can read, for a design with
#: no predicted GBW.  The bench reads the gain at 1 mHz
#: (:data:`~circuitgenome.sizer.verify.measure.AC_START_HZ`), which is the DC
#: gain only while the dominant pole ``GBW/A0`` sits above it.  180 dB is that
#: limit at 1 MHz, the lowest GBW the example specs ask for.  On GF180 the
#: bench reads three-stage designs at 145–165 dB within a few dB of the
#: prediction, and probes up to 196 dB without railing (#287).
OPEN_LOOP_GAIN_CEILING_DB = 180.0


def open_loop_gain_measurable(
    gain_db: float | None, ceiling_db: float = OPEN_LOOP_GAIN_CEILING_DB,
    gbw_hz: float | None = None,
) -> bool:
    """Whether the open-loop AC bench can read this much DC gain.

    The bench reads the gain at its first sweep point,
    :data:`~circuitgenome.sizer.verify.measure.AC_START_HZ` (1 mHz).  Once
    the dominant pole ``GBW/A0`` falls below that point, it reads less than
    the DC gain.  So with a GBW the limit is ``20·log10(GBW / 1 mHz)``
    (160 dB at 100 kHz, 180 dB at 1 MHz), capped at
    :data:`~circuitgenome.sizer.verify.measure.AC_LOOP_LIMIT_DB` (≈ 212 dB),
    where the bench's DC feedback loop closes.  This is advice only: the
    predicted gain is still reported and SPICE has the last word.

    :param gain_db: Predicted open-loop DC gain in dB, or ``None`` when none
        was computed (counts as measurable).
    :param ceiling_db: The limit in dB when ``gbw_hz`` is unknown.
    :param gbw_hz: Predicted gain-bandwidth product in Hz, or ``None``.
    :returns: ``True`` when the bench can read the gain.
    """
    if gain_db is None:
        return True
    if gbw_hz is not None and gbw_hz > 0:
        ceiling_db = min(20.0 * math.log10(gbw_hz / AC_START_HZ),
                         AC_LOOP_LIMIT_DB)
    return gain_db <= ceiling_db


def unity_gain_bw(gm1_a_v: float, cc_f: float, *, gain: float = 1.0) -> float:
    """Gain-bandwidth product (GBW) in Hz of a Miller-compensated amplifier,
    textbook form.

    GBW = gain·gm1 / (2π·Cc)

    Poles near the crossing pull the real GBW lower; the multi-stage metrics
    read it off the open-loop model instead
    (:func:`~.open_loop_response.two_stage_loop`).

    :param gm1_a_v: Input-pair transconductance in A/V.
    :param cc_f: Compensation cap in F.
    :param gain: Gain from the Miller output to the amplifier output: ``1.0``
        without an output buffer, ~0.88 behind a GF180 NMOS follower (#274).
    """
    return gain * gm1_a_v / (2.0 * math.pi * cc_f)


def phase_margin_single_stage_deg(gbw_hz: float, mirror_pole_hz: float) -> float:
    """Phase margin in degrees of a single-stage OTA compensated by its load.

    PM ≈ 90° − arctan(GBW / f_mirror)

    The load ``CL`` sets the main pole, which alone gives 90°.  The current
    mirror's node adds the next pole and takes the margin below 90°; it
    matters only when GBW gets close to it.  (In a two-stage amp the roles
    swap, see :func:`phase_margin_two_stage_deg`.)

    :param gbw_hz: Gain-bandwidth product in Hz (``gm1/(2π·CL)``).
    :param mirror_pole_hz: Pole of the current-mirror node in Hz.
    """
    return 90.0 - math.degrees(math.atan(gbw_hz / mirror_pole_hz))


def phase_margin_two_stage_deg(
    gm1: float, gm2: float, cc_f: float, cl_f: float
) -> float:
    """Phase margin in degrees of a two-stage Miller op-amp, textbook form.

    PM ≈ 90° − arctan(gm1·CL / (gm2·Cc))

    The Miller cap sets the main pole; the output, at ``gm2/CL``, sets the
    next one.  The mirror pole is ignored.  The sizer plans with this; the
    reported margin comes from :func:`~.open_loop_response.two_stage_loop`.

    :param gm1: Input-pair transconductance in A/V.
    :param gm2: Second-stage transconductance in A/V.
    :param cc_f: Compensation cap in F.
    :param cl_f: Load capacitance in F.
    """
    return 90.0 - math.degrees(math.atan(gm1 * cl_f / (gm2 * cc_f)))


def phase_margin_three_stage_deg(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
) -> float:
    """Phase margin in degrees of a nested-Miller (NMC) three-stage op-amp,
    textbook form.

    PM ≈ 90° − arctan(ωt·Cc2/gm2) − arctan(ωt·CL/gm3),  ωt = gm1/Cc1

    Treats the two extra poles, at ``gm2/Cc2`` and ``gm3/CL``, as separate.
    They really form a pair that can ring, so the reported margin comes from
    :func:`~.open_loop_response.nested_miller_loop` (and, for reversed nested Miller,
    :func:`~.open_loop_response.phase_margin_rnmc_deg`).

    :param gm1: Input-pair transconductance in A/V.
    :param gm2: Second-stage transconductance in A/V.
    :param gm3: Third-stage transconductance in A/V.
    :param cc1_f: Outer compensation cap in F.
    :param cc2_f: Inner compensation cap in F.
    :param cl_f: Load capacitance in F.
    """
    wt = gm1 / cc1_f
    lag2 = math.degrees(math.atan(wt * cc2_f / gm2))
    lag3 = math.degrees(math.atan(wt * cl_f / gm3))
    return 90.0 - lag2 - lag3


def slew_rate_vps(current_a: float, cap_f: float) -> float:
    """Slew rate in V/s of one node: how fast a fixed current can charge its
    capacitance.

    SR = I / C

    An amplifier slews at the pace of its slowest node (for example the tail
    current into the Miller cap); the metrics take the minimum over nodes.

    :param current_a: Current available to the node in A.
    :param cap_f: Capacitance it charges in F.
    """
    return current_a / cap_f


def quiescent_power(vdd: float, vss: float, supply_currents_a: list[float]) -> float:
    """Power in W drawn from the supplies with no signal.

    P = (VDD − VSS)·Σ\\|I\\|

    :param vdd: Positive supply in V.
    :param vss: Negative supply in V.
    :param supply_currents_a: Currents drawn from the positive supply in A
        (sign ignored).
    """
    return (vdd - vss) * sum(abs(i) for i in supply_currents_a)


def cmrr_db(gm1: float, gd_tail: float) -> float:
    """Common-mode rejection ratio in dB: how well the amplifier ignores a
    signal on both inputs at once.

    CMRR ≈ gm1 / (2·gd_tail)

    First-order: only the tail current source's leakage is counted.

    :param gm1: Input-pair transconductance in A/V.
    :param gd_tail: Output conductance of the tail current source in A/V.
    :returns: ``inf`` for an ideal tail (``gd_tail == 0``).
    """
    if gd_tail == 0.0:
        return math.inf
    return 20.0 * math.log10(gm1 / (2.0 * gd_tail))


def psrr_db_approx(gm2: float, gd_bias: float) -> float:
    """Positive-supply rejection (PSRR+) in dB: how well the output ignores
    ripple on VDD.

    PSRR+ ≈ gm2 / gd_bias

    A rough estimate for a simple two-stage op-amp; SPICE gives the real
    value.

    :param gm2: Second-stage transconductance in A/V.
    :param gd_bias: Output conductance of the second stage's bias device in
        A/V.
    :returns: ``inf`` for an ideal bias device (``gd_bias == 0``).
    """
    if gd_bias == 0.0:
        return math.inf
    return 20.0 * math.log10(gm2 / gd_bias)


# ---------------------------------------------------------------------------
# 3. Compensation design rules
# ---------------------------------------------------------------------------
# Reversed nested Miller (RNMC) puts both caps on the first-stage output: Cc1
# to the output (around gm2·gm3) and Cc2 to the second-stage output (around
# gm2 only).  That couples the two extra poles into a pair, which can ring or
# go unstable.  For high stage gains the loop's denominator reduces to
#
#   s·Cc1·gm2·gm3 · [1 + s·a1 + s²·a2]
#   a1 = Cc2·((CL+Cc1)·gm2 − Cc1·gm3) / (Cc1·gm2·gm3)
#   a2 = CL·Cc2·K / (gm2·gm3),   K = 1 + C2·(1/Cc2 + 1/Cc1 + 1/CL)
#
# with C2 the parasitic on the second-stage output (mostly the third stage's
# wide gate).  Matching 1 + a1·s + a2·s² to 1 + (2ζ/ωn)·s + s²/ωn² gives the
# pair's damping ζ = a1/(2·√a2), positive (stable) only while
# gm3 < gm2·(1 + CL/Cc1).  docs/theory/rnmc_stability.rst derives it.


def compensation_resistor_ohm(cc_f: float, c_out_f: float, gm_out: float) -> float:
    """Resistor in Ω to put in series with a compensation cap.

    R = (Cc + C) / (gm_out·Cc)

    This places the branch's zero on the output pole of the stage it wraps,
    so the two cancel (#108).

    :param cc_f: The branch's compensation cap in F.
    :param c_out_f: Load on the wrapped stage's output in F: ``CL``, except in
        RNMC, where it is the output buffer's gate (#262) or ``0`` without a
        buffer (#269), giving ``R ≈ 1/gm_out``.
    :param gm_out: Transconductance of the wrapped stage in A/V.
    """
    return (cc_f + c_out_f) / (gm_out * cc_f)


def rnmc_min_gm2(gm3: float, cc1_f: float, cc2_f: float, cl_f: float,
                 c2_f: float = 0.0, zeta: float = 0.7) -> float:
    """Smallest second-stage gm in A/V that damps the RNMC pole pair to
    ``zeta``.

    Solves ``ζ = a1/(2·√a2) ≥ zeta`` (see the comment above) for gm2.  With
    ``v = √gm2`` it is the quadratic::

        (CL+Cc1)·v² − b·v − Cc1·gm3 ≥ 0,   b = 2ζ·Cc1·√(CL·K·gm3/Cc2)

    whose positive root is the answer.  A quick seed: it ignores output
    conductances, the first-stage parasitic, a third-stage mirror and an
    output buffer, which :func:`~.open_loop_response.rnmc_pole_damping` includes.

    :param gm3: Third-stage transconductance in A/V.
    :param cc1_f: Outer cap, first-stage output → output, in F.
    :param cc2_f: Inner cap, first-stage output → second-stage output, in F.
    :param cl_f: Load capacitance in F.
    :param c2_f: Parasitic on the second-stage output in F.
    :param zeta: Target damping; ``0.7`` is the Butterworth placement (no
        resonance peak).
    """
    k = 1.0 + c2_f * (1.0 / cc2_f + 1.0 / cc1_f + 1.0 / cl_f)
    b = 2.0 * zeta * cc1_f * math.sqrt(cl_f * k * gm3 / cc2_f)
    a = cl_f + cc1_f
    v = (b + math.sqrt(b * b + 4.0 * a * cc1_f * gm3)) / (2.0 * a)
    return v * v
