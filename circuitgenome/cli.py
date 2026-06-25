from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .synthesizer import enumerate_circuits, to_flat_spice, to_hierarchical_spice
from .synthesizer.loader import load_modules, load_topologies


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="circuitgenome",
        description="Analog circuit topology synthesizer",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    synth = sub.add_parser("synthesize", help="Generate op-amp circuit variants")
    synth.add_argument("--type", default="opamp", choices=["opamp"],
                       help="Circuit type (default: opamp)")
    synth.add_argument("--stages", type=int, choices=[1, 2, 3],
                       help="Number of stages (1, 2, or 3)")
    synth.add_argument("--output-type", choices=["single_ended", "fully_differential"],
                       dest="output_type", help="Output topology type")
    synth.add_argument("--topology", help="Exact topology name to use")
    synth.add_argument("--format", choices=["flat", "hierarchical", "both"],
                       default="flat", help="SPICE output format (default: flat)")
    synth.add_argument("--output-dir", type=Path, default=Path("."),
                       dest="output_dir", help="Directory for output files (default: .)")
    synth.add_argument("--list-topologies", action="store_true", dest="list_topologies",
                       help="Print available topology names and exit")
    synth.add_argument("--list-modules", action="store_true", dest="list_modules",
                       help="Print available module variants and exit")
    synth.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print summary without writing files")

    sub.add_parser("visualize", help="Launch the topology visualizer (Streamlit web UI)")

    recog = sub.add_parser("recognize", help="Identify functional blocks in a SPICE netlist")
    recog.add_argument("netlist_file", type=Path, metavar="NETLIST",
                       help="Path to the flat SPICE netlist file")
    recog.add_argument("--topology", help="Topology name for FBR slot assignment")

    size = sub.add_parser("size", help="Compute initial transistor W/L values from a performance spec")
    size.add_argument("netlist_file", type=Path, metavar="NETLIST",
                      help="Path to the flat SPICE netlist file")
    size.add_argument("--topology", required=True, help="Topology name (required for sizing)")
    size.add_argument("--tech", type=Path, dest="tech_file",
                      help="Technology YAML config (default: built-in generic)")
    size.add_argument("--spec", type=Path, dest="spec_file", required=True,
                      help="Performance specification YAML file")
    size.add_argument("--time-limit", type=float, default=30.0, dest="time_limit",
                      help="CP-SAT solver time limit in seconds (default: 30)")
    size.add_argument("--simulate", action="store_true",
                      help="After sizing, verify metrics with an ngspice simulation "
                           "(same technology) and print analytical vs SPICE")
    size.add_argument("--refine", action="store_true",
                      help="After sizing, re-evaluate metrics at the actual SPICE "
                           "operating point (corrects for bias currents that don't "
                           "fully flow, e.g. a headroom-starved tail). Single-ended.")

    return parser.parse_args(argv)


