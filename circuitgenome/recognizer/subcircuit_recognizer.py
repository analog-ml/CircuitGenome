"""
Layer 1 — Subcircuit Recognizer (SR).

Matches the pattern library (``config/subcircuit_patterns.yaml``, loaded by
:func:`load_patterns`) against a
:class:`~circuitgenome.recognizer.models.ParsedNetlist`'s devices, producing
a :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`
(design doc section 5).

Each :class:`~circuitgenome.recognizer.models.PatternDef` is a small template
graph: a handful of typed
:class:`~circuitgenome.recognizer.models.PatternDevice` slots plus
:attr:`~circuitgenome.recognizer.models.PatternDef.same_net` equality
constraints between their terminals. For every pattern, in file order,
:func:`recognize`:

1. enumerates candidate assignments from template device refs to actual
   netlist devices via :func:`_find_assignments` -- filtered by
   :attr:`~circuitgenome.recognizer.models.PatternDevice.type` and checked
   against ``same_net`` via :func:`_check_same_net`;
2. resolves each pattern's :attr:`~circuitgenome.recognizer.models.PatternDef.pins`
   through the assignment via :func:`_resolve_pins`;
3. if the pattern declares an :attr:`~circuitgenome.recognizer.models.PatternDef.hook`,
   calls it (via :func:`_resolve_hook`) to accept-and-extend or reject the
   match -- see :class:`~circuitgenome.recognizer.models.HookMatch`;
4. records the result as a :class:`~circuitgenome.recognizer.models.RecognizedStructure`.

``recognize`` does not pick a winner among overlapping candidates -- multiple
``RecognizedStructure`` instances may cover the same device(s) if more than
one pattern matches them. See
:class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult` (design
doc section 5.4); disambiguation is Layer 2's job
(:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`).
"""
from __future__ import annotations

import importlib
from itertools import count
from pathlib import Path
from typing import Callable, Iterator

import yaml

from circuitgenome.synthesizer.models import Device

from .models import (
    HookMatch,
    ParsedNetlist,
    PatternDef,
    PatternDevice,
    RecognizedStructure,
    SubcircuitRecognitionResult,
)

_PATTERNS_PATH = Path(__file__).parent / "config" / "subcircuit_patterns.yaml"

HookFn = Callable[[dict[str, Device], dict[str, str], ParsedNetlist], "HookMatch | None"]


def load_patterns(path: Path | None = None) -> list[PatternDef]:
    """Load the SR pattern library from YAML.

    :param path: Path to a patterns YAML file with the same schema as
                  ``config/subcircuit_patterns.yaml`` (a top-level
                  ``patterns:`` list, each entry matching
                  :class:`~circuitgenome.recognizer.models.PatternDef`'s
                  fields). Defaults to the built-in
                  ``config/subcircuit_patterns.yaml``.
    :returns: The patterns in file order, as
              :class:`~circuitgenome.recognizer.models.PatternDef` instances.
              :attr:`~circuitgenome.recognizer.models.PatternDef.same_net` and
              :attr:`~circuitgenome.recognizer.models.PatternDef.pins` default
              to ``[]``/``{}`` if omitted from a pattern's YAML entry.
    """
    with open(path or _PATTERNS_PATH) as f:
        raw = yaml.safe_load(f)
    return [
        PatternDef(
            name=p["name"],
            category=p["category"],
            devices=[PatternDevice(ref=d["ref"], type=d["type"]) for d in p["devices"]],
            same_net=p.get("same_net", []),
            pins=p.get("pins", {}),
            tech_type_from=p.get("tech_type_from"),
            hook=p.get("hook"),
        )
        for p in raw["patterns"]
    ]


def _resolve_hook(path: str) -> HookFn:
    """Import and return the hook function referenced by a pattern.

    :param path: A ``"module:function"`` string, e.g.
                  ``"circuitgenome.recognizer.hooks:diode_connected_mosfet_bias_legs"``
                  (the value of
                  :attr:`~circuitgenome.recognizer.models.PatternDef.hook`).
    :returns: The imported callable, with signature
              ``(assignment, pins, netlist) -> HookMatch | None`` (see
              :class:`~circuitgenome.recognizer.models.HookMatch`).
    """
    module_name, func_name = path.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _check_same_net(pattern: PatternDef, assignment: dict[str, Device]) -> bool:
    """Check that ``assignment`` satisfies every ``same_net`` constraint.

    :param pattern: The pattern whose
                     :attr:`~circuitgenome.recognizer.models.PatternDef.same_net`
                     groups to check. Each group is a list of
                     ``"template_ref.terminal"`` strings that must all resolve
                     to the same net under ``assignment``.
    :param assignment: A candidate ``template_ref -> Device`` mapping covering
                        every template device referenced by ``pattern.same_net``.
    :returns: ``True`` if every group in
              :attr:`~circuitgenome.recognizer.models.PatternDef.same_net`
              resolves to a single net (groups of size 0 or 1 trivially pass);
              ``False`` if any group resolves to more than one distinct net.
    """
    for group in pattern.same_net:
        nets = set()
        for ref_term in group:
            ref, term = ref_term.split(".")
            nets.add(assignment[ref].terminals[term])
        if len(nets) != 1:
            return False
    return True


def _resolve_pins(pattern: PatternDef, assignment: dict[str, Device]) -> dict[str, str]:
    """Resolve a pattern's exported pins through a matched assignment.

    :param pattern: The pattern whose
                     :attr:`~circuitgenome.recognizer.models.PatternDef.pins`
                     map (``pin name -> "template_ref.terminal"``) to resolve.
    :param assignment: A ``template_ref -> Device`` mapping covering every
                        template device referenced by ``pattern.pins``.
    :returns: ``pin name -> net name``, e.g. ``{"in1": "net_diff1", "tail":
              "net_tail"}`` for ``differential_pair_nmos``. This becomes (part
              of) :attr:`~circuitgenome.recognizer.models.RecognizedStructure.pins`.
    """
    pins = {}
    for pin_name, ref_term in pattern.pins.items():
        ref, term = ref_term.split(".")
        pins[pin_name] = assignment[ref].terminals[term]
    return pins


