"""
Half-circuit symmetry filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Fully-differential templates build the positive and negative output paths
from separate ``<name>_p``/``<name>_n`` slots (``second_stage_p``/``_n``,
``third_stage_p``/``_n``, ``output_stage_p``/``_n``), each filled
independently from the same category pool. A mixed pairing -- e.g.
``third_stage_p = common_source_nmos`` with ``third_stage_n =
common_source_pmos``, or a PMOS follower on one output and an NMOS follower
on the other -- is not a differential amplifier: the two halves sit at
different DC levels, so at zero differential input the CMFB centres the
*average* output CM while the outputs split ~2.3 V apart (issue #208, gf180),
and the SPICE ``.op`` gate rejects it.

:func:`is_half_symmetric` requires every ``_p``/``_n`` pair of
``amplification_stage``/``output_stage`` slots to use the same variant.
Compensation slots are exempt: ``comp_p != comp_n`` changes only the AC
network, not the DC operating point.
"""
from __future__ import annotations

from ..models import ModuleVariant, TopologyTemplate

_SYMMETRIC_CATEGORIES = ("amplification_stage", "output_stage")


def is_half_symmetric(topology: TopologyTemplate,
                      variant_map: dict[str, ModuleVariant]) -> bool:
    """Return ``False`` if a ``_p``/``_n`` stage-slot pair uses different variants."""
    for slot in topology.slots:
        if slot.category not in _SYMMETRIC_CATEGORIES or not slot.name.endswith("_p"):
            continue
        p = variant_map.get(slot.name)
        n = variant_map.get(slot.name[:-2] + "_n")
        if p is not None and n is not None and p.name != n.name:
            return False
    return True
