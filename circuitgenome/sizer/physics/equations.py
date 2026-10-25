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

    It places the mirror pole of a load-compensated single-stage OTA (issue
    #221), where the dominant pole is set by the external ``CL`` and the
    *non*-dominant one by the gate capacitance on the mirror node, and loads
    the RNMC model's nodes. Overlap and fringe capacitance are ignored, so
    this under-estimates Cgs at short L.

    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param cox_f_per_um2: Gate-oxide capacitance per unit area in F/µm², or
        ``None`` for a tech that does not supply one — which returns ``0.0``,
        the caller's signal that no pole can be placed.
    """
    if not cox_f_per_um2:
        return 0.0
    return _CGS_SAT_FRACTION * w_um * l_um * cox_f_per_um2


def cdb(cdb_f_per_um: float | None, w_um: float) -> float:
    """Drain-bulk junction capacitance in F.

    Cdb = W·cdb

    The junction scales with the drain's area, which the PDK lays out in
    proportion to the width, so it is independent of L.  Only the RNMC model
    reads it (issue #266): behind an output buffer the third stage's drains
    are most of what its output node carries.

    :param cdb_f_per_um: Junction capacitance per µm of width in F/µm, or
        ``None`` for a tech that does not supply one — which returns ``0.0``.
    :param w_um: Gate width in µm (the device's total width).
    """
    return (cdb_f_per_um or 0.0) * w_um


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


#: Open-loop DC-gain ceiling for the ngspice open-loop AC bench (dB).
#: The bench reads the DC gain at its first sweep point, 1 mHz, so the reading
#: is the DC gain only while the dominant pole ``GBW/A0`` stays above 1 mHz:
#: up to A0 = 1e9 (180 dB) for a 1 MHz GBW, the lowest GBW the example specs
#: ask for.  (Its ``Lfb``/``Cfb`` DC loop stays open to ~210 dB.)  On GF180
#: the bench measures three-stage designs at 145–165 dB within a few dB of
#: the prediction, and W/L-scaled probes read up to 196 dB without railing
#: (#287).  The 150 dB of #155 flagged all of them.
OPEN_LOOP_GAIN_CEILING_DB = 180.0


def open_loop_gain_measurable(
    gain_db: float | None, ceiling_db: float = OPEN_LOOP_GAIN_CEILING_DB
) -> bool:
    """Whether the analytical open-loop gain is measurable on an open-loop bench.

    Above ``ceiling_db`` the open-loop AC bench's first (1 mHz) point no
    longer sits below the dominant pole, so the measured gain falls short of
    the DC gain the model predicts.  This is an **advisory** heuristic, not a
    hard prune — the analytical ``gain_db`` is still reported and SPICE
    remains the authority.  See
    :data:`OPEN_LOOP_GAIN_CEILING_DB`.

    :param gain_db: Analytical open-loop DC gain in dB, or ``None`` when no gain
        was computed (single-stage / gated).  ``None`` → measurable (no evidence
        of railing).
    :returns: ``True`` when measurable, ``False`` when the bench cannot read
        the predicted DC gain.
    """
    return gain_db is None or gain_db <= ceiling_db


def unity_gain_bw(gm1_a_v: float, cc_f: float, *, gain: float = 1.0) -> float:
    """Unity-gain bandwidth (GBW) in Hz of a single-pole loop.

    GBW = gain·gm1 / (2π·Cc)

    ``gain`` is the gain after the integrating stage: a source follower's
    ``gm/(gm + gmb + g_out)``, ~0.88 for a GF180 NMOS follower (issue #274).
    Poles near crossover pull the real crossing lower; the multi-stage
    metrics read it off the full model instead (:func:`two_stage_loop`).

    :param gm1_a_v: Input-pair transconductance in A/V.
    :param cc_f: Compensation capacitor in F.
    :param gain: Gain between the Miller output and the amplifier output,
        ``1.0`` without an output buffer.
    """
    return gain * gm1_a_v / (2.0 * math.pi * cc_f)


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


def compensation_resistor_ohm(cc_f: float, c_out_f: float, gm_out: float) -> float:
    """Series resistor in Ω of a nulling / indirect compensation branch.

    R = (Cc + C)/(gm_out·Cc)

    places the compensation zero on the output pole of the stage the branch
    wraps (issue #108).  ``C`` is the load that stage drives: ``CL`` —
    except in an RNMC amplifier, where it is the output buffer's gate (issue
    #262) or ``0`` without a buffer (issue #269), so R comes out close to
    ``1/gm_out``.

    :param cc_f: The branch's compensation cap in F.
    :param c_out_f: Load on the wrapped stage's output in F.
    :param gm_out: gm of the wrapped (last gain) stage in A/V.
    """
    return (cc_f + c_out_f) / (gm_out * cc_f)


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


class _Network:
    """Nodal equations ``(G + s·C)·v = b`` of a small-signal half circuit,
    assembled one element at a time (``None`` as a node is ground)."""

    def __init__(self):
        self.n = 0
        self._g: list[tuple[int, int, float]] = []
        self._c: list[tuple[int, int, float]] = []
        self._b: dict[int, float] = {}

    def node(self) -> int:
        self.n += 1
        return self.n - 1

    @staticmethod
    def _stamp(entries, i, j, x):
        entries.append((i, i, x))
        if j is not None:
            entries += [(j, j, x), (i, j, -x), (j, i, -x)]

    def cap(self, i, j, c):
        """Capacitor ``c`` between nodes ``i`` and ``j``."""
        self._stamp(self._c, i, j, c)

    def conductance(self, i, j, g):
        """Conductance ``g`` between nodes ``i`` and ``j``."""
        self._stamp(self._g, i, j, g)

    def vccs(self, i, ctrl, gm):
        """Current ``gm·v[ctrl]`` drawn out of node ``i``."""
        self._g.append((i, ctrl, gm))

    def input_stage(self, gm, g, c) -> int:
        """The input pair (inverting): ``gm·Vin`` out of a new node, Vin = 1."""
        v = self.node()
        self._b[v] = -gm
        self.conductance(v, None, g)
        self.cap(v, None, c)
        return v

    def stage(self, ctrl, gm, g, c, *, inverting=True, mirror_pole_hz=None) -> int:
        """A gain stage driven by ``ctrl`` into a new node.  A non-inverting
        stage passes through its current mirror when it has one: an extra
        node with ``v_m·(1 + s/ωm) = v[ctrl]``."""
        if mirror_pole_hz:
            m = self.node()
            self.vccs(m, ctrl, -1.0)
            self.conductance(m, None, 1.0)
            self.cap(m, None, 1.0 / (2.0 * math.pi * mirror_pole_hz))
            ctrl = m
        v = self.node()
        self.vccs(v, ctrl, gm if inverting else -gm)
        self.conductance(v, None, g)
        self.cap(v, None, c)
        return v

    def branch(self, i, j, c, r):
        """Compensation cap ``c`` from ``i`` to ``j``, through ``r`` when set."""
        if not r:
            self.cap(i, j, c)
            return
        x = self.node()
        self.cap(i, x, c)
        self.conductance(x, j, 1.0 / r)

    def load(self, v, cl_f, buffer) -> int:
        """``CL`` on ``v``, or on a source-follower buffer's output, which the
        follower charges with ``gm_f·(v − v_out)``.  Returns the node on CL."""
        if buffer is None:
            self.cap(v, None, cl_f)
            return v
        gm_f, cgs_f, g_f = buffer
        out = self.node()
        self.cap(v, out, cgs_f)
        self.vccs(out, v, -gm_f)
        self.conductance(out, None, gm_f + g_f)
        self.cap(out, None, cl_f)
        return out

    def matrices(self):
        """``(G, C, b)`` as numpy arrays."""
        import numpy as np

        G, C, b = np.zeros((self.n, self.n)), np.zeros((self.n, self.n)), np.zeros(self.n)
        for m, entries in ((G, self._g), (C, self._c)):
            for i, j, x in entries:
                m[i, j] += x
        for i, x in self._b.items():
            b[i] = x
        return G, C, b


def _floor_g(*pairs):
    """Output conductances floored at ``1e-9·gm``: a zero one would make the
    DC gain infinite, and the floor keeps it finite without moving anything
    near crossover."""
    return [max(g, 1e-9 * gm) for g, gm in pairs]


def _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f, g1, g2, g3,
                  mirror_pole_hz, buffer, c3_f=0.0, r1_ohm=0.0, r2_ohm=0.0):
    """Nodal equations ``(G + s·C)·v = b`` of the RNMC half circuit (Vin = 1).

    Both Miller caps hang off the first-stage output: ``Cc1`` to the third
    stage's output, ``Cc2`` to the second's, each through its series resistor
    (``r1_ohm``, ``r2_ohm``) when set.  The second stage inverts; the third
    does not, through its current mirror when it has one.  ``c3_f`` is the
    third-stage output's capacitance to ground besides ``CL``.  Returns
    ``(G, C, b, out)``, ``out`` being the index of the node that drives ``CL``.
    """
    g1, g2, g3 = _floor_g((g1, gm1), (g2, gm2), (g3, gm3))
    net = _Network()
    v1 = net.input_stage(gm1, g1, c1_f)
    v2 = net.stage(v1, gm2, g2, c2_f)
    v3 = net.stage(v2, gm3, g3, c3_f, inverting=False, mirror_pole_hz=mirror_pole_hz)
    net.branch(v1, v3, cc1_f, r1_ohm)
    net.branch(v1, v2, cc2_f, r2_ohm)
    out = net.load(v3, cl_f, buffer)
    return (*net.matrices(), out)


def _two_stage_half(net, drive, gm2, cc_f, cl_f, c1_f, c2_f, g1, g2, buffer, r_ohm):
    """One two-stage Miller half circuit in ``net``: the input pair (``drive``
    = its share of gm1, signed) into an inverting second stage, with ``Cc`` --
    through ``r_ohm`` when set -- from its input to its output.  ``c2_f`` is
    the second-stage output's capacitance besides ``CL``.  Returns
    ``(first-stage node, node on CL)``."""
    v1 = net.input_stage(drive, g1, c1_f)
    v2 = net.stage(v1, gm2, g2, c2_f)
    net.branch(v1, v2, cc_f, r_ohm)
    return v1, net.load(v2, cl_f, buffer)


def _nmc_half(net, drive, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f, c3_f, g1, g2, g3,
              mirror_pole_hz, buffer, r1_ohm, r2_ohm):
    """One nested-Miller half circuit in ``net``: a non-inverting second stage
    (through its mirror when it has one), an inverting third, ``Cc1`` from
    the first-stage output and ``Cc2`` from the second's, both to the third
    stage's output.  Returns ``(first-stage node, node on CL)``."""
    v1 = net.input_stage(drive, g1, c1_f)
    v2 = net.stage(v1, gm2, g2, c2_f, inverting=False, mirror_pole_hz=mirror_pole_hz)
    v3 = net.stage(v2, gm3, g3, c3_f)
    net.branch(v1, v3, cc1_f, r1_ohm)
    net.branch(v2, v3, cc2_f, r2_ohm)
    return v1, net.load(v3, cl_f, buffer)


def _miller_network(half, gm1, halves, cmfb):
    """Nodal equations ``(G, C, b, out)`` of a two-stage or nested-Miller
    amplifier (Vin = 1) from its ``half(net, drive, half_args)`` builder.

    One entry in ``halves`` is a single-ended amplifier.  Two are the
    ``(p, n)`` halves of a fully-differential one, driven by ``±gm1/2`` and
    read at ``out = (p, n)``, their difference.  Matched halves give exactly
    the single half's response; when their compensation differs, the halves
    are coupled through the common mode, which the CMFB feeds back:
    ``cmfb = (gm_c, pole_hz)`` injects ``gm_c·v_cm`` into each first-stage
    node, ``v_cm`` the outputs' common mode behind one pole (issue #274).
    Without it the halves are independent and the output averages them.
    """
    net = _Network()
    if len(halves) == 1:
        _v1, out = half(net, gm1, halves[0])
        return (*net.matrices(), out)
    (v1p, op), (v1n, on) = half(net, gm1 / 2.0, halves[0]), half(net, -gm1 / 2.0, halves[1])
    if cmfb:
        gm_c, pole_hz = cmfb
        m = net.node()
        net.vccs(m, op, -0.5)
        net.vccs(m, on, -0.5)
        net.conductance(m, None, 1.0)
        if pole_hz:
            net.cap(m, None, 1.0 / (2.0 * math.pi * pole_hz))
        net.vccs(v1p, m, -gm_c)
        net.vccs(v1n, m, -gm_c)
    return (*net.matrices(), (op, on))


#: dB under 0 dB within which a loop gain that comes back up counts as
#: reaching it.  Past the first crossing a lightly damped pole pair can lift
#: the gain back toward unity; the model and SPICE agree on that peak to
#: 0.07-0.45 dB, so a peak at -0.3 dB in the model can read +0.04 dB -- a
#: second crossing, with ~45° left -- in SPICE (issue #274).
_NEAR_CROSSING_DB = 0.5


def _loop_gbw_pm(G, C, b, out, w_t) -> tuple[float | None, float | None]:
    """``(GBW Hz, PM°)`` of an open loop (:func:`_sweep`): its first 0 dB
    crossing, and the worst margin over every crossing and every *return*
    toward 0 dB.

    A return is the stretch where the gain, having fallen more than
    ``_NEAR_CROSSING_DB`` under 0 dB past the first crossing, climbs back
    within it: the model cannot tell such a peak from a crossing, so its
    phase counts as a margin too.  The margin is ``0.0`` with a
    right-half-plane pole.  Either value is ``None`` when the gain never
    reaches 0 dB.
    """
    poles = _poles(G, C)
    crossings = _crossings(G, C, b, out, poles, w_t)
    if not crossings:
        return None, None
    if _has_rhp_pole(poles):
        return crossings[0][0] / (2.0 * math.pi), 0.0
    pms = [p for _w, p in crossings]
    w, mag_db, phase = _sweep(G, C, b, out, poles, w_t)
    import numpy as np
    past = w > crossings[0][0]
    dipped = np.cumsum(past & (mag_db < -_NEAR_CROSSING_DB)) > 0
    near = dipped & (mag_db >= -_NEAR_CROSSING_DB) & (mag_db < 0.0)
    pms += [float(180.0 + p) for p in phase[near]]
    return crossings[0][0] / (2.0 * math.pi), min(pms)


def two_stage_loop(
    gm1: float, gm2: float, cc_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0, g1: float = 0.0, g2: float = 0.0,
    buffer: tuple[float, float, float] | None = None,
    r_ohm: tuple[float, ...] = (0.0,),
    cmfb: tuple[float, float | None] | None = None,
) -> tuple[float | None, float | None]:
    """``(GBW Hz, phase margin °)`` of a two-stage Miller amp, full model.

    Read off the half circuit's open loop, as :func:`phase_margin_rnmc_deg`
    does for RNMC.  It sees what the closed forms
    (``gm1/(2π·Cc)``, :func:`phase_margin_two_stage_deg`) leave out: the
    output pole pulling the 0 dB crossing down (~14 % at 55°), the series
    resistor's zero cancelling that pole (``R = (Cc+CL)/(gm2·Cc)`` puts it
    there, which measures ~89° and no pull), and an output follower's gain
    and pole (issue #274).

    :param gm1: Input-pair transconductance seen by the loop, in A/V.
    :param gm2: Second-stage signal gm in A/V.
    :param cc_f: Miller cap in F.
    :param cl_f: Load capacitance in F, on the follower's output when
        ``buffer`` is given.
    :param c1_f: Parasitic on the first-stage output in F.
    :param c2_f: Parasitic on the second-stage output in F besides ``CL``.
    :param g1: First-stage output conductance in A/V.
    :param g2: Second-stage output conductance in A/V.
    :param buffer: ``(gm_f, cgs_f, g_f)`` of a source-follower output buffer,
        as for :func:`phase_margin_rnmc_deg`, or ``None``.
    :param r_ohm: Per half, the resistor in series with ``Cc`` in Ω (nulling
        or indirect compensation), ``0.0`` for a bare cap: one entry
        single-ended, ``(p, n)`` fully differential, whose halves can differ
        (:func:`_miller_network`).
    :param cmfb: ``(gm_c, pole_hz)`` of a fully-differential amplifier's
        common-mode feedback: the current in A/V it injects into each
        first-stage node per volt of output common mode, and its pole in Hz
        (``None``: none); ``None`` without a CMFB.  With mismatched halves it
        moves the margin ~6° (issue #274).
    :returns: Either value ``None`` when the gain never reaches 0 dB; the
        margin ``0.0`` with a right-half-plane pole.
    """
    g1, g2 = _floor_g((g1, gm1), (g2, gm2))

    def half(net, drive, r):
        return _two_stage_half(net, drive, gm2, cc_f, cl_f, c1_f, c2_f, g1, g2, buffer, r)
    return _loop_gbw_pm(*_miller_network(half, gm1, r_ohm, cmfb), gm1 / cc_f)


def nested_miller_loop(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0, c3_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
    comp_r: tuple[tuple[float, float], ...] = ((0.0, 0.0),),
    cmfb: tuple[float, float | None] | None = None,
) -> tuple[float | None, float | None]:
    """``(GBW Hz, phase margin °)`` of a nested-Miller three-stage amp, full
    model (see :func:`two_stage_loop`).

    :func:`phase_margin_three_stage_deg` treats the non-dominant poles as
    ``gm2/Cc2`` and ``gm3/CL``; behind a buffer the third stage drives only
    the follower's gate, and the series resistors' zeros move both, so it ran
    15-35° pessimistic in SPICE and could not place the 0 dB crossing
    (issue #274).

    :param gm2: Second-stage (non-inverting) signal gm in A/V.
    :param gm3: Third-stage (inverting) signal gm in A/V.
    :param cc1_f: Outer cap, first-stage output → third-stage output, in F.
    :param cc2_f: Inner cap, second-stage output → third-stage output, in F.
    :param c3_f: Parasitic on the third-stage output in F besides ``CL``.
    :param mirror_pole_hz: Pole of the second stage's current mirror in Hz,
        ``None`` when it has none.
    :param comp_r: Per half, ``(R1, R2)`` in Ω in series with ``Cc1`` and
        ``Cc2``; halves and ``cmfb`` as in :func:`two_stage_loop`.

    Other parameters as for :func:`two_stage_loop`.
    """
    g1, g2, g3 = _floor_g((g1, gm1), (g2, gm2), (g3, gm3))

    def half(net, drive, r):
        return _nmc_half(net, drive, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f, c3_f,
                         g1, g2, g3, mirror_pole_hz, buffer, *r)
    return _loop_gbw_pm(*_miller_network(half, gm1, comp_r, cmfb), gm1 / cc1_f)


def _poles(G, C):
    """Roots of ``det(G + s·C) = 0`` in rad/s: the eigenvalues of ``−C⁻¹G``.

    A node with no capacitance to ground -- the junction of a compensation
    cap and its resistor, with no parasitic given -- makes ``C`` singular: a
    pole at infinity.  A capacitance ``1e-9`` of the largest on every node
    then places it far past everything else instead.
    """
    import numpy as np

    try:
        return np.linalg.eigvals(-np.linalg.solve(C, G))
    except np.linalg.LinAlgError:
        floor = 1e-9 * np.abs(C).max() * np.eye(len(C))
        return np.linalg.eigvals(-np.linalg.solve(C + floor, G))


def _has_rhp_pole(poles) -> bool:
    """``True`` when a pole lies in the right half plane.

    A pole below ``1e-12`` of the fastest one is not resolved: the eigenvalues
    of ``−C⁻¹G`` carry an absolute error of about ``eps·max|p|``, so its sign
    is round-off.  That is the dominant pole of a loop whose output
    conductances are floored (DC gain ~1e27), and it does not count.
    """
    resolved = 1e-12 * max((abs(p) for p in poles), default=0.0)
    return any(p.real > 1e-9 * abs(p) and abs(p) > resolved for p in poles)


def _open_loop_gain(G, C, b, out, w):
    """``A(jω) = v[out]`` at each angular frequency ``w``, signed so ``A(0) > 0``;
    ``out = (p, n)`` reads the difference ``v[p] − v[n]``."""
    import numpy as np

    def read(v):
        return v[..., out[0]] - v[..., out[1]] if isinstance(out, tuple) else v[..., out]
    rhs = np.broadcast_to(b[:, None], (len(w), len(b), 1))
    v = np.linalg.solve(G + 1j * w[:, None, None] * C, rhs)[..., 0]
    return read(v) * np.sign(read(np.linalg.solve(G, b)))


def rnmc_pole_damping(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
    c3_f: float = 0.0,
    comp_r: tuple[float, float] = (0.0, 0.0),
) -> float:
    """Worst damping ``ζ`` over the RNMC loop's non-dominant poles.

    ζ = −Re(p) / \\|p\\|

    — the cosine of a pole's angle from the negative real axis.  Read off
    the poles of the full model (arguments as for
    :func:`phase_margin_rnmc_deg`), so it sees the parasitics, the
    third-stage mirror, an output buffer and series compensation resistors.
    The dominant (smallest) pole is skipped; a real pole counts as ``1``.  The sizer aims for ``0.7``, the
    Butterworth placement (no resonance peak).

    :returns: Negative when any non-dominant pole is in the right half plane.
    """
    G, C, _, _ = _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                               g1, g2, g3, mirror_pole_hz, buffer, c3_f, *comp_r)
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
    c3_f: float = 0.0,
    comp_r: tuple[float, float] = (0.0, 0.0),
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
    :param c3_f: Parasitic from the third-stage output to ground in F,
        besides ``CL`` (the drains on it; behind a buffer, nearly all it
        carries at the inner loop's frequencies).
    :param comp_r: ``(R1, R2)`` in Ω in series with ``Cc1`` and ``Cc2`` — a
        nulling or indirect compensation branch — ``0.0`` for a bare cap.  A
        bare ``Cc2`` feeds the first-stage output forward into the second's,
        a right-half-plane zero near ``gm2/Cc2``; ``R2 ≈ 1/gm`` cancels it,
        which is what damps a buffered RNMC loop's pole pair (issue #266).
        Several times ``1/gm`` in series with ``Cc1`` holds the gain near
        0 dB past the first crossing (issue #269).
    :returns: ``0.0`` when the open loop has a right-half-plane pole (no
        margin to speak of; a pole too slow to resolve does not count, see
        :func:`_has_rhp_pole`); ``None`` when the gain never reaches 0 dB.
    """
    G, C, b, out = _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                                 g1, g2, g3, mirror_pole_hz, buffer, c3_f, *comp_r)
    poles = _poles(G, C)
    if _has_rhp_pole(poles):
        return 0.0
    crossings = _crossings(G, C, b, out, poles, gm1 / cc1_f)
    if not crossings:
        return None
    return min(pm for _w, pm in crossings)


def unity_gain_bw_rnmc_hz(
    gm1: float, gm2: float, gm3: float,
    cc1_f: float, cc2_f: float, cl_f: float,
    *, c1_f: float = 0.0, c2_f: float = 0.0,
    g1: float = 0.0, g2: float = 0.0, g3: float = 0.0,
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
    c3_f: float = 0.0,
    comp_r: tuple[float, float] = (0.0, 0.0),
) -> float | None:
    """GBW in Hz of a reversed-nested-Miller amp: its first 0 dB crossing.

    Read off the same full model as :func:`phase_margin_rnmc_deg` (arguments
    as there), so the pole pair near crossover and an output buffer's gain
    below 1 both pull it under ``gm1/(2π·Cc1)``, by ~20 % on a buffered
    design (issue #274).  The first crossing is the one the AC bench reports.

    :returns: ``None`` when the gain never reaches 0 dB.
    """
    G, C, b, out = _rnmc_network(gm1, gm2, gm3, cc1_f, cc2_f, cl_f, c1_f, c2_f,
                                 g1, g2, g3, mirror_pole_hz, buffer, c3_f, *comp_r)
    crossings = _crossings(G, C, b, out, _poles(G, C), gm1 / cc1_f)
    return crossings[0][0] / (2.0 * math.pi) if crossings else None


def _sweep(G, C, b, out, poles, w_t):
    """``(ω, |A| dB, ∠A°)`` of an open loop on a log sweep spanning two
    decades past every pole and the nominal crossover ``w_t`` (rad/s)."""
    import numpy as np

    corners = [abs(p) for p in poles if abs(p) > 0] + [w_t]
    w = np.logspace(math.log10(min(corners)) - 2, math.log10(max(corners)) + 2, 4000)
    a = _open_loop_gain(G, C, b, out, w)
    return w, 20.0 * np.log10(np.abs(a)), np.degrees(np.unwrap(np.angle(a)))


def _crossings(G, C, b, out, poles, w_t) -> list[tuple[float, float]]:
    """``(ω, PM°)`` at every 0 dB crossing of an open loop (:func:`_sweep`),
    in order.

    Each is bracketed on the sweep and refined by false position in
    ``(log ω, dB)``, so it does not depend on the sweep's grid: a pole far
    past everything else (a node with no capacitance, :func:`_poles`)
    stretches the sweep and, read off the grid alone, moved the crossing by
    ~1 ppm.  The phase is read there too, unwrapped from the bracket's start.
    """
    import numpy as np

    def gain(lw):
        return _open_loop_gain(G, C, b, out, np.array([10.0 ** lw]))[0]

    w, mag_db, phase = _sweep(G, C, b, out, poles, w_t)
    found = []
    for i in np.nonzero(np.diff(np.sign(mag_db)))[0]:
        x0, x1 = math.log10(w[i]), math.log10(w[i + 1])
        d0, d1 = mag_db[i], mag_db[i + 1]
        for _ in range(3):
            x = x0 + d0 / (d0 - d1) * (x1 - x0)
            d = 20.0 * math.log10(abs(gain(x)))
            if d == 0.0:
                break
            if (d > 0.0) == (d0 > 0.0):
                x0, d0 = x, d
            else:
                x1, d1 = x, d
        lw = x if d == 0.0 else x0 + d0 / (d0 - d1) * (x1 - x0)
        turn = np.angle(gain(lw) / gain(math.log10(w[i])), deg=True)
        found.append((10.0 ** lw, float(180.0 + phase[i] + turn)))
    return found


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
