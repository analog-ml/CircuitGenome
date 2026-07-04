"""
Stage-interface level compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

A ``second_stage`` variant whose *signal device* (the transistor whose gate
is the ``in`` port) has the **same** channel type as the input pair is
structurally unbiasable when it senses the first stage's output (issue #109):
the two windows are disjoint, so no sizing can establish the interface DC
level. An NMOS pair confines its output node to the upper part of the supply
range (its floor is the tail node, ``Vcm - Vgs_pair``; vdd-referenced loads
confine it further), but an NMOS-gate stage needs its gate a ``Vgs`` above
gnd. Mirror-type loads let the feedback loop drag the node to the boundary
and pin the pair in triode; range-limited loads rail outright. The PMOS
mirror image fails the same way, so the signal device must be the input
pair's complement: an NMOS pair's high output level suits a PMOS gate, and
vice versa.

The check is *structural* (actual device terminal references, no YAML tags,
same approach as :mod:`~circuitgenome.synthesizer.bias_construction`), and it
is scoped to the first-stage interface: only ``second_stage``-category slots
whose ``in`` net is one of the load's output nets are constrained. In the
3-stage topologies the ``third_stage`` slot (same category) senses the second
stage's output instead -- a wide-swing common-source node that can meet
either gate level -- so it is deliberately left unconstrained.

``input_pair`` variants with no ``polarity`` tag (``inverter_based_input``)
set their output level near mid-rail, reachable by either gate type, so they
impose no constraint.

To extend: nothing to tag -- a new ``second_stage`` variant is classified by
whichever device gates its ``in`` port, and a new topology is covered as long
as it wires the sensing stage's ``in`` to a load output net (``load.out``/
``out1``/``out2``, the canonical load interface).
"""
from __future__ import annotations
from .models import ModuleVariant, TopologyTemplate

#: The canonical ``load`` interface's output ports (see the header of
#: ``config/opamp_topologies.yaml``): the nets they are wired to are the
#: first stage's output node(s).
_LOAD_OUTPUT_PORTS = ("out", "out1", "out2")

#: ``input_pair.polarity`` tag -> the pair transistors' channel type.
_PAIR_DEVICE_TYPE = {"nmos_input": "nmos", "pmos_input": "pmos"}


def signal_device_type(variant: ModuleVariant) -> str | None:
    """Return the channel type (``"nmos"``/``"pmos"``) of *variant*'s signal
    device -- the transistor whose gate is the ``in`` port -- or ``None`` if
    no device gates ``in``.
    """
    for dev in variant.devices:
        if dev.terminals.get("g") == "in":
            return dev.type
    return None


def is_second_stage_compatible(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> bool:
    """Return ``False`` if any ``second_stage``-category slot that senses the
    first stage's output has a signal device of the same channel type as the
    ``input_pair`` (see the module docstring for the electrical rationale).
    """
    input_pair = variant_map.get("input_pair")
    if input_pair is None or input_pair.polarity is None:
        return True
    pair_type = _PAIR_DEVICE_TYPE.get(input_pair.polarity)
    if pair_type is None:
        return True

    load_output_nets = {
        net
        for port, net in topology.slot_connections("load").items()
        if port in _LOAD_OUTPUT_PORTS
    }
    for slot in topology.slots:
        if slot.category != "second_stage":
            continue
        if topology.slot_connections(slot.name).get("in") not in load_output_nets:
            continue  # e.g. third_stage: senses a wide-swing CS output
        variant = variant_map.get(slot.name)
        if variant is not None and signal_device_type(variant) == pair_type:
            return False
    return True
