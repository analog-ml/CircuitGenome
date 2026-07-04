"""
Compensation inversion-parity compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``compensation`` variant in the library is Miller-family: it couples
its ``in`` port to its ``out`` port through a capacitor (directly, through a
nulling resistor, or via an internal series node). Wired across a stage
chain, that coupling is *negative* feedback -- pole splitting -- only when
the wrapped chain is inverting. Around a non-inverting chain with gain the
same capacitor is *positive* feedback: the AC response develops a
right-half-plane character and gain/GBW/PM become unmeasurable (issue #114:
``differential_ota_second_stage``, two cascaded common-source stages, PM
measured 270-281 deg with every compensation variant).

The parity of a chain is the number of common-source inversions along its
``in -> out`` device path:

- a **common-source hop** (signal enters a gate, exits the drain) inverts;
- a **follower hop** (signal enters a gate, exits the source) does not
  invert -- and contributes no gain, so a Miller capacitor around a pure
  follower chain is bootstrapped to ``C*(1-A) ~ 0``: useless but benign,
  NOT positive feedback. Followers are therefore deliberately allowed
  (a strict odd-parity rule would ban them and undo issue #110).

The reject condition is a chain whose total inversion count is a **positive
even number** -- non-inverting *with* gain. This also covers composite
chains: in the nested-Miller 3-stage topologies ``comp1`` wraps the
second+third stage cascade (``net_mid1 -> out``), where two common-source
stages compose to the same positive-feedback structure (standard NMC
requires a non-inverting second stage and an inverting output stage).

The check is *structural* (actual device terminal references, no YAML
tags, same approach as :mod:`~circuitgenome.synthesizer.second_stage_compatibility`):
a stage variant's inversion count is computed by walking gate-to-drain /
gate-to-source hops from its ``in`` port, and chains are composed by
following ``second_stage``-category slots' ``in``/``out`` nets between the
compensation slot's ``in`` and ``out`` nets. Anything unclassifiable (a
compensation variant that does not couple ``in`` to ``out``, a chain the
walk cannot follow, a stage with no device gating ``in``) imposes no
constraint.
"""
from __future__ import annotations
from .models import ModuleVariant, TopologyTemplate

_MOS_TYPES = ("nmos", "pmos")


def _couples_in_out(variant: ModuleVariant) -> bool:
    """Return ``True`` if *variant*'s devices form a connected path between
    its ``in`` and ``out`` ports (all current compensation variants do).
    """
    adjacency: dict[str, set[str]] = {}
    for dev in variant.devices:
        nets = set(dev.terminals.values())
        for net in nets:
            adjacency.setdefault(net, set()).update(nets)
    frontier, seen = ["in"], {"in"}
    while frontier:
        net = frontier.pop()
        if net == "out":
            return True
        for nxt in adjacency.get(net, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


def stage_inversions(variant: ModuleVariant) -> int | None:
    """Return the number of common-source inversions along *variant*'s
    ``in -> out`` device chain, or ``None`` if the chain cannot be followed
    (no device gates ``in``, a dangling hop, or a cycle).

    Each hop enters a MOSFET gate; leaving through the drain counts one
    inversion (common source), leaving through the source counts none
    (follower). ``common_source``/``common_source_pmos`` -> 1,
    ``common_drain``/``common_drain_nmos`` -> 0,
    ``differential_ota_second_stage`` -> 2.
    """
    net = "in"
    inversions = 0
    seen: set[str] = set()
    while True:
        if net in seen:
            return None
        seen.add(net)
        dev = next(
            (
                d
                for d in variant.devices
                if d.type in _MOS_TYPES and d.terminals.get("g") == net
            ),
            None,
        )
        if dev is None:
            return None
        if dev.terminals.get("s") == "out":
            return inversions
        drain = dev.terminals.get("d")
        if drain is None:
            return None
        inversions += 1
        if drain == "out":
            return inversions
        net = drain


def is_compensation_compatible(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> bool:
    """Return ``False`` if any ``compensation`` slot that couples its ``in``
    and ``out`` nets wraps a stage chain whose total inversion count is a
    positive even number -- non-inverting with gain, i.e. positive feedback
    through the compensation network (see the module docstring for the
    electrical rationale).
    """
    stage_by_in_net: dict[str, tuple[str, str]] = {}
    for slot in topology.slots:
        if slot.category != "second_stage":
            continue
        conns = topology.slot_connections(slot.name)
        if "in" in conns and "out" in conns:
            stage_by_in_net[conns["in"]] = (slot.name, conns["out"])

    for slot in topology.slots:
        if slot.category != "compensation":
            continue
        variant = variant_map.get(slot.name)
        if variant is None or not _couples_in_out(variant):
            continue
        conns = topology.slot_connections(slot.name)
        in_net, out_net = conns.get("in"), conns.get("out")
        if in_net is None or out_net is None:
            continue

        inversions = 0
        net = in_net
        visited: set[str] = set()
        classifiable = True
        while net != out_net:
            if net in visited or net not in stage_by_in_net:
                classifiable = False
                break
            visited.add(net)
            stage_name, stage_out = stage_by_in_net[net]
            stage_variant = variant_map.get(stage_name)
            stage_inv = (
                stage_inversions(stage_variant)
                if stage_variant is not None
                else None
            )
            if stage_inv is None:
                classifiable = False
                break
            inversions += stage_inv
            net = stage_out
        if classifiable and inversions > 0 and inversions % 2 == 0:
            return False
    return True
