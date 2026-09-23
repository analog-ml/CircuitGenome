"""
``tail_current``-slot compatibility filter and pruning for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every topology has a ``tail_current`` slot, wired
``input_pair.tail -> net_tail <- tail_current.out``. The 4
``differential_pair_*`` variants use ``tail`` as the pair's common-source
node. ``rail_to_rail_complementary_input`` instead uses it as a current
reference feeding an internal NMOS diode, so it specifically requires a
VDD-side (``pmos_input``) tail source. ``inverter_based_input`` -- two
back-to-back CMOS inverters -- is self-biased and never references ``tail``,
so for that variant ``net_tail`` is a floating, single-terminal node and
``tail_current`` is synthesized dead.

Without a filter, all 6 ``tail_current`` variants would be enumerated for
every combination, but for ``inverter_based_input`` the choice between them
makes zero difference to the assembled circuit -- pure combinatorial
duplication, plus dead ``tail_current_*`` devices and an unnecessarily
"needed" bias rail 7 (``tail_current.bias``).

:func:`is_tail_current_compatible` collapses this duplication: for an
``input_pair`` that doesn't reference ``tail``, only the canonical
:data:`CANONICAL_TAIL_CURRENT_VARIANT` is allowed through.
:func:`prune_tail_current` then empties that variant's ports/devices for
those combinations, so it contributes no devices and ``tail_current.bias``
is no longer "needed" (see
:func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`).

Both the filter and the prune are required together, exactly as with
:func:`~circuitgenome.synthesizer.compatibility.cmfb.is_cmfb_compatible`/
:func:`~circuitgenome.synthesizer.compatibility.cmfb.prune_cmfb`:
``itertools.product`` enumerates all 6 ``tail_current`` variants for every
combination *before* pruning runs. If :func:`prune_tail_current` ran
unconditionally with no filter, all 6 choices for an
``inverter_based_input`` combination would each be pruned to the same empty
``tail_current_absent`` placeholder -- and since pruning happens before the
circuit name is computed, that would yield 6
:class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`\\ s with
identical devices *and* identical names for what should be one circuit.
:func:`is_tail_current_compatible` prevents this by rejecting all but
:data:`CANONICAL_TAIL_CURRENT_VARIANT` up front, so
:func:`prune_tail_current` only ever empties that one canonical choice -- 1
circuit per ``(inverter_based_input, load, bias_gen, ...)`` combination, not
6.

To extend: wire a new input pair's tail-side device terminal(s) to ``tail``
to make it a generic ``tail_current`` consumer. A specialized reference
interface such as the rail-to-rail macro needs an explicit rule here. A new
self-biased, no-tail variant is automatically treated like
``inverter_based_input``.
"""
from __future__ import annotations
import dataclasses

from ..models import ModuleVariant

CANONICAL_TAIL_CURRENT_VARIANT = "current_mirror_tail_pmos"
RAIL_TO_RAIL_INPUT_VARIANTS = {"rail_to_rail_complementary_input"}


def _input_pair_uses_tail(input_pair: ModuleVariant) -> bool:
    """Return ``True`` if any of *input_pair*'s devices reference its ``tail`` port."""
    return any("tail" in dev.terminals.values() for dev in input_pair.devices)


def is_tail_current_compatible(variant_map: dict[str, ModuleVariant]) -> bool:
    """Return ``False`` if ``tail_current``'s variant choice is irrelevant for this combination.

    If a conventional ``input_pair`` references its ``tail`` port (the 4
    ``differential_pair_*`` variants), every ``tail_current`` variant remains
    distinct. The rail-to-rail macro accepts only VDD-side sources because its
    ``tail`` port feeds an NMOS reference diode. For
    ``inverter_based_input`` -- which is self-biased and never references
    ``tail`` -- ``tail_current.out`` drives nothing, so only
    :data:`CANONICAL_TAIL_CURRENT_VARIANT` is allowed through -- the other 5
    variants would otherwise be enumerated as duplicate no-op circuits.
    """
    input_pair = variant_map["input_pair"]
    tail_current = variant_map["tail_current"]
    if input_pair.name in RAIL_TO_RAIL_INPUT_VARIANTS:
        # The macro treats `tail` as a current-reference input that feeds an
        # internal NMOS diode; it therefore needs current sourced from VDD.
        return tail_current.polarity == "pmos_input"
    if _input_pair_uses_tail(input_pair):
        return True
    return tail_current.name == CANONICAL_TAIL_CURRENT_VARIANT


def prune_tail_current(variant: ModuleVariant, input_pair: ModuleVariant) -> ModuleVariant:
    """Return an empty placeholder if *input_pair* doesn't consume ``tail_current.out``.

    If *input_pair* references its ``tail`` port, *variant* is returned
    unchanged. Otherwise, returns a copy of *variant* with no ports and no
    devices -- it contributes nothing to the assembled circuit, ``net_tail``
    is no longer left floating, and ``tail_current.bias`` is no longer
    "needed" by
    :func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`.
    """
    if _input_pair_uses_tail(input_pair):
        return variant
    return dataclasses.replace(variant, name="tail_current_absent", ports=[], devices=[])
