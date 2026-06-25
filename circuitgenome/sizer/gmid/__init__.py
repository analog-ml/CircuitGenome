"""Block-based gm/Id sizing pipeline (separate from the Level-1 CP-SAT sizer).

The gm/Id path is built from self-sizing *blocks* (input pair, gain stage, load,
tail, bias, compensation) driven by an explicit per-role region/intent config,
reusing the device primitives in :mod:`~circuitgenome.sizer.gmid_lut` /
:mod:`~circuitgenome.sizer.device_model` and the model-independent op-amp physics
in :mod:`~circuitgenome.sizer.equations`.  The Level-1 analytical sizer is left
untouched.
"""
from __future__ import annotations

from .intent import GmIdIntent
from .gmid_sizer import size_gmid

__all__ = ["GmIdIntent", "size_gmid"]
