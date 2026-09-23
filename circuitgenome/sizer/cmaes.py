"""CMA-ES refinement of an initial sizing against ngspice measurements.

The analytical/gm-Id sizer supplies a physically meaningful starting point.
This module then searches log-scaled width multipliers (one variable per
matched/mirrored group) and, optionally, the compensation capacitor.  Keeping
lengths fixed makes the first refinement pass compact and preserves the
initial intrinsic-gain policy.
"""
from __future__ import annotations

import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import Callable, Sequence

import numpy as np

from .models import SizingResult, SizingSpec, TechParams
from .verify import check_bias_soundness, ngspice_available, simulate_metrics


_SPECS: dict[str, tuple[str, bool]] = {
    "gain_db": ("gain_min_db", True),
    "gbw_hz": ("gbw_min_hz", True),
    "phase_margin_deg": ("phase_margin_min_deg", True),
    "slew_rate_vps": ("slew_rate_min_vps", True),
    "power_w": ("power_max_w", False),
    "cmrr_db": ("cmrr_min_db", True),
    "psrr_db": ("psrr_min_db", True),
    "output_swing_max_v": ("output_swing_max_v", True),
    "output_swing_min_v": ("output_swing_min_v", False),
}


@dataclass(frozen=True)
class CmaEsConfig:
    """Controls the SPICE refinement.

    Width variables are logarithmic multipliers around the initial sizing.  The
    defaults spend at most 1,281 SPICE evaluations (initial point + 80×16) in a
    local ``0.6×`` to ``1.7×`` interval while keeping every proposal positive.
    """
    generations: int = 80
    population_size: int | None = 16
    sigma: float = 0.15
    min_scale: float = 0.6
    max_scale: float = 1.7
    optimize_compensation: bool = True
    objective: str = "fom"
    area_weight: float = 0.02
    workers: int = 1
    seed: int | None = None
    corner: str | None = None
    check_bias: bool = True
    max_slew_current_ratio: float = 50.0
    patience: int = 30
    tolerance: float = 1e-4


@dataclass
class CmaEsRefinement:
    """Best sizing and optimization trace returned by :func:`refine_cmaes`."""
    sizing: SizingResult
    spice_metrics: dict[str, float | None]
    normalized_margins: dict[str, float]
    figures_of_merit: dict[str, float]
    score: float
    evaluations: int
    generations: int
    groups: tuple[tuple[str, ...], ...]
    history: list[float] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)


@dataclass
class CmaEsMinimum:
    """Result of the reusable numerical CMA-ES minimizer."""
    x: np.ndarray
    value: float
    evaluations: int
    generations: int
    history: list[float]


