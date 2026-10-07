#!/usr/bin/env python3
"""Regenerate the predicted-vs-measured prediction-error page for the docs.

The gm/Id sizer *predicts* each sized circuit's performance from its lookup
table; ngspice *measures* it on the foundry PDK.  This tool quantifies how far
apart the two land -- the **prediction error**, always measured − predicted --
for a fixed-seed random sample of circuits from every topology template, and
snapshots the result into the docs:

* ``docs/prediction_error/tables.inc`` -- provenance, coverage and
  per-metric error tables, ``.. include::``-d by the hand-written
  ``docs/prediction_error.rst`` (which carries the commentary);
* ``docs/prediction_error/<metric>.svg`` -- one box plot per metric;
* ``docs/_extra/prediction_error_explorer/index.html`` -- an interactive
  predicted-vs-measured scatter (plotly.js from its CDN).

Sizing and simulating the sample takes ~1 s of ngspice per circuit -- minutes
with one process per template on an idle machine, hours on a busy one -- so the
work is split in two.  ``run`` sizes and simulates, appending one CSV row per circuit to
``docs/prediction_error/results/<template>.csv`` (checked in); it skips
circuits already present, so a killed run resumes where it stopped, and
templates can run as separate processes in parallel.  ``render`` rebuilds the
tables, figures and explorer from those CSVs in seconds::

    python tools/gen_prediction_error.py run [--template NAME ...]
    python tools/gen_prediction_error.py render

``render`` needs matplotlib (``uv sync --group docs-data``); the Sphinx build
itself does not -- it only includes the checked-in outputs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import subprocess
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA_DIR = DOCS / "prediction_error"
RESULTS_DIR = DATA_DIR / "results"
RUN_INFO = DATA_DIR / "run.json"
TABLES = DATA_DIR / "tables.inc"  # not .rst: Sphinx must not build it standalone
EXPLORER = DOCS / "_extra" / "prediction_error_explorer" / "index.html"

TECH = "gf180mcu"
SPEC = "docs/prediction_error/spec.yaml"  # spec_01_easy, swing relaxed
N_PER_TEMPLATE = 100
SEED = 0

# key → (label, error unit, error form).  "abs" errors stay in the metric's own
# unit (dB / degrees / volts); "rel" errors are a percentage of the predicted
# value, for metrics that span decades across templates.
METRICS: dict[str, tuple[str, str, str]] = {
    "gain_db": ("Open-loop gain", "dB", "abs"),
    "gbw_hz": ("GBW", "%", "rel"),
    "phase_margin_deg": ("Phase margin", "°", "abs"),
    "slew_rate_vps": ("Slew rate", "%", "rel"),
    "power_w": ("Quiescent power", "%", "rel"),
    "output_swing_max_v": ("Output swing max", "V", "abs"),
    "output_swing_min_v": ("Output swing min", "V", "abs"),
    "cmrr_db": ("CMRR", "dB", "abs"),
    "psrr_db": ("PSRR+", "dB", "abs"),
}

# A design whose predicted gain exceeds the open-loop bench ceiling rails to
# ~0 dB in that bench, so these measurements are a bench artefact, not a model
# error -- they are left out of the statistics for such circuits.
OPEN_LOOP_METRICS = {"gain_db", "gbw_hz", "phase_margin_deg"}

# A fully-differential circuit is simulated perfectly symmetric (no mismatch),
# so its common-mode and supply rejection come out practically infinite
# (230-290 dB) -- again a property of the bench, not a model error.
SYMMETRY_METRICS = {"cmrr_db", "psrr_db"}

# Per-circuit outcome; only "simulated" rows carry measured metrics.
OUTCOMES = ("simulated", "sizing_failed", "bias_infeasible_sizer",
            "bias_infeasible_spice", "error")

COLUMNS = (["template", "index", "variants", "outcome", "detail",
            "open_loop_measurable", "seconds"]
           + [f"pred_{k}" for k in METRICS] + [f"meas_{k}" for k in METRICS])


# --------------------------------------------------------------------- run --

def sample_indices(count: int, n: int, seed: int, template: str) -> list[int]:
    """The sorted 1-based enumeration indices sampled from one template.

    Seeded per template, so adding or re-running one template never shifts
    another's sample; a template with fewer than ``n`` circuits is taken whole.
    """
    rng = random.Random(f"{seed}:{template}")
    return sorted(rng.sample(range(1, count + 1), min(n, count)))


def _evaluate(template, index, netlist_text, variants, topology, tech, spec):
    """Size one circuit and, when its bias is sound, measure it in ngspice."""
    from circuitgenome.recognizer import assign_slots, parse, recognize
    from circuitgenome.sizer import (check_bias_soundness, simulate_metrics,
                                     size_circuit)

    row = {"template": template, "index": index,
           "variants": ";".join(f"{s}={v}" for s, v in variants.items())}
    t0 = time.monotonic()
    try:
        parsed = parse(netlist_text)
        sr = recognize(parsed)
        result = size_circuit(parsed, sr, assign_slots(sr, topology),
                              topology, tech, spec)
        if not result.transistors or result.solver_status not in (
                "GMID", "OPTIMAL", "FEASIBLE"):
            row.update(outcome="sizing_failed", detail=result.solver_status)
        else:
            row.update({f"pred_{k}": result.metrics.get(k) for k in METRICS})
            row["open_loop_measurable"] = result.open_loop_measurable
            if not result.bias_feasible:
                row.update(outcome="bias_infeasible_sizer",
                           detail=next(iter(result.warnings), ""))
            else:
                ok, reason = check_bias_soundness(netlist_text, result, tech, spec)
                if not ok:
                    row.update(outcome="bias_infeasible_spice", detail=reason or "")
                else:
                    sim = simulate_metrics(netlist_text, result, tech, spec)
                    row.update({f"meas_{k}": sim.get(k) for k in METRICS})
                    row.update(outcome="simulated",
                               detail="; ".join(sim.get("notes") or []))
    except Exception as e:  # record and keep the run going
        row.update(outcome="error", detail=f"{type(e).__name__}: {e}")
    row["seconds"] = round(time.monotonic() - t0, 1)
    return row


def _provenance(n: int, seed: int) -> dict:
    """What the sample depends on, recorded in ``run.json`` for the page header."""
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    ngspice = subprocess.run(["ngspice", "-v"], capture_output=True,
                             text=True).stdout
    version = next((ln.strip("* ").split(":")[0].strip()
                    for ln in ngspice.splitlines() if "ngspice-" in ln), "ngspice")
    return {"tech": TECH, "spec": SPEC, "n_per_template": n, "seed": seed,
            "commit": commit, "ngspice": version, "date": date.today().isoformat()}


def run(templates: list[str] | None, n: int, seed: int) -> None:
    """Size and simulate the sample, appending to the per-template CSVs."""
    from circuitgenome.sizer import load_spec, load_tech, ngspice_available
    from circuitgenome.synthesizer import enumerate_circuits, to_flat_spice
    from circuitgenome.synthesizer.loader import load_modules, load_topologies

    if not ngspice_available():
        raise SystemExit("ngspice not found on PATH")
    info = _provenance(n, seed)
    if RUN_INFO.exists():
        old = json.loads(RUN_INFO.read_text())
        keys = ("tech", "spec", "n_per_template", "seed")
        if any(old[k] != info[k] for k in keys):
            raise SystemExit(f"{RUN_INFO} was sampled with different settings "
                             f"{ {k: old[k] for k in keys} }; delete "
                             f"{DATA_DIR} to start a fresh sample")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic, so templates running as parallel processes never read it half-written.
    tmp = RUN_INFO.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(info, indent=2) + "\n")
    tmp.replace(RUN_INFO)

    tech, spec, modules = load_tech(TECH), load_spec(ROOT / SPEC), load_modules()
    for topology in load_topologies():
        if templates and topology.name not in templates:
            continue
        path = RESULTS_DIR / f"{topology.name}.csv"
        done = {int(r["index"]) for r in _read_csv(path)}
        count = sum(1 for _ in enumerate_circuits(topology, modules))
        todo = set(sample_indices(count, n, seed, topology.name)) - done
        print(f"{topology.name}: {len(todo)} to evaluate "
              f"({len(done)} already done, {count:,} enumerated)", flush=True)
        if not todo:
            continue
        new_file = not path.exists()
        with path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            for i, circuit in enumerate(enumerate_circuits(topology, modules), 1):
                if i not in todo:
                    continue
                variants = {s: v.name for s, v in circuit.variant_map.items()
                            if v is not None}
                row = _evaluate(topology.name, i,
                                to_flat_spice(circuit, name=f"circuit_{i:05d}"),
                                variants, topology, tech, spec)
                writer.writerow(row)
                f.flush()
                todo.discard(i)
                print(f"  #{i:05d} {row['outcome']:<22} {row['seconds']:>6.1f}s"
                      f"  ({len(todo)} left)", flush=True)
                if not todo:
                    break


# ------------------------------------------------------------------ analysis --

def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _num(text: str | None) -> float | None:
    return float(text) if text not in (None, "", "None") else None


def load_rows(results_dir: Path = RESULTS_DIR) -> list[dict]:
    """Every CSV row, with metric columns parsed to ``float | None``."""
    rows = []
    for path in sorted(results_dir.glob("*.csv")):
        for r in _read_csv(path):
            r["index"] = int(r["index"])
            r["open_loop_measurable"] = r["open_loop_measurable"] != "False"
            for k in METRICS:
                r[f"pred_{k}"] = _num(r[f"pred_{k}"])
                r[f"meas_{k}"] = _num(r[f"meas_{k}"])
            rows.append(r)
    return rows


def prediction_error(metric: str, predicted: float, measured: float) -> float | None:
    """measured − predicted, in the metric's error unit (see :data:`METRICS`)."""
    if METRICS[metric][2] == "abs":
        return measured - predicted
    if predicted == 0:
        return None
    return 100.0 * (measured - predicted) / abs(predicted)


