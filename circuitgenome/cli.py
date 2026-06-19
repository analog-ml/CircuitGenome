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
    from .recognizer import parse, recognize, assign_slots

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
            print(f"  {slot.name:<32}  {assignment.pattern_name}")
        else:
            print(f"  {slot.name:<32}  (unassigned)")

    if fbr_result.unassigned_structures:
        print(f"\nUnassigned structures ({len(fbr_result.unassigned_structures)}):")
        for s in fbr_result.unassigned_structures:
            print(f"  {s.name}  [{s.category}]")


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


if __name__ == "__main__":
    main()
