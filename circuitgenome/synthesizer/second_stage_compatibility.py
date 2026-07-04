"""
Stage-interface level compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

A ``second_stage`` variant can only be biased against the first stage when
the gate level its *signal device* (the transistor whose gate is the ``in``
port) requires falls inside the first stage's reachable output window
(issue #109; follower classification: issue #110). The window is set by the
input pair: an NMOS pair confines its output node to the upper part of the
supply range (its floor is the tail node, ``Vcm - Vgs_pair``;
vdd-referenced loads confine it further), a PMOS pair mirrors that low.
When the required level and the window are disjoint, no sizing can
establish the interface DC level: mirror-type loads let the feedback loop
drag the node to the boundary and pin the pair in triode; range-limited
loads rail outright.

Which level the signal device needs follows from its *source terminal*:

- **Common-source** (source on a supply): the gate sits one ``V_GS`` from
  that supply -- an NMOS CS gate is low, a PMOS CS gate is high. Suits the
  *opposite*-polarity pair (an NMOS pair's high output suits a PMOS-gate
  CS stage, and vice versa).
- **Source follower** (source on the output node): the gate sits one
  ``V_GS`` *beyond* the output, toward the device's back rail -- an NMOS
  follower's gate is high, a PMOS follower's gate is low. Suits the
  *same*-polarity pair (issue #110: the mis-wired ``common_drain`` was the
  one datum that made a blanket opposite-type rule look right).

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
whichever device gates its ``in`` port and where that device's source sits,
and a new topology is covered as long as it wires the sensing stage's ``in``
to a load output net (``load.out``/``out1``/``out2``, the canonical load
interface).
"""
from __future__ import annotations
from .models import ModuleVariant, TopologyTemplate

#: The canonical ``load`` interface's output ports (see the header of
#: ``config/opamp_topologies.yaml``): the nets they are wired to are the
#: first stage's output node(s).
_LOAD_OUTPUT_PORTS = ("out", "out1", "out2")

#: ``input_pair.polarity`` tag -> the pair transistors' channel type.
_PAIR_DEVICE_TYPE = {"nmos_input": "nmos", "pmos_input": "pmos"}


def _signal_device(variant: ModuleVariant):
    """Return *variant*'s signal device -- the transistor whose gate is the
    ``in`` port -- or ``None`` if no device gates ``in``.
    """
    for dev in variant.devices:
        if dev.terminals.get("g") == "in":
            return dev
    return None


def signal_device_type(variant: ModuleVariant) -> str | None:
    """Return the channel type (``"nmos"``/``"pmos"``) of *variant*'s signal
    device, or ``None`` if no device gates ``in``.
    """
    dev = _signal_device(variant)
    return dev.type if dev is not None else None


def required_pair_type(variant: ModuleVariant) -> str | None:
    """Return the input-pair channel type (``"nmos"``/``"pmos"``) whose
    first-stage output window can reach *variant*'s required gate level, or
    ``None`` if *variant* imposes no constraint (no device gates ``in``).

    Common-source stages (signal-device source on its back supply) need the
    opposite-type pair; followers (source on the output node) need the
    same-type pair -- see the module docstring for the electrical rationale.
    """
    dev = _signal_device(variant)
    if dev is None:
        return None
    source = dev.terminals.get("s")
    if dev.type == "nmos":
        return "pmos" if source == "gnd" else "nmos"
    if dev.type == "pmos":
        return "nmos" if source == "vdd" else "pmos"
    return None


def is_second_stage_compatible(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> bool:
    """Return ``False`` if any ``second_stage``-category slot that senses the
    first stage's output requires a gate level the ``input_pair``'s output
    window cannot reach (see the module docstring for the electrical
    rationale).
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
        if variant is None:
            continue
        required = required_pair_type(variant)
        if required is not None and required != pair_type:
            return False
    return True
