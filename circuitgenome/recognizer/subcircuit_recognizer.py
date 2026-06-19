"""
Layer 1 — Subcircuit Recognizer (SR).

Matches the pattern library (``config/primitives.yaml`` +
``config/subcircuit_patterns.yaml``, loaded by :func:`load_patterns`) against
a :class:`~circuitgenome.recognizer.models.ParsedNetlist`'s devices, producing
a :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`.

The recognition algorithm runs in multiple ordered passes determined by a
topological sort of pattern :attr:`~circuitgenome.recognizer.models.PatternDef.children`
references:

**Pass 0 — exclusive per-device primitives.** For each device, the
highest-priority :attr:`~circuitgenome.recognizer.models.PatternDef.exclusive`
pattern that matches it is assigned. Each device is claimed by exactly one
exclusive pattern. Exclusive patterns live in ``config/primitives.yaml``.

**Passes 1+ — multi-level composites.** Patterns whose computed level ≥ 1
(i.e. they declare children) are tried in level order. A match is only
accepted when all declared children are present as structures from the
previous level's pass. Multiple parent structures may share the same child
(DAG sharing).

**Non-exclusive level-0 pass.** Patterns with no children and
``exclusive=False`` run in a single pass using the original algorithm — every
assignment is accepted regardless of exclusive device claims. This preserves
backward compatibility with the 34 MVP composite patterns.

See :class:`~circuitgenome.recognizer.models.PatternDef` for field semantics.
"""
from __future__ import annotations

import importlib
from itertools import count
from pathlib import Path
from typing import Callable, Iterator

import yaml

from circuitgenome.synthesizer.models import Device

from .models import (
    ChildDef,
    HookMatch,
    ParsedNetlist,
    PatternDef,
    PatternDevice,
    RecognizedStructure,
    SubcircuitRecognitionResult,
)

_PRIMITIVES_PATH = Path(__file__).parent / "config" / "primitives.yaml"
_PATTERNS_PATH = Path(__file__).parent / "config" / "subcircuit_patterns.yaml"

HookFn = Callable[[dict[str, Device], dict[str, str], ParsedNetlist], "HookMatch | None"]


def _load_file(path: Path, key: str = "patterns") -> list[PatternDef]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return [
        PatternDef(
            name=p["name"],
            category=p.get("category"),
            devices=[PatternDevice(ref=d["ref"], type=d["type"]) for d in p["devices"]],
            same_net=p.get("same_net", []),
            pins=p.get("pins", {}),
            tech_type_from=p.get("tech_type_from"),
            hook=p.get("hook"),
            children=[
                ChildDef(pattern=c["pattern"], devices=c["devices"])
                for c in p.get("children", [])
            ],
            exclusive=p.get("exclusive", False),
            priority=p.get("priority", 0),
        )
        for p in raw[key]
    ]


def load_patterns(path: Path | None = None) -> list[PatternDef]:
    """Load the SR pattern library.

    With no arguments, loads ``config/primitives.yaml`` (level-0 exclusive
    patterns) followed by ``config/subcircuit_patterns.yaml`` (level-1+
    structural composites and MVP composites). Pass an explicit path to load
    a single-file library instead (legacy / testing use).

    :param path: If given, load only this file (top-level key ``"patterns"``).
                  Defaults to loading both built-in files.
    :returns: :class:`~circuitgenome.recognizer.models.PatternDef` instances
              in load order (primitives first when using the default).
    """
    if path is not None:
        return _load_file(path)
    return _load_file(_PRIMITIVES_PATH, key="primitives") + _load_file(_PATTERNS_PATH)


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
    """Check that ``assignment`` satisfies every fully-assigned ``same_net`` constraint.

    ``assignment`` may be partial (built up incrementally by
    :func:`_find_assignments`'s backtracking search): a group whose
    ``template_ref``\\s aren't all in ``assignment`` yet is skipped (neither
    passes nor fails). This lets :func:`_find_assignments` call this function
    after *every* tentative binding to prune invalid branches as early as
    possible, rather than only once a complete assignment is built.

    :param pattern: The pattern whose
                     :attr:`~circuitgenome.recognizer.models.PatternDef.same_net`
                     groups to check. Each group is a list of
                     ``"template_ref.terminal"`` strings that must all resolve
                     to the same net under ``assignment``.
    :param assignment: A (possibly partial) ``template_ref -> Device`` mapping.
    :returns: ``False`` if any group whose refs are all present in
              ``assignment`` resolves to more than one distinct net;
              ``True`` otherwise (including groups of size 0 or 1, and groups
              not yet fully assigned).
    """
    for group in pattern.same_net:
        nets = set()
        for ref_term in group:
            ref, term = ref_term.split(".")
            dev = assignment.get(ref)
            if dev is None:
                break
            nets.add(dev.terminals[term])
        else:
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
    of matching :attr:`~circuitgenome.synthesizer.models.Device.type`. After
    each binding, :func:`_check_same_net` is called on the (possibly partial)
    assignment so far -- any
    :attr:`~circuitgenome.recognizer.models.PatternDef.same_net` group whose
    refs are all bound is checked immediately, pruning that branch before
    recursing further if it fails. By the time a complete assignment is
    reached, every group has therefore already been checked.

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
            yield dict(assignment)
            return
        template_dev = pattern.devices[i]
        for dev in devices:
            if dev.ref in used or dev.type != template_dev.type:
                continue
            assignment[template_dev.ref] = dev
            used.add(dev.ref)
            if _check_same_net(pattern, assignment):
                yield from backtrack(i + 1, assignment, used)
            used.discard(dev.ref)
            del assignment[template_dev.ref]

    for assignment in backtrack(0, {}, set()):
        key = frozenset(d.ref for d in assignment.values())
        if key in seen:
            continue
        seen.add(key)
        yield assignment


