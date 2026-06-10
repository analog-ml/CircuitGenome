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


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "synthesize":
        _cmd_synthesize(args)


if __name__ == "__main__":
    main()
