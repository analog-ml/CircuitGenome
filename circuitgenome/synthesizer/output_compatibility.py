"""
Output-cardinality compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

A topology template's ``output_type`` (``"single_ended"`` or
``"fully_differential"``) determines how two of the ``load`` slot's ports are
wired:

- ``load.out1``/``out2`` are wired to the same nets as ``load.in1``/``in2``
  (the input pair's outputs) in *every* topology -- this is fixed, unconditional
  wiring.
- ``load.out`` is wired to the stage's single output node only in
  ``single_ended`` topologies; ``fully_differential`` topologies never connect
  it.

Two groups of ``load`` variants declare a *mandatory* port whose net
assignment depends on which of these rules applies:

- ``folded_cascode_load_*_input_single_output`` and
  ``telescopic_cascode_load_{pmos,nmos}`` declare ``out`` as
  ``role: output`` (mandatory) and ``out1``/``out2`` as ``role: optional``
  (unused -- those branches have no separate cascode-output device). In a
  ``fully_differential`` topology, ``out`` is never wired by
  ``slot_connections``, so the device terminal falls back to the
  internal-node naming and becomes a floating, disconnected node.
  -> ``output_cardinality: "single"``, compatible only with
  ``output_type: "single_ended"``.

- ``folded_cascode_load_*_input_differential_output`` declare ``out1``/
  ``out2`` as ``role: output`` (mandatory, distinct cascode-output nodes) and
  ``out`` as ``role: optional`` (unused). In a ``single_ended`` topology,
  ``out1``/``out2`` are wired to the same nets as ``in1``/``in2`` (per the
  unconditional rule above), so the cascode device whose drain is ``out1``
  and source is ``in1`` ends up with drain == source -- a shorted,
  degenerate device. -> ``output_cardinality: "differential"``, compatible
  only with ``output_type: "fully_differential"``.

The other 6 ``load`` variants (resistor/active/current-source loads) declare
``out1``/``out2`` as ``alias_of: in1``/``in2`` and ``out`` as ``optional`` --
they have no mandatory port whose net assignment depends on ``output_type``,
so they're untagged (``output_cardinality`` is ``None``) and compatible with
every topology.

To extend: tag new or edited ``load`` variants with
``output_cardinality: "single"`` / ``"differential"`` in
``opamp_modules.yaml`` -- no code changes needed here.
"""
from __future__ import annotations
from .models import ModuleVariant, TopologyTemplate

_CARDINALITY_OUTPUT_TYPE = {
    "single": "single_ended",
    "differential": "fully_differential",
}


def is_output_type_compatible(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> bool:
    """Return ``False`` if ``load``'s ``output_cardinality`` (if set) doesn't
    match *topology*'s ``output_type``.

    ``load`` is the only slot with an ``output_cardinality`` tag; a load with
    no tag (``None``) is compatible with every topology.
    """
    load = variant_map["load"]
    if load.output_cardinality is None:
        return True
    return topology.config.get("output_type") == _CARDINALITY_OUTPUT_TYPE[load.output_cardinality]