def _compute_levels(patterns: list[PatternDef]) -> dict[str, int]:
    """Return ``{pattern_name: level}`` via topological sort of children refs.

    Level is ``0`` for patterns with no children; for others, it is
    ``max(child_levels) + 1``. Patterns within the same YAML file may
    reference children defined in another file — the combined list from
    :func:`load_patterns` must include all referenced names.

    :param patterns: All patterns in the library (primitives + composites).
    :returns: Mapping from pattern name to its computed level (0-based).
    """
    by_name = {p.name: p for p in patterns}
    memo: dict[str, int] = {}

    def level(name: str) -> int:
        if name in memo:
            return memo[name]
        p = by_name[name]
        lv = 0 if not p.children else max(level(c.pattern) for c in p.children) + 1
        memo[name] = lv
        return lv

    for p in patterns:
        level(p.name)
    return memo


def _match_children(
    pattern: PatternDef,
    assignment: dict[str, Device],
    prev_structures: list[RecognizedStructure],
) -> list[RecognizedStructure] | None:
    """Return child structures if all of ``pattern.children`` are satisfied, else ``None``.

    For each :class:`~circuitgenome.recognizer.models.ChildDef` in
    ``pattern.children``, looks up a :class:`~circuitgenome.recognizer.models.RecognizedStructure`
    from ``prev_structures`` whose name matches and whose device set equals
    the child's device set (resolved through ``assignment``).

    :param pattern: The pattern being matched.
    :param assignment: A complete ``template_ref -> Device`` mapping for
                        ``pattern``.
    :param prev_structures: All structures recognized at the previous level
                             (level N-1 when this pattern is at level N).
    :returns: Ordered list of matched child structures (same order as
              ``pattern.children``), or ``None`` if any child is unsatisfied.
    """
    if not pattern.children:
        return []

    by_devset: dict[frozenset, list[RecognizedStructure]] = {}
    for s in prev_structures:
        key = frozenset(d.ref for d in s.devices)
        by_devset.setdefault(key, []).append(s)

    child_matches: list[RecognizedStructure] = []
    for child_def in pattern.children:
        child_devset = frozenset(assignment[ref].ref for ref in child_def.devices)
        candidates = [s for s in by_devset.get(child_devset, []) if s.name == child_def.pattern]
        if not candidates:
            return None
        child_matches.append(candidates[0])
    return child_matches


