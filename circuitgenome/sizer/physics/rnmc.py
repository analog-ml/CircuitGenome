"""Reversed-nested-Miller (RNMC) compensation sizing (PR #RNMCPR).

RNMC puts both compensation caps on the first-stage output: ``Cc1`` to the
amplifier output and ``Cc2`` to the second-stage output.  That makes the two
non-dominant poles a *pair* (see the RNMC section of
:mod:`~circuitgenome.sizer.physics.equations`), not two separate real poles,
and the pair is only in the left half plane while

    gm3 < gm2 · (1 + CL/Cc1)            (gm3 < 5·gm2 at Cc1 = CL/4)

The generic three-stage requirements push gm3 to its ceiling and leave gm2 at
whatever the second stage's minimum width gives, so an RNMC amplifier could be
sized straight into the right half plane.  :func:`design_rnmc` picks
``(gm2, Cc2, gm3)`` so the pair is damped to ``ζ ≈ 0.7`` and the full-model
phase margin (:func:`~.equations.phase_margin_rnmc_deg`) meets the spec,
trying the knobs in this order:

1. **raise gm2** — the second stage's signal device gets a larger gm
   requirement (up to its weak-inversion ceiling);
2. **raise Cc2** — in steps up to ``Cc1``, which damps the pair further;
3. **cap gm3** — only as a last resort, since it costs DC gain.

The Level-1 sizer runs it inside :func:`~.preprocess.compute_requirements` on
estimated values (its geometry delivers what it is asked for); the gm/Id
pipeline runs it after geometry (:mod:`~circuitgenome.sizer.gmid.rnmc_refine`),
because its DC-bias repair can move the input pair's gm far from the GBW
requirement and only the sized circuit shows the third stage's gate
capacitance.  A gm3 cap lands on the third stage's *input* device only, so a
non-inverting stage's mirror keeps its sizing (and the output its swing).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import equations as eq
from .device_model import SIGNAL

#: ``compensation_scheme`` value (topology config) that selects this module.
RNMC = "reversed_nested_miller"

#: Target damping of the non-dominant pole pair ("roughly critical").
ZETA_TARGET = 0.7

#: Lowest damping accepted when ``ZETA_TARGET`` and the PM spec cannot both
#: be met: the pair still settles (≈16 % overshoot, ~1 dB peaking).
ZETA_FLOOR = 0.5

#: Cc2 multiples of its starting value tried in order (capped at Cc1).
_CC2_STEPS = (1.0, 1.5, 2.0, 3.0, 4.0)
#: gm3 is lowered geometrically by this factor per step, down to its floor.
_GM3_STEP = 0.85
#: At most this many gm3 steps (0.85^20 ≈ 4 % of the starting gm3).
_GM3_MAX_STEPS = 20
#: gm2 is raised geometrically by this factor per step, up to its ceiling.
_GM2_STEP = 1.15


@dataclass(frozen=True)
class RnmcDesign:
    """One RNMC operating choice and how it scores.

    :param gm2: second-stage signal gm in A/V.
    :param cc2_f: inner cap (first-stage output → second-stage output) in F.
    :param gm3: third-stage gm in A/V.
    :param zeta: damping of the non-dominant pole pair (negative = RHP).
    :param pm_deg: full-model phase margin in degrees (worst over all 0 dB
        crossings, ``0.0`` when the open loop has RHP poles), or ``None``.
    :param met: ``True`` when ``zeta ≥ ZETA_TARGET`` and the PM target holds.
    """
    gm2: float
    cc2_f: float
    gm3: float
    zeta: float
    pm_deg: float | None
    met: bool


def design_rnmc(
    gm1: float, gm2: float, gm3: float, *,
    cc1_f: float, cc2_f: float, cl_f: float,
    gm2_max: float, gm3_min: float = 0.0,
    pm_min_deg: float | None = None,
    c1_f: float | Callable[[float], float] = 0.0,
    c2_f: float | Callable[[float], float] = 0.0,
    g: tuple[float, float, float] = (0.0, 0.0, 0.0),
    mirror_pole_hz: float | None = None,
    buffer: tuple[float, float, float] | None = None,
) -> RnmcDesign:
    """Choose ``(gm2, Cc2, gm3)`` for a damped, phase-margin-meeting RNMC loop.

    Starts from the given operating point and never lowers ``gm2`` or
    ``Cc2``, nor raises ``gm3``.  For each ``(Cc2, gm3)`` in preference order
    (all Cc2 steps at the starting gm3 first, then each lower gm3), ``gm2``
    starts at the closed-form damping floor (:func:`~.equations.rnmc_min_gm2`;
    at its current value behind an output ``buffer``, where that formula's
    load does not apply) and rises until the full-model damping
    (:func:`~.equations.rnmc_pole_damping`) reaches ``ZETA_TARGET`` and the
    full-model PM meets ``pm_min_deg`` (or is simply positive when there is
    no PM spec).  The first choice meeting both wins.

    ``c1_f``/``c2_f`` are the parasitics on the first/second-stage outputs.
    Each is a function of the gm of the device whose gate dominates it (gm2
    widens the second stage's input gate on the first-stage output, gm3 the
    third stage's on the second-stage output), or a float: ``c1_f`` then
    stays fixed and ``c2_f`` — its value at the *starting* gm3 — scales in
    proportion to gm3.  When nothing meets both targets (``met=False``) the
    phase-margin spec, which SPICE verifies, comes before the damping
    heuristic, among choices damped to at least ``ZETA_FLOOR``: the
    best-damped one that meets the PM target, else the one with the highest
    PM; failing even ``ZETA_FLOOR``, the best-damped one.

    :param gm1: loop transconductance of the first stage in A/V.
    :param gm2_max: second-stage gm ceiling in A/V.
    :param gm3_min: lowest gm3 the third stage can be sized to in A/V.
    :param g: stage output conductances ``(g1, g2, g3)`` in A/V.
    :param buffer: ``(gm, cgs, g_out)`` of a source-follower output buffer
        between the third stage and ``CL`` (see
        :func:`~.equations.phase_margin_rnmc_deg`), or ``None``.
    """
    pm_target = pm_min_deg if pm_min_deg is not None else 0.0

    def c1_at(gm2_: float) -> float:
        return c1_f(gm2_) if callable(c1_f) else c1_f

    def c2_at(gm3_: float) -> float:
        if callable(c2_f):
            return c2_f(gm3_)
        return c2_f * gm3_ / gm3 if gm3 > 0 else c2_f

    def score(gm2_: float, cc2_: float, gm3_: float) -> RnmcDesign:
        kw = dict(c1_f=c1_at(gm2_), c2_f=c2_at(gm3_), g1=g[0], g2=g[1],
                  g3=g[2], mirror_pole_hz=mirror_pole_hz, buffer=buffer)
        zeta = eq.rnmc_pole_damping(gm1, gm2_, gm3_, cc1_f, cc2_, cl_f, **kw)
        pm = eq.phase_margin_rnmc_deg(gm1, gm2_, gm3_, cc1_f, cc2_, cl_f, **kw)
        met = zeta >= ZETA_TARGET - 1e-9 and pm is not None and pm >= pm_target
        return RnmcDesign(gm2_, cc2_, gm3_, zeta, pm, met)

    start = score(gm2, cc2_f, gm3)
    if start.met:
        return start

    gm3_ladder = [gm3]
    while (len(gm3_ladder) <= _GM3_MAX_STEPS
           and gm3_ladder[-1] * _GM3_STEP >= gm3_min):
        gm3_ladder.append(gm3_ladder[-1] * _GM3_STEP)
    if gm3_ladder[-1] > gm3_min > 0 and len(gm3_ladder) <= _GM3_MAX_STEPS:
        gm3_ladder.append(gm3_min)
    cc2_ladder = []
    for k in _CC2_STEPS:
        c = min(cc2_f * k, cc1_f)
        if c not in cc2_ladder:
            cc2_ladder.append(c)

    tried = [start]
    for gm3_ in gm3_ladder:
        for cc2_ in cc2_ladder:
            floor = (0.0 if buffer else
                     eq.rnmc_min_gm2(gm3_, cc1_f, cc2_, cl_f, c2_at(gm3_),
                                     ZETA_TARGET))
            gm2_ = max(gm2, floor)
            if gm2_ > gm2_max:
                tried.append(score(gm2_max, cc2_, gm3_))
                continue
            while True:
                d = score(gm2_, cc2_, gm3_)
                tried.append(d)
                if d.met:
                    return d
                if gm2_ >= gm2_max:
                    break
                gm2_ = min(gm2_ * _GM2_STEP, gm2_max)

    usable = [d for d in tried if d.zeta >= ZETA_FLOOR and d.pm_deg is not None]
    meets_pm = [d for d in usable if d.pm_deg >= pm_target]
    if meets_pm:
        return max(meets_pm, key=lambda d: d.zeta)
    if usable:
        return max(usable, key=lambda d: d.pm_deg)
    return max(tried, key=lambda d: d.zeta)


def signal_cgs_estimate(model, dtype: str, gm: float, ids: float, tech) -> float:
    """Gate capacitance in F of a signal device sized for ``gm`` at ``ids``.

    Used before geometry exists: the gm/Id model inverts its LUT for the
    width (:meth:`~.device_model.GmIdModel.geometry_for`); the Level-1 model
    solves the square law ``W = gm²·L / (2·µCox·Id)`` at minimum length.
    Either width is clamped to the technology's range.
    """
    if gm <= 0 or ids <= 0:
        return 0.0
    if hasattr(model, "geometry_for"):
        geo = model.geometry_for(dtype, ids, SIGNAL, gm)
        w_um, l_um = geo.w_um, geo.l_um
    else:
        p = tech.nmos if dtype == "nmos" else tech.pmos
        l_um = tech.length.min
        w_um = gm * gm * l_um / (2.0 * p.mu_cox * ids)
    w_um = min(max(w_um, tech.width.min), tech.width.max)
    return model.cgs(dtype, w_um, l_um)


def design_warnings(d: RnmcDesign, gm3_0: float,
                    pm_min_deg: float | None) -> list[str]:
    """Plain-language advisories for what :func:`design_rnmc` had to change."""
    out: list[str] = []
    if d.gm3 < gm3_0 * (1 - 1e-9):
        out.append(
            f"RNMC compensation: third-stage gm capped at {d.gm3 * 1e3:.3g} mS "
            f"— the second stage (gm {d.gm2 * 1e3:.3g} mS) cannot match more, "
            f"and gm3 ≥ gm2·(1+CL/Cc1) puts the inner pole pair in the right "
            f"half plane; DC gain is lower as a result.")
    if not d.met:
        pm = "n/a" if d.pm_deg is None else f"{d.pm_deg:.1f}°"
        target = (f"PM ≥ {pm_min_deg:.0f}°" if pm_min_deg is not None
                  else "a positive PM")
        out.append(
            f"RNMC compensation: no gm2/Cc2/gm3 choice reaches ζ ≥ "
            f"{ZETA_TARGET} and {target} at this bias (best: ζ = "
            f"{d.zeta:.2f}, PM {pm}) — raise the second-stage current or the "
            f"compensation cap Cc1.")
    return out