def minimize_cmaes(
    objective: Callable[[np.ndarray], float],
    x0: Sequence[float],
    config: CmaEsConfig = CmaEsConfig(),
    bounds: tuple[Sequence[float], Sequence[float]] | None = None,
) -> CmaEsMinimum:
    """Minimize a black-box function with full-covariance CMA-ES.

    Population members may be evaluated concurrently because ngspice runs in
    separate subprocesses.  Bounds are enforced by clipping in the transformed
    search space; callers should use log variables for positive quantities.
    """
    mean = np.asarray(x0, dtype=float).copy()
    n = mean.size
    if n == 0:
        value = float(objective(mean))
        return CmaEsMinimum(mean, value, 1, 0, [value])
    if config.generations < 0 or config.sigma <= 0:
        raise ValueError("generations must be non-negative and sigma must be positive")
    if bounds is None:
        lower = np.full(n, -np.inf)
        upper = np.full(n, np.inf)
    else:
        lower, upper = (np.asarray(v, dtype=float) for v in bounds)
        if lower.shape != mean.shape or upper.shape != mean.shape:
            raise ValueError("CMA-ES bounds must match x0")
        if np.any(lower > upper):
            raise ValueError("CMA-ES lower bounds exceed upper bounds")
    mean = np.clip(mean, lower, upper)

    lam = config.population_size or (4 + int(3 * math.log(n)))
    if lam < 2:
        raise ValueError("population_size must be at least 2")
    mu = lam // 2
    weights = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
    weights /= weights.sum()
    mueff = 1.0 / np.sum(weights ** 2)
    cc = (4.0 + mueff / n) / (n + 4.0 + 2.0 * mueff / n)
    cs = (mueff + 2.0) / (n + mueff + 5.0)
    c1 = 2.0 / ((n + 1.3) ** 2 + mueff)
    cmu = min(1.0 - c1,
              2.0 * (mueff - 2.0 + 1.0 / mueff) / ((n + 2.0) ** 2 + mueff))
    damps = 1.0 + 2.0 * max(0.0, math.sqrt((mueff - 1.0) / (n + 1.0)) - 1.0) + cs
    chi_n = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))

    rng = np.random.default_rng(config.seed)
    covariance = np.eye(n)
    pc = np.zeros(n)
    ps = np.zeros(n)
    sigma = float(config.sigma)
    best_x = mean.copy()
    best = float(objective(best_x))
    evaluations = 1
    history = [best]
    stale = 0

    for generation in range(1, config.generations + 1):
        eigvals, basis = np.linalg.eigh((covariance + covariance.T) * 0.5)
        scales = np.sqrt(np.maximum(eigvals, 1e-20))
        transform = basis @ np.diag(scales)
        z = rng.standard_normal((lam, n))
        y = z @ transform.T
        population = np.clip(mean + sigma * y, lower, upper)
        if config.workers > 1:
            with ThreadPoolExecutor(max_workers=config.workers) as pool:
                values = np.asarray(list(pool.map(objective, population)), dtype=float)
        else:
            values = np.asarray([objective(x) for x in population], dtype=float)
        evaluations += lam
        values = np.where(np.isfinite(values), values, 1e30)
        order = np.argsort(values)
        selected = population[order[:mu]]
        old_mean = mean.copy()
        mean = weights @ selected
        y_w = (mean - old_mean) / sigma

        inv_sqrt = basis @ np.diag(1.0 / scales) @ basis.T
        ps = ((1.0 - cs) * ps
              + math.sqrt(cs * (2.0 - cs) * mueff) * (inv_sqrt @ y_w))
        norm_ps = float(np.linalg.norm(ps))
        hsig_limit = (1.4 + 2.0 / (n + 1.0)) * chi_n
        hsig = float(norm_ps / math.sqrt(1.0 - (1.0 - cs) ** (2 * generation))
                     < hsig_limit)
        pc = ((1.0 - cc) * pc
              + hsig * math.sqrt(cc * (2.0 - cc) * mueff) * y_w)
        steps = (selected - old_mean) / sigma
        rank_mu = sum(w * np.outer(step, step)
                      for w, step in zip(weights, steps))
        covariance = (
            (1.0 - c1 - cmu) * covariance
            + c1 * (np.outer(pc, pc) + (1.0 - hsig) * cc * (2.0 - cc) * covariance)
            + cmu * rank_mu
        )
        sigma *= math.exp((cs / damps) * (norm_ps / chi_n - 1.0))

        generation_best = float(values[order[0]])
        if generation_best < best - config.tolerance:
            best = generation_best
            best_x = population[order[0]].copy()
            stale = 0
        else:
            stale += 1
        history.append(best)
        if config.patience > 0 and stale >= config.patience:
            return CmaEsMinimum(best_x, best, evaluations, generation, history)
    return CmaEsMinimum(best_x, best, evaluations, config.generations, history)


def _mos_meta(netlist_text: str) -> dict[str, tuple[str, str, str]]:
    """Return ``ref -> (type, drain, gate)`` from generic flat MOS lines."""
    out: dict[str, tuple[str, str, str]] = {}
    for line in netlist_text.splitlines():
        tok = line.split()
        if len(tok) >= 6 and tok[0].lower().startswith("m") \
                and tok[5].lower() in ("nmos", "pmos"):
            out[tok[0]] = (tok[5].lower(), tok[1], tok[2])
    return out


def _slot(ref: str) -> str | None:
    for name in ("input_pair", "tail_current", "output_stage", "load"):
        if ref.endswith("_" + name):
            return name
    return None


