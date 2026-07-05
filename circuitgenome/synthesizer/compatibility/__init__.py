"""
Cross-slot compatibility filters for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Each submodule owns one slot's rule for rejecting variant combinations that
assemble into a non-functional or duplicate circuit. Two shapes recur:

- **pure filters** (``polarity``, ``output``, ``load_branch``,
  ``second_stage``, ``compensation``) -- an ``is_*_compatible`` predicate that
  drops a combination before it is ever assembled.
- **filter + prune pairs** (``cmfb``, ``tail_current``) -- the filter collapses
  combinatorial duplication down to a single canonical variant, and the paired
  ``prune_*`` then empties that variant's ports/devices so it contributes no
  dead devices and stops "needing" its bias rail (see
  :func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`).

Checks are either *structural* (they inspect real device-terminal references,
e.g. ``load_branch``, ``second_stage``, ``compensation``) or *tag-based* (they
read declared fields from ``opamp_modules.yaml``, e.g. ``polarity``,
``cmfb``'s ``output_cardinality``). This package is the single import surface;
submodule layout is an internal detail.
"""
from __future__ import annotations

from .cmfb import CANONICAL_CMFB_VARIANT, is_cmfb_compatible, prune_cmfb
from .compensation import is_compensation_compatible, stage_inversions
from .load_branch import is_load_branch_compatible, untapped_branch_is_dc_defined
from .output import is_output_type_compatible
from .polarity import is_combination_valid
from .second_stage import (
    is_second_stage_compatible,
    required_pair_type,
    signal_device_type,
)
from .tail_current import (
    CANONICAL_TAIL_CURRENT_VARIANT,
    is_tail_current_compatible,
    prune_tail_current,
)

__all__ = [
    "CANONICAL_CMFB_VARIANT",
    "is_cmfb_compatible",
    "prune_cmfb",
    "is_compensation_compatible",
    "stage_inversions",
    "is_load_branch_compatible",
    "untapped_branch_is_dc_defined",
    "is_output_type_compatible",
    "is_combination_valid",
    "is_second_stage_compatible",
    "required_pair_type",
    "signal_device_type",
    "CANONICAL_TAIL_CURRENT_VARIANT",
    "is_tail_current_compatible",
    "prune_tail_current",
]