def recognize(
    netlist: ParsedNetlist,
    patterns: list[PatternDef] | None = None,
) -> SubcircuitRecognitionResult:
    """Match every pattern in ``patterns`` against ``netlist.devices``.

    Runs the multi-level recognition algorithm:

    1. **Pass 0 (exclusive)** — assign each device to the highest-priority
       exclusive (level-0 primitive) pattern that matches it.
    2. **Passes 1+ (multi-level)** — for each level in topological order,
       find all assignments that satisfy both the template constraints and the
       children constraint (all declared children must be present from the
       previous level's results).
    3. **Non-exclusive level-0 pass** — run all non-exclusive, children-less
       patterns in a single pass (the original MVP algorithm, backward-
       compatible with the 34 existing composite patterns).

    :param netlist: The parsed netlist to search,
                     typically :func:`~circuitgenome.recognizer.netlist_parser.parse`'s
                     output.
    :param patterns: Patterns to match. Defaults to :func:`load_patterns`'s
                      built-in library (primitives + subcircuit_patterns).
    :returns: A :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`
              with one
              :class:`~circuitgenome.recognizer.models.RecognizedStructure` per
              accepted candidate (level-0 primitives, level-1+ composites, and
              MVP composites) and ``unrecognized_devices`` = every netlist
              device that is not part of *any* accepted candidate's device set.
    """
    patterns = patterns if patterns is not None else load_patterns()

    levels = _compute_levels(patterns)
    max_level = max(levels.values(), default=0)
    by_level: dict[int, list[PatternDef]] = {
        lv: [p for p in patterns if levels[p.name] == lv]
        for lv in range(max_level + 1)
    }

    instance_counters: dict[str, count] = {}
    all_structures: list[RecognizedStructure] = []
    device_map: dict[str, RecognizedStructure] = {}  # ref → level-0 primitive structure

    # Pass 0: exclusive per-device (level-0 primitives, highest priority wins)
    exclusive_patterns = sorted(
        [p for p in by_level.get(0, []) if p.exclusive],
        key=lambda p: -p.priority,
    )
    for device in netlist.devices:
        for pattern in exclusive_patterns:
            for assignment in _find_assignments(pattern, [device]):
                pins = _resolve_pins(pattern, assignment)
                tech_type = device.type[0] if pattern.tech_type_from else None
                counter = instance_counters.setdefault(pattern.name, count())
                s = RecognizedStructure(
                    name=pattern.name,
                    category=pattern.category,
                    index=next(counter),
                    tech_type=tech_type,
                    pins=pins,
                    devices=[device],
                )
                device_map[device.ref] = s
                all_structures.append(s)
                break
            if device.ref in device_map:
                break

    # Passes 1+: multi-level composites (children verification required)
    prev_level_structures = list(all_structures)  # level-0 structures for Pass 1
    for lv in range(1, max_level + 1):
        level_structures: list[RecognizedStructure] = []
        for pattern in by_level.get(lv, []):
            for assignment in _find_assignments(pattern, netlist.devices):
                pins = _resolve_pins(pattern, assignment)
                devices_list = list(assignment.values())

                if pattern.hook:
                    result = _resolve_hook(pattern.hook)(assignment, pins, netlist)
                    if result is None:
                        continue
                    devices_list = devices_list + result.extra_devices
                    pins = {**pins, **result.extra_pins}

                children = _match_children(pattern, assignment, prev_level_structures)
                if children is None:
                    continue

                tech_type = None
                if pattern.tech_type_from:
                    tech_type = assignment[pattern.tech_type_from].type[0]

                counter = instance_counters.setdefault(pattern.name, count())
                s = RecognizedStructure(
                    name=pattern.name,
                    category=pattern.category,
                    index=next(counter),
                    tech_type=tech_type,
                    pins=pins,
                    devices=devices_list,
                    children=children,
                )
                level_structures.append(s)

        all_structures.extend(level_structures)
        prev_level_structures = level_structures

    # Non-exclusive level-0 pass: MVP composites (no children, not exclusive)
    # These run the original single-pass algorithm unchanged.
    claimed_refs = {d.ref for s in all_structures for d in s.devices}
    for pattern in [p for p in by_level.get(0, []) if not p.exclusive]:
        for assignment in _find_assignments(pattern, netlist.devices):
            pins = _resolve_pins(pattern, assignment)
            devices_list = list(assignment.values())

            if pattern.hook:
                result = _resolve_hook(pattern.hook)(assignment, pins, netlist)
                if result is None:
                    continue
                devices_list = devices_list + result.extra_devices
                pins = {**pins, **result.extra_pins}

            tech_type = None
            if pattern.tech_type_from:
                tech_type = assignment[pattern.tech_type_from].type[0]

            counter = instance_counters.setdefault(pattern.name, count())
            s = RecognizedStructure(
                name=pattern.name,
                category=pattern.category,
                index=next(counter),
                tech_type=tech_type,
                pins=pins,
                devices=devices_list,
            )
            all_structures.append(s)
            claimed_refs.update(d.ref for d in devices_list)

    unrecognized = [d for d in netlist.devices if d.ref not in claimed_refs]
    return SubcircuitRecognitionResult(structures=all_structures, unrecognized_devices=unrecognized)