def default_width_groups(netlist_text: str, initial: SizingResult,
                         ) -> tuple[tuple[str, ...], ...]:
    """Build conservative matched/mirror groups for width scaling.

    Differential-pair/load/tail devices with the same type, current and role
    are kept together.  A diode-connected reference also ties every same-type
    device on its gate net, preserving current-mirror width ratios.  Remaining
    transistors each receive their own variable.
    """
    refs = sorted(initial.transistors)
    parent = {r: r for r in refs}

    def find(r: str) -> str:
        while parent[r] != r:
            parent[r] = parent[parent[r]]
            r = parent[r]
        return r

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    meta = _mos_meta(netlist_text)
    # Explicit current mirrors: one diode on (type, gate) anchors the group.
    by_gate: dict[tuple[str, str], list[str]] = {}
    diode_keys: set[tuple[str, str]] = set()
    for ref, (dtype, drain, gate) in meta.items():
        if ref not in parent:
            continue
        key = (dtype, gate)
        by_gate.setdefault(key, []).append(ref)
        if drain == gate:
            diode_keys.add(key)
    for key in diode_keys:
        members = by_gate[key]
        for ref in members[1:]:
            union(members[0], ref)

    # Structural matched groups; output-stage signal devices remain separate.
    matched: dict[tuple, list[str]] = {}
    for ref in refs:
        slot = _slot(ref)
        if slot not in ("input_pair", "load", "tail_current") or ref not in meta:
            continue
        sizing = initial.transistors[ref]
        intent = initial.transistor_intents.get(ref)
        role = getattr(intent, "role", None)
        key = (slot, meta[ref][0], round(sizing.ids_a, 18), role)
        matched.setdefault(key, []).append(ref)
    for members in matched.values():
        for ref in members[1:]:
            union(members[0], ref)

    grouped: dict[str, list[str]] = {}
    for ref in refs:
        grouped.setdefault(find(ref), []).append(ref)
    return tuple(tuple(sorted(group)) for group in sorted(grouped.values(), key=min))


def _normalized_margins(metrics: dict, spec: SizingSpec) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, (attr, is_min) in _SPECS.items():
        target = getattr(spec, attr)
        measured = metrics.get(key)
        if target is None or measured is None:
            continue
        delta = measured - target if is_min else target - measured
        out[key] = delta / abs(target) if target else delta
    return out


def _margin_factors(metrics: dict, spec: SizingSpec) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, (attr, is_min) in _SPECS.items():
        target = getattr(spec, attr)
        measured = metrics.get(key)
        if target is None or measured is None:
            continue
        if is_min:
            out[key] = measured / target if target else float("inf")
        else:
            out[key] = target / measured if measured else float("inf")
    return out


def figures_of_merit(metrics: dict, spec: SizingSpec) -> dict[str, float]:
    """Return the standard op-amp small- and large-signal current FoMs.

    ``FoM_S = GBW·CL/Iq`` and ``FoM_L = SR·CL/Iq``, with
    ``Iq = power/(VDD-VSS)``.  ``FoM_S`` is returned in 1/V and ``FoM_L`` is
    dimensionless.  Multiplying either SI value by ``1e3`` gives the commonly
    quoted numerical scale (``MHz·pF/mA`` or ``V/µs·pF/mA`` respectively).
    """
    power = metrics.get("power_w")
    span = spec.vdd - spec.vss
    if power is None or power <= 0 or span <= 0:
        return {}
    iq = power / span
    out = {"iq_a": iq}
    gbw = metrics.get("gbw_hz")
    slew = metrics.get("slew_rate_vps")
    if gbw is not None and gbw > 0:
        out["fom_s_per_v"] = gbw * spec.cl / iq
    if slew is not None and slew > 0:
        out["fom_l"] = slew * spec.cl / iq
    return out


