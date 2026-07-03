"""Designer module (Layer 4): synthesize → size → SPICE-verify → export.

:func:`design` closes the loop the other layers leave manual: enumerate every
valid circuit for the chosen template(s) (synthesizer), size each with the
gm/Id sizer (recognizer + sizer), keep the circuits whose **ngspice-measured**
metrics meet the target spec, export the survivors as sized flat SPICE
netlists, and report statistics with the best design points.

Acceptance gates on measured metrics only: a circuit is rejected when its
sizing fails, its DC bias is infeasible (analytical or SPICE-grounded), or any
spec ngspice *did* measure is missed.  A measurement the rig could not extract
for a given circuit (e.g. swing/slew on fully-differential topologies) never
disqualifies it — the affected spec fields are surfaced in the report's
``unverified_specs`` instead.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from functools import partial
from itertools import islice
from pathlib import Path
from typing import Callable, Iterable, Iterator

from ..recognizer import assign_slots, parse, recognize
from ..sizer import SizingSpec, TechParams, load_spec, load_tech, size_circuit
from ..sizer.shared.spice_sim import (
    check_bias_soundness,
    ngspice_available,
    simulate_metrics,
    sized_netlist,
)
from ..synthesizer import enumerate_circuits, to_flat_spice
from ..synthesizer.loader import load_modules, load_topologies
from ..synthesizer.models import TopologyTemplate
from .models import DesignReport, DesignSolution, TemplateStats

# Metric key → (SizingSpec attribute, is_min_spec) for every metric the ngspice
# rig measures (mirrors the CLI's spice_keys).  A constrained spec whose
# measurement comes back None for an accepted circuit is reported in
# DesignReport.unverified_specs rather than failing the circuit.
_MEASURED_SPECS: dict[str, tuple[str, bool]] = {
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

# criterion name → (metric key, pick the max?) for the best-points table.
_BEST_CRITERIA: dict[str, tuple[str, bool]] = {
    "highest_gain": ("gain_db", True),
    "highest_gbw": ("gbw_hz", True),
    "highest_phase_margin": ("phase_margin_deg", True),
    "lowest_power": ("power_w", False),
}

_PROGRESS_EVERY = 25


def _margins(sim: dict[str, float | None], spec: SizingSpec) -> dict[str, float]:
    """Normalized margin per constrained spec that ngspice measured.

    ``(meas − min)/min`` for min-specs, ``(max − meas)/max`` for max-specs, so
    margins are comparable across metrics; a constrained metric whose
    measurement is ``None`` is skipped (unverified, not failed).
    """
    out: dict[str, float] = {}
    for key, (attr, is_min) in _MEASURED_SPECS.items():
        target = getattr(spec, attr)
        meas = sim.get(key)
        if target is None or meas is None:
            continue
        raw = (meas - target) if is_min else (target - meas)
        out[key] = raw / abs(target) if target else raw
    return out


@dataclass
class _Outcome:
    """Picklable per-candidate result returned by the worker."""
    index: int
    variants: dict[str, str]
    stage: str  # "accepted" | "sizing_failed" | "bias_infeasible" | "spec_failed" | "error"
    metrics: dict[str, float | None] = field(default_factory=dict)
    margins: dict[str, float] = field(default_factory=dict)
    netlist: str = ""  # sized netlist text, only when accepted
    detail: str = ""


def _evaluate_candidate(
    item: tuple[int, str, dict[str, str]],
    topology: TopologyTemplate,
    tech: TechParams,
    spec: SizingSpec,
) -> _Outcome:
    """Run one circuit through recognize → size → bias check → simulate → gate."""
    index, netlist_text, variants = item
    try:
        parsed = parse(netlist_text)
        sr = recognize(parsed)
        fbr = assign_slots(sr, topology)
        result = size_circuit(parsed, sr, fbr, topology, tech, spec)
        if not result.transistors or result.solver_status not in (
                "GMID", "OPTIMAL", "FEASIBLE"):
            return _Outcome(index, variants, "sizing_failed",
                            detail=result.solver_status)
        if result.bias_feasible:
            ok, reason = check_bias_soundness(netlist_text, result, tech, spec)
            if not ok:
                return _Outcome(index, variants, "bias_infeasible",
                                detail=reason or "")
        else:
            return _Outcome(index, variants, "bias_infeasible",
                            detail=next(iter(result.warnings), ""))
        sim = simulate_metrics(netlist_text, result, tech, spec)
        metrics = {k: sim.get(k) for k in _MEASURED_SPECS}
        margins = _margins(sim, spec)
        if any(m < 0 for m in margins.values()):
            return _Outcome(index, variants, "spec_failed",
                            metrics=metrics, margins=margins)
        return _Outcome(index, variants, "accepted", metrics=metrics,
                        margins=margins,
                        netlist=sized_netlist(netlist_text, result))
    except Exception as e:  # count, report, and keep the run going
        return _Outcome(index, variants, "error",
                        detail=f"{type(e).__name__}: {e}")


def _candidates(topology: TopologyTemplate, modules: dict,
                limit: int | None) -> Iterator[tuple[int, str, dict[str, str]]]:
    """Yield ``(index, flat netlist text, variant names)`` per valid circuit."""
    gen = enumerate_circuits(topology, modules)
    for i, circuit in enumerate(islice(gen, limit), start=1):
        variants = {s: v.name for s, v in circuit.variant_map.items() if v is not None}
        yield i, to_flat_spice(circuit, name=f"circuit_{i:04d}"), variants


def _outcomes(items: Iterator, worker, workers: int) -> Iterator[_Outcome]:
    """Map ``worker`` over ``items``, in-process or via a bounded process pool.

    Chunked submission keeps memory flat on huge enumerations (the netlist
    texts of at most one chunk are alive at a time).
    """
    if workers <= 1:
        for item in items:
            yield worker(item)
        return
    chunk_size = workers * 8
    with ProcessPoolExecutor(max_workers=workers) as pool:
        while True:
            chunk = list(islice(items, chunk_size))
            if not chunk:
                return
            yield from pool.map(worker, chunk)


def _select_templates(templates: list[str] | None) -> list[TopologyTemplate]:
    topologies = load_topologies()
    if templates is None:
        return topologies
    by_name = {t.name: t for t in topologies}
    unknown = [n for n in templates if n not in by_name]
    if unknown:
        raise ValueError(
            f"unknown template(s): {', '.join(unknown)} — available: "
            f"{', '.join(sorted(by_name))}")
    return [by_name[n] for n in templates]


def _best_points(solutions: list[DesignSolution]) -> dict[str, DesignSolution]:
    best: dict[str, DesignSolution] = {}
    for crit, (key, pick_max) in _BEST_CRITERIA.items():
        cands = [s for s in solutions if s.metrics.get(key) is not None]
        if cands:
            best[crit] = (max if pick_max else min)(
                cands, key=lambda s: s.metrics[key])
    robust = [s for s in solutions if s.worst_margin != float("inf")]
    if robust:
        best["most_robust"] = max(robust, key=lambda s: s.worst_margin)
    return best


def _write_report(report: DesignReport, output_dir: Path) -> None:
    def _solution(s: DesignSolution) -> dict:
        d = asdict(s)
        if d["worst_margin"] == float("inf"):  # unconstrained → not valid JSON
            d["worst_margin"] = None
        return d

    payload = {
        "spec": report.spec,
        "tech": report.tech,
        "unverified_specs": report.unverified_specs,
        "runtime_s": report.runtime_s,
        "templates": {name: asdict(st) for name, st in report.stats.items()},
        "best_points": {crit: s.name for crit, s in report.best_points.items()},
        "solutions": [_solution(s) for s in report.solutions],
    }
    (output_dir / "report.json").write_text(json.dumps(payload, indent=1))


def design(
    spec: SizingSpec | str | Path,
    output_dir: str | Path,
    templates: Iterable[str] | None = None,
    tech: TechParams | str | Path = "gf180mcu",
    limit: int | None = None,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
) -> DesignReport:
    """Synthesize, size and SPICE-verify circuits against ``spec``; export the
    designs that meet it.

    :param spec: the target :class:`~circuitgenome.sizer.SizingSpec`, or a path
        to its YAML file.
    :param output_dir: where the sized netlists (one ``<topology>/`` folder per
        template) and ``report.json`` are written.  Created if missing.
    :param templates: topology template names to design, or ``None`` for all.
    :param tech: technology (``TechParams``, built-in name or YAML path).  Must
        carry a gm/Id LUT — this pipeline sizes with the gm/Id sizer.
    :param limit: evaluate at most this many circuits per template
        (enumeration order); ``None`` = exhaustive.
    :param workers: parallel worker processes for size+simulate (1 = in-process).
    :param progress: optional line sink (e.g. ``print``) for progress output.
    :returns: the :class:`~.models.DesignReport` (also written as ``report.json``).
    :raises RuntimeError: when ngspice is not on PATH (acceptance is
        SPICE-measured by definition).
    :raises ValueError: for an unknown template name or a tech without a
        gm/Id LUT.
    """
    if not ngspice_available():
        raise RuntimeError(
            "the designer verifies acceptance with ngspice, which was not "
            "found on PATH — install it (e.g. `brew install ngspice`).")
    if not isinstance(spec, SizingSpec):
        spec = load_spec(spec)
    if not isinstance(tech, TechParams):
        tech = load_tech(tech)
    if not tech.gmid_lut:
        raise ValueError(
            f"technology '{tech.name}' has no gm/Id LUT — the designer sizes "
            "with the gm/Id pipeline (use e.g. 'gf180mcu').")

    selected = _select_templates(list(templates) if templates is not None else None)
    modules = load_modules()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda _line: None)

    report = DesignReport(
        spec={k: v for k, v in asdict(spec).items() if v is not None},
        tech=tech.name,
    )
    t_run = time.perf_counter()

    for topology in selected:
        say(f"Template: {topology.name}")
        t0 = time.perf_counter()
        stats = TemplateStats(template=topology.name)
        worker = partial(_evaluate_candidate, topology=topology, tech=tech, spec=spec)
        tdir = output_dir / topology.name

        for outcome in _outcomes(_candidates(topology, modules, limit),
                                 worker, workers):
            stats.enumerated += 1
            if outcome.stage == "accepted":
                stats.accepted += 1
                tdir.mkdir(parents=True, exist_ok=True)
                name = f"circuit_{outcome.index:04d}"
                path = tdir / f"{name}_sized.ckt"
                path.write_text(outcome.netlist)
                report.solutions.append(DesignSolution(
                    name=name, topology=topology.name, variants=outcome.variants,
                    metrics=outcome.metrics, margins=outcome.margins,
                    worst_margin=min(outcome.margins.values(), default=float("inf")),
                    netlist_path=str(path)))
            else:
                setattr(stats, outcome.stage, getattr(stats, outcome.stage) + 1)
            if stats.enumerated % _PROGRESS_EVERY == 0:
                say(f"  {stats.enumerated} evaluated, {stats.accepted} accepted "
                    f"({time.perf_counter() - t0:.0f}s)")

        stats.runtime_s = time.perf_counter() - t0
        report.stats[topology.name] = stats
        say(f"  done: {stats.accepted}/{stats.enumerated} accepted "
            f"in {stats.runtime_s:.1f}s")

    report.best_points = _best_points(report.solutions)
    # Constrained specs that went unmeasured on at least one accepted circuit:
    # those solutions were accepted without that spec being SPICE-verified.
    report.unverified_specs = sorted({
        attr for s in report.solutions
        for key, (attr, _is_min) in _MEASURED_SPECS.items()
        if getattr(spec, attr) is not None and s.metrics.get(key) is None})
    report.runtime_s = time.perf_counter() - t_run
    _write_report(report, output_dir)
    return report
