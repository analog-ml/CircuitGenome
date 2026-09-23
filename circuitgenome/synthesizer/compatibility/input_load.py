"""Compatibility between self-loading input macros and the load slot."""
from __future__ import annotations

from ..models import ModuleVariant

RAIL_TO_RAIL_INPUT = "rail_to_rail_complementary_input"
RAIL_TO_RAIL_LOAD = "rail_to_rail_load_absent"


def is_input_load_compatible(variant_map: dict[str, ModuleVariant]) -> bool:
    """Keep the rail-to-rail macro and its zero-device load adapter together.

    The macro sums complementary pair currents directly at ``out1/out2``;
    ordinary input pairs still require a real load, while the rail-to-rail
    macro must not receive one.
    """
    is_rr_input = variant_map["input_pair"].name == RAIL_TO_RAIL_INPUT
    is_rr_load = variant_map["load"].name == RAIL_TO_RAIL_LOAD
    return is_rr_input == is_rr_load
