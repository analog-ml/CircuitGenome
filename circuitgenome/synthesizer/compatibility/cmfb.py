"""
CMFB-slot compatibility filter and pruning for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``fully_differential`` topology has a ``cmfb`` slot, wired
``cmfb.out -> net_cmfb_out -> load.bias_cmfb``. But only 4 of the 12 ``load``
variants -- ``folded_cascode_load_{nmos,pmos}_input_differential_output``
(gating ``mn3``/``mn4`` or ``mp1``/``mp2``) and
``current_source_load_{pmos,nmos}`` (gating both branch devices, issue #112),
the four tagged ``output_cardinality: "differential"`` -- declare
``bias_cmfb`` as ``role: input`` and actually reference it from a device
terminal. The other 8 ``load`` variants declare ``bias_cmfb`` as
``role: optional`` and never reference it, so ``net_cmfb_out`` drives nothing
for those combinations.

Without a filter, both ``cmfb`` variants (``resistive_sense_cmfb``/
``dda_cmfb``) would be enumerated for every combination, but for the 8
non-``"differential"`` loads the choice between them makes zero difference to
the assembled circuit -- pure combinatorial duplication, plus 7-8 dead
``cmfb_*`` devices and an unnecessarily "needed" bias rail 4 (``cmfb.bias``).

:func:`is_cmfb_compatible` collapses this duplication: for a ``load`` whose
``output_cardinality`` isn't ``"differential"``, only the canonical
:data:`CANONICAL_CMFB_VARIANT` is allowed through. :func:`prune_cmfb` then
empties that variant's ports/devices for those combinations, so it
contributes no devices and ``cmfb.bias`` is no longer "needed" (see
:func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`).

To extend: tag a new or edited ``load`` variant with
``output_cardinality: "differential"`` (and give it a real
``bias_cmfb: role: input`` consumer) to make it a genuine ``cmfb`` consumer --
no code changes needed here.
"""
from __future__ import annotations
import dataclasses

from ..models import ModuleVariant
from .compensation import stage_inversions

CANONICAL_CMFB_VARIANT = "resistive_sense_cmfb"
_CMFB_CONSUMING_CARDINALITY = "differential"


def is_cmfb_compatible(variant_map: dict[str, ModuleVariant]) -> bool:
    """Return ``False`` if ``cmfb``'s variant choice is irrelevant for this combination.

    Topologies without a ``cmfb`` slot are unaffected. For a ``load`` whose
    ``output_cardinality`` is ``"differential"``, both ``cmfb`` variants drive
    a real ``bias_cmfb`` consumer and remain distinct. For every other
    ``load``, ``cmfb.out`` drives nothing, so only
    :data:`CANONICAL_CMFB_VARIANT` is allowed through -- the other variant
    would otherwise be enumerated as a duplicate no-op circuit.
    """
    if "cmfb" not in variant_map:
        return True
    if variant_map["load"].output_cardinality == _CMFB_CONSUMING_CARDINALITY:
        return True
    return variant_map["cmfb"].name == CANONICAL_CMFB_VARIANT


def prune_cmfb(variant: ModuleVariant, load: ModuleVariant) -> ModuleVariant:
    """Return an empty placeholder if *load* doesn't consume ``cmfb.out``.

    If *load*'s ``output_cardinality`` is ``"differential"``, *variant* is
    returned unchanged. Otherwise, returns a copy of *variant* with no ports
    and no devices -- it contributes nothing to the assembled circuit, and
    ``cmfb.bias`` is no longer "needed" by
    :func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`.
    """
    if load.output_cardinality == _CMFB_CONSUMING_CARDINALITY:
        return variant
    return dataclasses.replace(variant, name="cmfb_absent", ports=[], devices=[])