def error_of(row: dict, metric: str) -> float | None:
    """The row's prediction error for ``metric``, or ``None`` when excluded.

    A pair counts only when the circuit was simulated (sized, bias sound in
    both the sizer and SPICE), both values exist, and the bench measures a
    model-comparable value: gain, GBW and phase margin need a design the
    open-loop bench can measure, and CMRR/PSRR a single-ended one.
    """
    if row["outcome"] != "simulated":
        return None
    if metric in OPEN_LOOP_METRICS and not row["open_loop_measurable"]:
        return None
    if metric in SYMMETRY_METRICS and "fully_differential" in row["template"]:
        return None
    pred, meas = row[f"pred_{metric}"], row[f"meas_{metric}"]
    if pred is None or meas is None:
        return None
    return prediction_error(metric, pred, meas)


def stats(errors: list[float]) -> dict | None:
    """n, median, p10, p90 and the signed worst (largest |error|)."""
    if not errors:
        return None
    s = sorted(errors)

    def pct(q):  # linear interpolation, as numpy.percentile's default
        pos = (len(s) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return {"n": len(s), "median": pct(0.5), "p10": pct(0.1), "p90": pct(0.9),
            "worst": max(s, key=abs)}


def coverage(rows: list[dict]) -> dict[str, int]:
    """Counts of each per-circuit outcome, plus the two partial-metric flags."""
    out = {o: sum(r["outcome"] == o for r in rows) for o in OUTCOMES}
    sim = [r for r in rows if r["outcome"] == "simulated"]
    out["sampled"] = len(rows)
    out["no_predicted_gain"] = sum(r["pred_gain_db"] is None for r in sim)
    out["above_ceiling"] = sum(not r["open_loop_measurable"] for r in sim)
    return out


# -------------------------------------------------------------------- render --

def ordered_templates(rows: list[dict]) -> list[str]:
    """Templates present in ``rows``, grouped by stage count like the overview."""
    rank = {"one": 1, "two": 2, "three": 3}
    names = sorted({r["template"] for r in rows})
    return sorted(names, key=lambda t: (rank.get(t.split("_")[0], 9), names.index(t)))


def _fmt(metric: str, value: float) -> str:
    unit = METRICS[metric][1]
    if unit == "V":
        return f"{value:+.3f}"
    return f"{value:+.1f}"


def _list_table(header: list[str], body: list[list[str]], widths: str) -> list[str]:
    lines = [".. list-table::", "   :header-rows: 1", f"   :widths: {widths}", ""]
    for row in [header] + body:
        lines.append(f"   * - {row[0]}")
        lines.extend(f"     - {cell}" for cell in row[1:])
    return lines + [""]


def _stat_cells(metric: str, st: dict | None) -> list[str]:
    if st is None:
        return ["0", "—", "—", "—", "—"]
    return [str(st["n"])] + [_fmt(metric, st[k])
                             for k in ("median", "p10", "p90", "worst")]


def render_tables(rows: list[dict], info: dict, fig_rel: str) -> str:
    """The generated reStructuredText: provenance, coverage, summary, per metric.

    ``fig_rel`` is the figure directory relative to the *including* page.
    """
    templates = ordered_templates(rows)
    by_t = {t: [r for r in rows if r["template"] == t] for t in templates}
    out = [
        ".. DO NOT EDIT -- generated by tools/gen_prediction_error.py.",
        ".. Regenerate with `python tools/gen_prediction_error.py render`;",
        ".. re-sample with its `run` command (minutes to hours of ngspice).",
        "",
        "Run",
        "---",
        "",
        f"* **Technology:** ``{info['tech']}`` (gm/Id sizer, foundry PDK at its",
        "  nominal corner)",
        f"* **Spec:** ``{info['spec']}`` — ``spec_01_easy`` with the output-swing",
        "  targets relaxed to 1.1 V / 2.0 V, because a buffered op-amp's",
        "  source-follower output cannot reach the easy spec's 0.25 V / 3.05 V and",
        "  the sizer refuses to size it.  The swing prediction itself does not",
        "  depend on the target.",
        f"* **Sample:** {info['n_per_template']} circuits per template, drawn at",
        f"  random (seed {info['seed']}) from each template's full enumeration;",
        "  a smaller template is taken whole",
        f"* **Run:** commit ``{info['commit']}``, {info['ngspice']},",
        f"  {info['date']}",
        "",
        "Which pairs count",
        "-----------------",
        "",
        "A circuit contributes only when it sized, its bias point is sound both",
        "in the sizer's DC check and in a SPICE ``.op``, and ngspice measured",
        "the metric.  Gain, GBW and phase margin additionally skip designs",
        "whose predicted gain is above the open-loop bench ceiling — those rail",
        "to ~0 dB in that bench, an artefact rather than a model error — and",
        "CMRR and PSRR skip fully-differential templates, whose perfectly",
        "symmetric simulation (no device mismatch) measures 230–290 dB.  The",
        "coverage table counts every circuit-level exclusion:",
        "",
    ]
    cov_rows = []
    for t in templates:
        c = coverage(by_t[t])
        cov_rows.append([f"``{t}``"] + [str(c[k]) for k in (
            "sampled", "sizing_failed", "bias_infeasible_sizer",
            "bias_infeasible_spice", "error", "simulated",
            "no_predicted_gain", "above_ceiling")])
    total = coverage(rows)
    cov_rows.append(["**All templates**"] + [f"**{total[k]}**" for k in (
        "sampled", "sizing_failed", "bias_infeasible_sizer",
        "bias_infeasible_spice", "error", "simulated",
        "no_predicted_gain", "above_ceiling")])
    out += _list_table(
        ["Template", "Sampled", "Sizing failed", "Bias-infeasible (sizer)",
         "Bias-infeasible (SPICE .op)", "Error", "Simulated",
         "No predicted gain", "Gain above open-loop ceiling"],
        cov_rows, "28 8 8 9 9 6 8 8 9")

    out += ["Summary", "-------", "",
            "All templates pooled.  The median is the model's systematic bias;",
            "p10–p90 is the spread; *worst* is the single largest error, signed.",
            ""]
    summary = []
    for k, (label, unit, _) in METRICS.items():
        errs = [e for r in rows if (e := error_of(r, k)) is not None]
        summary.append([f":ref:`{label} <pe-{k}>`", unit] + _stat_cells(k, stats(errs)))
    out += _list_table(["Metric", "Unit", "n", "Median", "p10", "p90", "Worst"],
                       summary, "30 10 10 12 12 12 14")

    for k, (label, unit, _) in METRICS.items():
        heading = f"{label} ({unit})"
        out += [f".. _pe-{k}:", "", heading, "-" * len(heading), "",
                f".. image:: {fig_rel}/{k}.svg",
                f"   :alt: Box plot of the {label.lower()} prediction error per template",
                "   :width: 100%", ""]
        body = []
        for t in templates:
            errs = [e for r in by_t[t] if (e := error_of(r, k)) is not None]
            body.append([f"``{t}``"] + _stat_cells(k, stats(errs)))
        errs = [e for r in rows if (e := error_of(r, k)) is not None]
        body.append(["**All templates**"]
                    + [f"**{c}**" for c in _stat_cells(k, stats(errs))])
        out += _list_table(["Template", "n", "Median", "p10", "p90", "Worst"],
                           body, "40 10 12 12 12 14")
    return "\n".join(out).rstrip() + "\n"


def render_figures(rows: list[dict], fig_dir: Path) -> None:
    """One horizontal box plot (+ points) of the error per template, per metric."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"svg.hashsalt": "prediction-error", "font.size": 9})
    blue, ink, muted = "#2a78d6", "#0b0b0b", "#52514e"
    templates = ordered_templates(rows)
    fig_dir.mkdir(parents=True, exist_ok=True)
    for k, (label, unit, _) in METRICS.items():
        data = [[e for r in rows if r["template"] == t
                 and (e := error_of(r, k)) is not None] for t in templates]
        fig, ax = plt.subplots(figsize=(8, 0.38 * len(templates) + 1.1))
        fig.patch.set_facecolor("white")
        ys = list(range(len(templates), 0, -1))
        filled = [(d, y) for d, y in zip(data, ys) if d]
        if filled:
            ax.boxplot([d for d, _ in filled], positions=[y for _, y in filled],
                       vert=False, widths=0.55, whis=(10, 90), showfliers=False,
                       patch_artist=True,
                       boxprops={"facecolor": "#cde2fb", "edgecolor": blue},
                       medianprops={"color": blue, "linewidth": 2},
                       whiskerprops={"color": blue}, capprops={"color": blue})
            jitter = random.Random(k)
            for d, y in filled:
                ax.scatter(d, [y + jitter.uniform(-0.18, 0.18) for _ in d],
                           s=8, color=muted, alpha=0.45, linewidths=0, zorder=3)
        ax.axvline(0, color=ink, linewidth=1)
        # A few huge relative errors (e.g. +4000 % power) would squash every
        # other template into a sliver at zero: keep ±100 % linear, log beyond.
        if any(abs(e) > 300 for d in data for e in d):
            ax.set_xscale("symlog", linthresh=100)
        ax.set_yticks(ys)
        ax.set_yticklabels([f"{t}  (n={len(d)})" for t, d in zip(templates, data)])
        ax.set_ylim(0.4, len(templates) + 0.6)
        ax.set_xlabel(f"{label} prediction error, measured − predicted ({unit})")
        ax.grid(axis="x", color="#e4e3df", linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=muted)
        fig.tight_layout()
        fig.savefig(fig_dir / f"{k}.svg", format="svg", metadata={"Date": None})
        plt.close(fig)


def render_explorer(rows: list[dict], info: dict) -> str:
    """The standalone interactive predicted-vs-measured scatter page."""
    templates = ordered_templates(rows)
    points = {k: [] for k in METRICS}
    for r in rows:
        for k in METRICS:
            e = error_of(r, k)
            if e is not None:
                points[k].append([r["template"], r["index"],
                                  *(float(f"{v:.4g}") for v in
                                    (r[f"pred_{k}"], r[f"meas_{k}"], e))])
    variants = {f"{r['template']}#{r['index']}": r["variants"].replace(";", "<br>")
                for r in rows if r["outcome"] == "simulated"}
    data = {"metrics": {k: {"label": l, "unit": u, "log": f == "rel"}
                        for k, (l, u, f) in METRICS.items()},
            "templates": templates, "points": points, "variants": variants,
            "info": info}
    return _EXPLORER_TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))


_EXPLORER_TEMPLATE = """<!doctype html>
<!-- DO NOT EDIT -- generated by tools/gen_prediction_error.py render. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prediction error explorer — CircuitGenome</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
:root { color-scheme: light; --bg:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --grid:#e4e3df; --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --dim:#c9c8c3; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --bg:#1a1a19;
  --ink:#ffffff; --ink2:#c3c2b7; --grid:#383835; --s1:#3987e5; --s2:#d95926;
  --s3:#199e70; --dim:#4a4a46; } }
body { margin:0; padding:16px clamp(16px,4vw,40px); background:var(--bg);
  color:var(--ink); font:14px/1.5 system-ui, sans-serif; }
a { color:var(--s1); }
h1 { font-size:1.4rem; margin:.4rem 0; }
p { color:var(--ink2); max-width:70ch; }
.controls { display:flex; flex-wrap:wrap; gap:12px; margin:12px 0; }
label { display:flex; flex-direction:column; font-size:.8rem; color:var(--ink2); }
select { font:inherit; padding:4px 6px; max-width:90vw; }
#plot { width:100%; height:min(75vh, 720px); }
</style>
</head>
<body>
<a href="../prediction_error.html">← CircuitGenome docs: predicted vs. measured metrics</a>
<h1>Prediction error explorer</h1>
<p id="about"></p>
<div class="controls">
  <label>Metric <select id="metric"></select></label>
  <label>Template <select id="template"></select></label>
</div>
<div id="plot"></div>
<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const stageOf = t => ({one:1, two:2, three:3})[t.split("_")[0]];
const metricSel = document.getElementById("metric");
const templateSel = document.getElementById("template");
for (const [k, m] of Object.entries(D.metrics)) metricSel.add(new Option(m.label, k));
templateSel.add(new Option("All templates", ""));
for (const t of D.templates) templateSel.add(new Option(t, t));
const i = D.info;
document.getElementById("about").textContent =
  `Each point is one sized circuit: x is the gm/Id sizer's predicted value, y the ` +
  `ngspice-measured one; points on the dashed line are predicted exactly. ` +
  `${i.tech}, ${i.spec}, ${i.n_per_template} circuits per template (seed ${i.seed}), ` +
  `commit ${i.commit}, ${i.ngspice}, ${i.date}.`;

function fmt(v) { return Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0)
  ? v.toExponential(3) : v.toFixed(3); }

function draw() {
  const k = metricSel.value, m = D.metrics[k], pick = templateSel.value;
  const pts = D.points[k];
  const groups = pick
    ? [["Other templates", p => p[0] !== pick, css("--dim")],
       [pick, p => p[0] === pick, css("--s1")]]
    : [["1-stage", p => stageOf(p[0]) === 1, css("--s1")],
       ["2-stage", p => stageOf(p[0]) === 2, css("--s2")],
       ["3-stage", p => stageOf(p[0]) === 3, css("--s3")]];
  const traces = groups.map(([name, keep, color]) => {
    const g = pts.filter(keep);
    return { type: "scattergl", mode: "markers", name: `${name} (n=${g.length})`,
      x: g.map(p => p[2]), y: g.map(p => p[3]),
      marker: { size: 8, color, line: { width: 1, color: css("--bg") } },
      text: g.map(p => `<b>${p[0]} #${p[1]}</b><br>predicted ${fmt(p[2])}` +
        `<br>measured ${fmt(p[3])}<br>error ${p[4] >= 0 ? "+" : ""}` +
        `${p[4].toFixed(m.unit === "V" ? 3 : 1)} ${m.unit}` +
        `<br><br>${D.variants[p[0] + "#" + p[1]] || ""}`),
      hovertemplate: "%{text}<extra></extra>" };
  });
  const all = pts.flatMap(p => [p[2], p[3]]).filter(v => !m.log || v > 0);
  const lo = Math.min(...all), hi = Math.max(...all);
  const axis = title => ({ title, type: m.log ? "log" : "linear",
    gridcolor: css("--grid"), zerolinecolor: css("--grid"), color: css("--ink2") });
  Plotly.react("plot", traces, {
    paper_bgcolor: css("--bg"), plot_bgcolor: css("--bg"),
    font: { color: css("--ink"), family: "system-ui, sans-serif" },
    margin: { t: 20, r: 20, b: 60, l: 70 }, hovermode: "closest",
    legend: { orientation: "h", y: 1.06 },
    xaxis: axis(`Predicted ${m.label}`), yaxis: axis(`Measured ${m.label}`),
    shapes: pts.length ? [{ type: "line", x0: lo, y0: lo, x1: hi, y1: hi,
      line: { color: css("--ink2"), dash: "dash", width: 1 } }] : [],
  }, { responsive: true, displaylogo: false });
}
metricSel.onchange = templateSel.onchange = draw;
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
draw();
</script>
</body>
</html>
"""


def render(results_dir: Path = RESULTS_DIR, tables: Path = TABLES,
           explorer: Path = EXPLORER, run_info: Path = RUN_INFO,
           docs: Path = DOCS) -> None:
    """Rebuild the tables, figures and explorer from the checked-in CSVs.

    ``docs`` is the directory of the page that includes ``tables``; figure
    paths are written relative to it.
    """
    rows = load_rows(results_dir)
    info = json.loads(run_info.read_text())
    fig_dir = results_dir.parent
    render_figures(rows, fig_dir)
    tables.write_text(render_tables(rows, info,
                                    fig_dir.relative_to(docs).as_posix()))
    explorer.parent.mkdir(parents=True, exist_ok=True)
    explorer.write_text(render_explorer(rows, info))
    print(f"wrote {tables}, {len(METRICS)} figures and {explorer} "
          f"({len(rows)} circuits)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="size and simulate the sample (hours)")
    run_p.add_argument("--template", action="append", dest="templates",
                       help="only this template (repeatable); default all")
    run_p.add_argument("--n", type=int, default=N_PER_TEMPLATE,
                       help=f"circuits per template (default {N_PER_TEMPLATE})")
    run_p.add_argument("--seed", type=int, default=SEED,
                       help=f"sampling seed (default {SEED})")
    sub.add_parser("render", help="rebuild the docs tables from the CSVs (seconds)")
    args = parser.parse_args(argv)
    if args.command == "run":
        run(args.templates, args.n, args.seed)
    else:
        render()


if __name__ == "__main__":
    main()
