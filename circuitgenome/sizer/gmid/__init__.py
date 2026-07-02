"""Block-based gm/Id sizing pipeline (separate from the Level-1 CP-SAT sizer).

:func:`size_gmid` runs five phases with typed hand-offs — Analyze
(:mod:`.analyze` → ``CircuitView``), Bias currents and Plan (:mod:`.plan` →
``CurrentPlan``/``SizingPlan``), Size (:mod:`.geometry`, :mod:`.bias`,
:mod:`.resistors`), and Evaluate (:mod:`.evaluate`) — driven by the explicit
per-block design intent in :mod:`.intent`.  The device primitives
(:mod:`~circuitgenome.sizer.shared.gmid_lut`,
:mod:`~circuitgenome.sizer.shared.device_model`) and the model-independent
op-amp physics (:mod:`~circuitgenome.sizer.shared.preprocess`,
:mod:`~circuitgenome.sizer.shared.metrics`) are reused from ``sizer.shared``;
the Level-1 analytical sizer is left untouched.
"""
from __future__ import annotations

from .intent import (
    DEFAULT_BLOCK_INTENTS,
    BlockIntent,
    GmIdIntent,
    TransistorIntent,
    resolve_transistor_intents,
)
from .gmid_sizer import size_gmid

__all__ = [
    "BlockIntent",
    "DEFAULT_BLOCK_INTENTS",
    "GmIdIntent",
    "TransistorIntent",
    "resolve_transistor_intents",
    "size_gmid",
]