# Per-variant gate rewiring that flips the CMFB amplifier's comparison
# polarity: the sensed CM moves to the mirror-output side and vref to the
# diode side, so a rising sensed CM *lowers* cmfb.out.
_INVERTING_GATE_SWAP: dict[str, dict[str, str]] = {
    "resistive_sense_cmfb": {"m1": "vref", "m2": "sense"},
    "dda_cmfb": {"m1": "vref", "m2": "in1", "m3": "vref", "m4": "in2"},
}


def _cm_loop_inversions(topology, variant_map: dict[str, ModuleVariant]) -> int | None:
    """Return the total common-source inversion count along the stage chain
    from the first-stage (``load``) outputs to the CMFB's sensed nets, or
    ``None`` if the chain cannot be classified.

    Walks ``amplification_stage``/``output_stage`` slots by their ``in``/
    ``out`` nets (same composition as the compensation parity filter) from
    ``load.out1``/``out2`` until a sensed net (``cmfb.in1``/``in2``) is
    reached, summing each traversed variant's
    :func:`~.compensation.stage_inversions`.
    """
    cmfb_conns = topology.slot_connections("cmfb")
    sense_nets = {cmfb_conns.get("in1"), cmfb_conns.get("in2")} - {None}
    load_conns = topology.slot_connections("load")
    stage_by_in_net: dict[str, tuple[str, str]] = {}
    for slot in topology.slots:
        if slot.category not in ("amplification_stage", "output_stage"):
            continue
        conns = topology.slot_connections(slot.name)
        if "in" in conns and "out" in conns:
            stage_by_in_net[conns["in"]] = (slot.name, conns["out"])

    for start in (load_conns.get("out1"), load_conns.get("out2")):
        inversions, net = 0, start
        visited: set[str] = set()
        while net is not None and net not in sense_nets:
            if net in visited or net not in stage_by_in_net:
                net = None
                break
            visited.add(net)
            stage_name, stage_out = stage_by_in_net[net]
            stage_variant = variant_map.get(stage_name)
            stage_inv = (stage_inversions(stage_variant)
                         if stage_variant is not None else None)
            if stage_inv is None:
                net = None
                break
            inversions += stage_inv
            net = stage_out
        if net is not None:
            return inversions
    return None


def orient_cmfb(variant: ModuleVariant, topology,
                variant_map: dict[str, ModuleVariant]) -> ModuleVariant:
    """Return the polarity-correct CMFB orientation for *topology* (issue #165).

    The CMFB senses the external outputs (``outp``/``outn``) and drives the
    first-stage load gates, so the CM loop traverses every stage after the
    first.  All four CMFB-consuming loads respond the same way (``cmfb.out``
    up → first-stage output CM down), so the loop sign depends on the *net
    inversion parity* of the chosen stage chain (:func:`_cm_loop_inversions`
    over *variant_map* — NOT the stage count: the NMC three-stage chain is
    ``noninverting_stage`` + common source, the same odd parity as a
    two-stage despite its three stages):

    - **odd** parity (net-inverting chain — two-stage, NMC three-stage): the
      loop is positive with the stock amp orientation — swap the sense/vref
      gates (``<name>_inverting``) so a rising output CM lowers ``cmfb.out``;
    - **even** parity (RNMC three-stage's CS+CS chain): the stock orientation
      is already negative — returned unchanged.

    An unclassifiable chain falls back to the stage-count rule (two-stage →
    inverting).  ``cmfb_absent`` placeholders pass through untouched.
    """
    if not variant.devices:
        return variant
    swap = _INVERTING_GATE_SWAP.get(variant.name)
    if swap is None:
        return variant
    inversions = _cm_loop_inversions(topology, variant_map)
    if inversions is None:
        inversions = 1 if topology.config.get("stages") == 2 else 2
    if inversions % 2 == 0:
        return variant
    devices = [
        dataclasses.replace(d, terminals={**d.terminals, "g": swap[d.ref]})
        if d.ref in swap else d
        for d in variant.devices
    ]
    return dataclasses.replace(variant, name=f"{variant.name}_inverting",
                               devices=devices)