def metric_sanity_problem(
    metrics: dict,
    sizing: SizingResult,
    spec: SizingSpec,
    *,
    max_slew_current_ratio: float = 50.0,
) -> str | None:
    """Return why a measured candidate is physically implausible, if any.

    This is deliberately loose: it rejects simulator/extraction artifacts, not
    merely poor designs.  In particular ``SR·CL`` is the measured load current;
    allowing fifty times the sum of planned quiescent device currents leaves
    ample room for Class-AB drive while rejecting picosecond feedthrough spikes.
    """
    numeric = {k: v for k, v in metrics.items() if k != "notes" and v is not None}
    for key, value in numeric.items():
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return f"{key} is not finite"
    power = metrics.get("power_w")
    if power is not None and power <= 0:
        return "quiescent power is not positive"
    gain = metrics.get("gain_db")
    if gain is not None and gain <= 0:
        return "open-loop gain is non-positive"
    gbw = metrics.get("gbw_hz")
    if gbw is not None and gbw <= 0:
        return "GBW is not positive"
    pm = metrics.get("phase_margin_deg")
    if pm is not None and not (0.0 < pm <= 180.0):
        return "phase margin lies outside (0, 180] degrees"
    hi = metrics.get("output_swing_max_v")
    lo = metrics.get("output_swing_min_v")
    rail_tol = 0.05 * max(spec.vdd - spec.vss, 1e-12)
    if hi is not None and not (spec.vss - rail_tol <= hi <= spec.vdd + rail_tol):
        return "high output-swing measurement lies outside the supply rails"
    if lo is not None and not (spec.vss - rail_tol <= lo <= spec.vdd + rail_tol):
        return "low output-swing measurement lies outside the supply rails"
    if hi is not None and lo is not None and hi <= lo:
        return "output-swing limits are reversed"
    slew = metrics.get("slew_rate_vps")
    if slew is not None:
        if slew <= 0:
            return "slew rate is not positive"
        planned_i = sum(abs(s.ids_a) for s in sizing.transistors.values())
        if (spec.cl > 0 and planned_i > 0
                and slew * spec.cl > max_slew_current_ratio * planned_i):
            return ("slew-rate extraction implies an implausible load current "
                    f"({slew * spec.cl:.3g} A from SR·CL)")
    return None