def _find_assignments(pattern: PatternDef, devices: list[Device]) -> Iterator[dict[str, Device]]:
    """Enumerate ``pattern.devices -> devices`` assignments satisfying ``same_net``.

    A small backtracking search over
    :attr:`~circuitgenome.recognizer.models.PatternDef.devices` in order: each
    template device is tentatively bound to every not-yet-used netlist device
    of matching :attr:`~circuitgenome.synthesizer.models.Device.type`, and a
    complete assignment is checked against
    :attr:`~circuitgenome.recognizer.models.PatternDef.same_net` via
    :func:`_check_same_net` before being yielded.

    Assignments covering the same set of devices as a previously-yielded one
    are skipped, so symmetric templates (e.g. ``differential_pair_nmos``,
    where swapping ``m1``/``m2`` yields the same pair) don't produce duplicate
    matches.

    :param pattern: The pattern to match. Patterns are 1-4 devices, so plain
                     backtracking (no graph library) is sufficient.
    :param devices: Candidate netlist devices, typically
                     :attr:`~circuitgenome.recognizer.models.ParsedNetlist.devices`.
    :yields: Each distinct ``template_ref -> Device`` assignment (keyed by
             :attr:`~circuitgenome.recognizer.models.PatternDevice.ref`) that
             satisfies ``pattern.same_net``, in search order.
    """
    seen: set[frozenset[str]] = set()

    def backtrack(i: int, assignment: dict[str, Device], used: set[str]) -> Iterator[dict[str, Device]]:
        if i == len(pattern.devices):
            if _check_same_net(pattern, assignment):
                yield dict(assignment)
            return
        template_dev = pattern.devices[i]
        for dev in devices:
            if dev.ref in used or dev.type != template_dev.type:
                continue
            assignment[template_dev.ref] = dev
            used.add(dev.ref)
            yield from backtrack(i + 1, assignment, used)
            used.discard(dev.ref)
            del assignment[template_dev.ref]

    for assignment in backtrack(0, {}, set()):
        key = frozenset(d.ref for d in assignment.values())
        if key in seen:
            continue
        seen.add(key)
        yield assignment


def recognize(
    netlist: ParsedNetlist,
    patterns: list[PatternDef] | None = None,
) -> SubcircuitRecognitionResult:
    """Match every pattern in ``patterns`` against ``netlist.devices``.

    For each pattern, every assignment yielded by :func:`_find_assignments` is
    turned into a candidate :class:`~circuitgenome.recognizer.models.RecognizedStructure`
    -- resolving :attr:`~circuitgenome.recognizer.models.PatternDef.pins` via
    :func:`_resolve_pins`, then applying
    :attr:`~circuitgenome.recognizer.models.PatternDef.hook` (if any) via
    :func:`_resolve_hook`. A hook returning ``None`` drops that candidate
    entirely; otherwise its
    :attr:`~circuitgenome.recognizer.models.HookMatch.extra_devices` and
    :attr:`~circuitgenome.recognizer.models.HookMatch.extra_pins` are merged
    in. :attr:`~circuitgenome.recognizer.models.RecognizedStructure.tech_type`
    is taken from the matched
    :attr:`~circuitgenome.synthesizer.models.Device.type` of the template
    device named by
    :attr:`~circuitgenome.recognizer.models.PatternDef.tech_type_from`, if set.

    :param netlist: The parsed netlist to search,
                     typically :func:`~circuitgenome.recognizer.netlist_parser.parse`'s
                     output.
    :param patterns: Patterns to match, in the order they're tried (also the
                      order of
                      :attr:`~circuitgenome.recognizer.models.RecognizedStructure.index`
                      assignment per pattern name). Defaults to
                      :func:`load_patterns`'s built-in library.
    :returns: A :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`
              with one
              :class:`~circuitgenome.recognizer.models.RecognizedStructure` per
              accepted candidate (possibly multiple overlapping candidates for
              the same devices -- see
              :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`)
              and ``unrecognized_devices`` = every netlist device that is not
              part of *any* accepted candidate's
              :attr:`~circuitgenome.recognizer.models.RecognizedStructure.devices`.
    """
    patterns = patterns if patterns is not None else load_patterns()
    structures: list[RecognizedStructure] = []
    claimed_refs: set[str] = set()
    instance_counters: dict[str, count] = {}

    for pattern in patterns:
        for assignment in _find_assignments(pattern, netlist.devices):
            pins = _resolve_pins(pattern, assignment)
            devices = list(assignment.values())

            if pattern.hook:
                result = _resolve_hook(pattern.hook)(assignment, pins, netlist)
                if result is None:
                    continue
                devices = devices + result.extra_devices
                pins = {**pins, **result.extra_pins}

            tech_type = None
            if pattern.tech_type_from:
                tech_type = assignment[pattern.tech_type_from].type[0]

            counter = instance_counters.setdefault(pattern.name, count())
            structures.append(
                RecognizedStructure(
                    name=pattern.name,
                    category=pattern.category,
                    index=next(counter),
                    tech_type=tech_type,
                    pins=pins,
                    devices=devices,
                )
            )
            claimed_refs.update(d.ref for d in devices)

    unrecognized = [d for d in netlist.devices if d.ref not in claimed_refs]
    return SubcircuitRecognitionResult(structures=structures, unrecognized_devices=unrecognized)
