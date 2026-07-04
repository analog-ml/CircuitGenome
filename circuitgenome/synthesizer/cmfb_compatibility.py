"""
CMFB-slot compatibility filter and pruning for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``fully_differential`` topology has a ``cmfb`` slot, wired
``cmfb.out -> net_cmfb_out -> load.bias_cmfb``. But only 2 of the 12 ``load``
variants -- ``folded_cascode_load_{nmos,pmos}_input_differential_output``, the
two tagged ``output_cardinality: "differential"`` -- declare ``bias_cmfb`` as
``role: input`` and actually reference it from a device terminal (gating
``mn3``/``mn4`` or ``mp1``/``mp2``). The other 10 ``load`` variants declare
``bias_cmfb`` as ``role: optional`` and never reference it, so
``net_cmfb_out`` drives nothing for those combinations.

Without a filter, both ``cmfb`` variants (``resistive_sense_cmfb``/
``dda_cmfb``) would be enumerated for every combination, but for the 10
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

from .models import ModuleVariant

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