def refine_cmaes(
    netlist_text: str,
    initial: SizingResult,
    tech: TechParams,
    spec: SizingSpec,
    config: CmaEsConfig = CmaEsConfig(),
    *,
    groups: Sequence[Sequence[str]] | None = None,
    simulator: Callable[..., dict] = simulate_metrics,
) -> CmaEsRefinement:
    """Refine ``initial`` sizing using CMA-ES and ngspice metrics.

    The default score is feasibility-first: missing constrained measurements
    and normalized spec violations dominate; once feasible, CMA-ES maximizes
    ``GBW·CL/Iq`` with a small total-area regularizer.
    ``simulator`` is injectable for deterministic tests or a custom bench.
    """
    if simulator is simulate_metrics and not ngspice_available():
        raise RuntimeError("CMA-ES SPICE refinement requires ngspice on PATH")
    if not initial.transistors:
        raise ValueError("CMA-ES refinement requires a non-empty initial sizing")
    if config.min_scale <= 0 or config.max_scale < config.min_scale:
        raise ValueError("invalid positive width scale bounds")
    if config.objective not in ("fom", "worst_margin"):
        raise ValueError("objective must be 'fom' or 'worst_margin'")
    if config.max_slew_current_ratio <= 0:
        raise ValueError("max_slew_current_ratio must be positive")

    width_groups = tuple(tuple(g) for g in (
        groups if groups is not None else default_width_groups(netlist_text, initial)))
    flat = [r for group in width_groups for r in group]
    if len(flat) != len(set(flat)) or set(flat) != set(initial.transistors):
        raise ValueError("width groups must partition all sized transistor refs exactly once")
    has_cc = bool(config.optimize_compensation and initial.cc_pf)
    nvars = len(width_groups) + int(has_cc)
    lower = np.full(nvars, math.log(config.min_scale))
    upper = np.full(nvars, math.log(config.max_scale))
    base_area = sum(s.w_um * s.l_um for s in initial.transistors.values()) or 1.0
    cache: dict[tuple[float, ...], tuple[float, SizingResult, dict, dict]] = {}
    rejections: Counter[str] = Counter()
    rejection_lock = Lock()

    def reject(reason: str) -> None:
        with rejection_lock:
            rejections[reason] += 1

    def candidate(x: np.ndarray) -> SizingResult:
        transistors = dict(initial.transistors)
        for value, group in zip(x, width_groups):
            scale = math.exp(float(value))
            for ref in group:
                old = initial.transistors[ref]
                width = tech.width.snap(old.w_um * scale)
                transistors[ref] = replace(old, w_um=width)
        cc_pf = initial.cc_pf
        if has_cc:
            cc_pf = tech.cap.snap(initial.cc_pf * math.exp(float(x[-1])))
        return replace(initial, transistors=transistors, cc_pf=cc_pf)

    def evaluate(x: np.ndarray) -> float:
        key = tuple(np.round(x, 10))
        if key in cache:
            return cache[key][0]
        sized = candidate(x)
        if config.check_bias and simulator is simulate_metrics:
            sound, _reason = check_bias_soundness(
                netlist_text, sized, tech, spec)
            if not sound:
                reject("dc_bias")
                cache[key] = (1e12, sized, {}, {})
                return 1e12
        try:
            metrics = simulator(netlist_text, sized, tech, spec, corner=config.corner)
        except Exception:
            reject("simulation_error")
            metrics = {}
        sanity = metric_sanity_problem(
            metrics, sized, spec,
            max_slew_current_ratio=config.max_slew_current_ratio)
        if sanity is not None:
            reject("metric_sanity")
            metrics = dict(metrics)
            metrics.setdefault("notes", []).append(
                "CMA-ES candidate rejected: " + sanity)
            cache[key] = (1e12, sized, metrics, {})
            return 1e12
        margins = _normalized_margins(metrics, spec)
        required = sum(getattr(spec, attr) is not None for attr, _ in _SPECS.values())
        missing = required - len(margins)
        violations = sum(max(-m, 0.0) ** 2 for m in margins.values())
        if missing:
            reject("missing_constrained_metric")
        elif violations:
            reject("spec_violation")
        area_ratio = sum(s.w_um * s.l_um for s in sized.transistors.values()) / base_area
        if missing or violations:
            score = 1e6 * missing + 1e3 * (1.0 + violations) + config.area_weight * area_ratio
        elif config.objective == "fom":
            foms = figures_of_merit(metrics, spec)
            fom_s = foms.get("fom_s_per_v")
            # When GBW/power is unavailable (e.g. a custom reduced bench),
            # retain a useful area-only objective instead of inventing a FoM.
            score = (-math.log(max(fom_s, 1e-30)) if fom_s is not None else 0.0)
            score += config.area_weight * area_ratio
        elif margins:
            score = -min(margins.values()) + config.area_weight * area_ratio
        else:
            score = config.area_weight * area_ratio
        cache[key] = (float(score), sized, metrics, margins)
        return float(score)

    minimum = minimize_cmaes(
        evaluate, np.zeros(nvars), config, bounds=(lower, upper))
    key = tuple(np.round(minimum.x, 10))
    if key not in cache:
        evaluate(minimum.x)
    score, best_sizing, metrics, margins = cache[key]
    numeric_metrics = {k: v for k, v in metrics.items()
                       if k != "notes" and isinstance(v, (int, float))}
    warnings = list(best_sizing.warnings)
    warnings.append(
        f"CMA-ES ngspice refinement: {minimum.evaluations} evaluations, "
        f"{minimum.generations} generations; analytical Vgs/Vdsat fields retain "
        f"their initial-sizing values.")
    best_sizing = replace(
        best_sizing,
        metrics=numeric_metrics,
        margins=_margin_factors(metrics, spec),
        warnings=warnings,
    )
    return CmaEsRefinement(
        sizing=best_sizing,
        spice_metrics=metrics,
        normalized_margins=margins,
        figures_of_merit=figures_of_merit(metrics, spec),
        score=score,
        evaluations=minimum.evaluations,
        generations=minimum.generations,
        groups=width_groups,
        history=minimum.history,
        rejections=dict(rejections),
    )
