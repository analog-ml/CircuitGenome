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


def orient_cmfb(variant: ModuleVariant, topology) -> ModuleVariant:
    """Return the polarity-correct CMFB orientation for *topology* (issue #165).

    The CMFB senses the external outputs (``outp``/``outn``) and drives the
    first-stage load gates, so the CM loop traverses every stage after the
    first.  Each amplification stage inverts, and all four CMFB-consuming
    loads respond the same way (``cmfb.out`` up → first-stage output CM
    down), so the loop sign depends only on the stage count:

    - **two-stage** FD (one inversion to the outputs): the loop is positive
      with the stock amp orientation — swap the sense/vref gates
      (``<name>_inverting``) so a rising output CM lowers ``cmfb.out``;
    - **three-stage** FD (two inversions): the stock orientation is already
      negative — returned unchanged.

    ``cmfb_absent`` placeholders pass through untouched.
    """
    if not variant.devices:
        return variant
    swap = _INVERTING_GATE_SWAP.get(variant.name)
    if swap is None or topology.config.get("stages") != 2:
        return variant
    devices = [
        dataclasses.replace(d, terminals={**d.terminals, "g": swap[d.ref]})
        if d.ref in swap else d
        for d in variant.devices
    ]
    return dataclasses.replace(variant, name=f"{variant.name}_inverting",
                               devices=devices)