def _cmd_synthesize(args: argparse.Namespace) -> None:
    modules = load_modules()
    topologies = load_topologies()

    if args.list_topologies:
        for t in topologies:
            info = f"stages={t.config.get('stages')}, output={t.config.get('output_type')}"
            if t.config.get("compensation_scheme"):
                info += f", compensation={t.config['compensation_scheme']}"
            print(f"  {t.name}  ({info})")
        return

    if args.list_modules:
        for category, variants in sorted(modules.items()):
            print(f"\n[{category}]")
            for v in variants:
                print(f"  {v.name} — {v.display_name}")
        return

    # Filter topologies
    filtered = topologies
    if args.topology:
        filtered = [t for t in filtered if t.name == args.topology]
    if args.stages:
        filtered = [t for t in filtered if t.config.get("stages") == args.stages]
    if args.output_type:
        filtered = [t for t in filtered if t.config.get("output_type") == args.output_type]

    if not filtered:
        print("No topologies match the given filters.", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for topology in filtered:
        print(f"\nTopology: {topology.name}")
        count = 0
        for circuit in enumerate_circuits(topology, modules):
            count += 1
            total += 1
            short_name = f"circuit_{count:04d}"

            if args.dry_run:
                continue

            if args.format in ("flat", "both"):
                flat_path = args.output_dir / f"{short_name}_flat.ckt"
                flat_path.write_text(to_flat_spice(circuit, name=short_name))

            if args.format in ("hierarchical", "both"):
                hier_path = args.output_dir / f"{short_name}_hier.ckt"
                hier_path.write_text(to_hierarchical_spice(circuit, name=short_name))

        print(f"  Generated {count} circuits")

    print(f"\nTotal: {total} circuits", end="")
    if args.dry_run:
        print(" (dry run — no files written)")
    else:
        print(f" written to {args.output_dir}/")


def _cmd_recognize(args: argparse.Namespace) -> None:
    from .recognizer import parse, recognize, assign_slots, group_by_category

    netlist_text = args.netlist_file.read_text()
    parsed = parse(netlist_text)
    sr_result = recognize(parsed)

    print(f"Netlist: {args.netlist_file.name}")
    print(f"\nRecognized structures ({len(sr_result.structures)}):")
    for s in sr_result.structures:
        device_names = ", ".join(d.ref for d in s.devices)
        print(f"  [{s.category}]  {s.name}  (devices: {device_names})")

    if sr_result.unrecognized_devices:
        print(f"\nUnrecognized devices ({len(sr_result.unrecognized_devices)}):")
        for d in sr_result.unrecognized_devices:
            print(f"  {d.ref} ({d.type})")
    else:
        print("\nUnrecognized devices: none")

    if not args.topology:
        fbr_result = group_by_category(sr_result, parsed)
        print("\nFunctional block groups (topology-free):")
        for cb, categories in fbr_result.groups.items():
            print(f"\n  [{cb}]")
            for cat, structs in categories.items():
                devices = ", ".join(d.ref for d in structs[0].devices)
                print(f"    {cat:<32}  {structs[0].name}  (devices: {devices})")
        return

    topology = next((t for t in load_topologies() if t.name == args.topology), None)
    if topology is None:
        print(f"Unknown topology: {args.topology}", file=sys.stderr)
        sys.exit(1)

    fbr_result = assign_slots(sr_result, topology)

    print(f"\nSlot assignments (topology: {args.topology}):")
    for slot in topology.slots:
        assignment = fbr_result.slot_assignments.get(slot.name)
        if assignment:
            devices = ", ".join(d.ref for d in assignment.structure.devices)
            print(f"  {slot.name:<32}  {assignment.pattern_name}  (devices: {devices})")
        else:
            print(f"  {slot.name:<32}  (unassigned)")

    if fbr_result.unassigned_structures:
        print(f"\nUnassigned structures ({len(fbr_result.unassigned_structures)}):")
        for s in fbr_result.unassigned_structures:
            print(f"  {s.name}  [{s.category}]")


def _cmd_size(args: argparse.Namespace) -> None:
    import yaml
    from .recognizer import parse, recognize, assign_slots
    from .sizer import load_tech, size_circuit, SizingSpec, UnsupportedTechError

    netlist_text = args.netlist_file.read_text()
    parsed = parse(netlist_text)
    sr_result = recognize(parsed)

    topology = next((t for t in load_topologies() if t.name == args.topology), None)
    if topology is None:
        print(f"Unknown topology: {args.topology}", file=sys.stderr)
        sys.exit(1)

    fbr_result = assign_slots(sr_result, topology)

    tech = load_tech(args.tech_file)  # None → built-in generic

    with open(args.spec_file) as f:
        spec_data = yaml.safe_load(f)
    spec = SizingSpec(**{k: v for k, v in spec_data.items() if k in SizingSpec.__dataclass_fields__})

    try:
        result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec,
                              time_limit_s=args.time_limit)
    except UnsupportedTechError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "refine", False) and result.transistors:
        from .sizer.refine import refine_with_spice
        from .sizer.sizer import _extract_slot_transistors
        from .sizer.device_model import build_device_model
        slot_t = _extract_slot_transistors(fbr_result)
        gd_load_r = (1.0 / min(result.resistors.values())) if result.resistors else 0.0
        result = refine_with_spice(result, netlist_text, slot_t, tech, spec,
                                   build_device_model(tech), gd_load_r)

    # Ground the feasibility verdict in the SPICE DC operating point: the analytical
    # bias check only validates the input-pair tail, so it false-positives on circuits
    # whose downstream stages don't bias. Automatic when ngspice is available; the
    # analytical verdict stands when it isn't.
    if result.transistors and result.bias_feasible:
        from .sizer.spice_sim import ngspice_available, check_bias_soundness
        if ngspice_available():
            ok, reason = check_bias_soundness(netlist_text, result, tech, spec)
            if not ok:
                result.bias_feasible = False
                result.warnings.append(reason)

    print(f"Netlist: {args.netlist_file.name}  |  Topology: {args.topology}")
    print(f"Tech: {tech.name}")
    print(f"\nSolver: {result.solver_status}")

    for w in result.warnings:
        print(f"⚠ {w}")

    if not result.transistors:
        print("No feasible sizing found — relax the performance spec or widen the W/L grid.")
        sys.exit(1)

    print("\nTransistor sizing:")
    for ref, s in result.transistors.items():
        print(f"  {ref:<30}  W={s.w_um:.3f}µm  L={s.l_um:.3f}µm  "
              f"IDS={s.ids_a*1e6:.2f}µA  VGS={s.vgs_v:.3f}V  VDS_sat={s.vds_sat_v:.3f}V")

    if result.cc_pf is not None:
        print(f"  Cc = {result.cc_pf:.1f}pF")
    for ref, ohms in result.resistors.items():
        print(f"  {ref:<30}  R={ohms/1e3:.2f}kΩ")

    if result.metrics:
        _METRIC_LABELS = {
            "gain_db": ("Open-loop gain", "dB", True),
            "gbw_hz": ("GBW", "MHz", True),
            "phase_margin_deg": ("Phase margin", "°", True),
            "slew_rate_vps": ("Slew rate", "V/µs", True),
            "power_w": ("Quiescent power", "mW", False),
            "output_swing_max_v": ("Output swing max", "V", True),
            "output_swing_min_v": ("Output swing min", "V", False),
            "cmrr_db": ("CMRR", "dB", True),
            "psrr_db": ("PSRR+", "dB", True),
        }
        _SCALE = {
            "gbw_hz": 1e-6, "slew_rate_vps": 1e-6, "power_w": 1e3,
        }
        _SPEC_KEYS = {
            "gain_db": "gain_min_db", "gbw_hz": "gbw_min_hz",
            "phase_margin_deg": "phase_margin_min_deg",
            "slew_rate_vps": "slew_rate_min_vps", "power_w": "power_max_w",
            "output_swing_max_v": "output_swing_max_v",
            "output_swing_min_v": "output_swing_min_v",
            "cmrr_db": "cmrr_min_db", "psrr_db": "psrr_min_db",
        }
        # Feasibility verdict drives how (and whether) metrics are shown:
        #   INFEASIBLE — bias point collapses → metrics are meaningless, suppress them.
        #   MARGINAL   — biases but misses spec → metrics are real, show with ✗.
        #   FEASIBLE   — biases and meets spec → normal ✓ table.
        failing = [k for k, m in (result.margins or {}).items() if m < 0]
        if not result.bias_feasible:
            print("\nFeasibility: INFEASIBLE — bias point cannot be established.")
            for w in result.warnings:
                if any(t in w for t in ("cascode", "headroom", "collapse", "SPICE bias")):
                    print(f"  ↳ {w}")
            print("  Performance not evaluated; run --simulate to measure the "
                  "actual operating point.")
        elif tech.spice_model:
            # The technology has a BSIM4 model card, so the analytical metrics
            # (square-law / single-pole) would mismatch the selected device model.
            # Measure performance directly with ngspice instead — the SPICE numbers
            # are the sole source of truth, with no analytical fallback.
            from .sizer.spice_sim import ngspice_available, simulate_metrics
            if not ngspice_available():
                print(f"\nError: tech {tech.name} uses a SPICE model card; performance "
                      "metrics are measured with ngspice, which was not found on PATH. "
                      "Install it (e.g. `brew install ngspice`) and rerun.",
                      file=sys.stderr)
                sys.exit(1)
            sim = simulate_metrics(netlist_text, result, tech, spec)
            # Only these keys are measured by the SPICE rig; CMRR/PSRR/output-swing
            # have no testbench yet and are omitted (see note below).
            spice_keys = ["gain_db", "gbw_hz", "phase_margin_deg",
                          "slew_rate_vps", "power_w"]
            rows = []
            any_fail = False
            for key in spice_keys:
                label, unit, is_min = _METRIC_LABELS[key]
                scale = _SCALE.get(key, 1.0)
                raw = sim.get(key)
                spec_val = getattr(spec, _SPEC_KEYS[key], None)
                if raw is None:
                    rows.append(f"  {label:<22} {'n/a':<16}  "
                                "[ngspice could not extract this metric]")
                    continue
                val_str = f"{raw * scale:.2f} {unit}"
                if spec_val is not None:
                    margin = (raw - spec_val) if is_min else (spec_val - raw)
                    any_fail = any_fail or margin < 0
                    op = "≥" if is_min else "≤"
                    spec_str = f"[spec {op} {spec_val * scale:.2f} {unit}]"
                    sign = "+" if margin >= 0 else ""
                    margin_str = f"margin {sign}{margin * scale:.2f} {unit}"
                    status = "✓" if margin >= 0 else "✗"
                    rows.append(f"  {label:<22} {val_str:<16}  {spec_str:<30}  "
                                f"{margin_str}  {status}")
                else:
                    rows.append(f"  {label:<22} {val_str}")
            verdict = ("MARGINAL — biases, but does not meet spec"
                       if any_fail else "FEASIBLE")
            print(f"\nFeasibility: {verdict}")
            print("\nPerformance metrics (ngspice / BSIM4):")
            for row in rows:
                print(row)
            for note in sim.get("notes", []) or []:
                print(f"  ⓘ {note}")
            print("  ⓘ CMRR, PSRR, and output swing are not measured by the current "
                  "ngspice rig and are omitted.")
        else:
            verdict = ("MARGINAL — biases, but does not meet spec (see ⚠ above)"
                       if failing else "FEASIBLE")
            print(f"\nFeasibility: {verdict}")
            print("\nPerformance metrics:")
            for key, (label, unit, _is_min) in _METRIC_LABELS.items():
                if key not in result.metrics:
                    continue
                raw = result.metrics[key]
                scale = _SCALE.get(key, 1.0)
                val_str = f"{raw * scale:.2f} {unit}"
                spec_key = _SPEC_KEYS.get(key)
                spec_val = getattr(spec, spec_key, None) if spec_key else None
                margin = result.margins.get(key)
                if spec_val is not None and margin is not None:
                    op = "≥" if _is_min else "≤"
                    spec_str = f"[spec {op} {spec_val * scale:.2f} {unit}]"
                    sign = "+" if margin >= 0 else ""
                    margin_str = f"margin {sign}{margin * scale:.2f} {unit}"
                    status = "✓" if margin >= 0 else "✗"
                    print(f"  {label:<22} {val_str:<16}  {spec_str:<30}  {margin_str}  {status}")
                else:
                    print(f"  {label:<22} {val_str}")

    if args.simulate and tech.spice_model:
        print("\n(--simulate is redundant for this technology: the metrics above are "
              "already measured with ngspice.)")
    elif args.simulate:
        from .sizer.spice_sim import ngspice_available, simulate_metrics
        print("\nSPICE verification (ngspice):")
        if not result.bias_feasible:
            print("  Bias point infeasible — 'analytical' not evaluated; the "
                  "'SPICE' column is the measured operating point.")
        if not ngspice_available():
            print("  ngspice not found on PATH — install it (e.g. `brew install ngspice`).")
        else:
            sim = simulate_metrics(args.netlist_file.read_text(), result, tech, spec)
            cols = [
                ("gain_db", "Open-loop gain", "dB", 1.0, "{:.2f}"),
                ("gbw_hz", "GBW", "MHz", 1e-6, "{:.3f}"),
                ("phase_margin_deg", "Phase margin", "°", 1.0, "{:.1f}"),
                ("slew_rate_vps", "Slew rate", "V/µs", 1e-6, "{:.3f}"),
                ("power_w", "Quiescent power", "mW", 1e3, "{:.4f}"),
                ("output_swing_max_v", "Output swing max", "V", 1.0, "{:.3f}"),
                ("output_swing_min_v", "Output swing min", "V", 1.0, "{:.3f}"),
            ]
            print(f"  {'metric':<20}{'analytical':>15}{'SPICE':>15}{'Δ':>10}")
            for key, label, unit, scl, fmt in cols:
                a = result.metrics.get(key) if result.bias_feasible else None
                s = sim.get(key)
                a_str = f"{fmt.format(a*scl)} {unit}" if a is not None else "n/a"
                s_str = f"{fmt.format(s*scl)} {unit}" if s is not None else "n/a"
                if a not in (None, 0) and s is not None:
                    d_str = f"{(s-a)/abs(a)*100:+.0f}%"
                else:
                    d_str = "—"
                print(f"  {label:<20}{a_str:>15}{s_str:>15}{d_str:>10}")
            for note in sim.get("notes", []) or []:
                print(f"  ⓘ {note}")
            print("  (SPICE = best-effort cross-check; FD AC metrics may show n/a)")


def _cmd_visualize(args: argparse.Namespace) -> None:
    try:
        import streamlit.web.cli as stcli
    except ImportError:
        print("The visualizer requires the 'viz' extra: pip install circuitgenome[viz]", file=sys.stderr)
        sys.exit(1)

    app_path = Path(__file__).parent / "visualizer" / "app.py"
    sys.argv = ["streamlit", "run", str(app_path)]
    stcli.main()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "synthesize":
        _cmd_synthesize(args)
    elif args.command == "visualize":
        _cmd_visualize(args)
    elif args.command == "recognize":
        _cmd_recognize(args)
    elif args.command == "size":
        _cmd_size(args)


if __name__ == "__main__":
    main()
