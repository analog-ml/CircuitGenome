"""
Output-cardinality compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

``load.in1``/``in2`` (the folding nodes fed by ``input_pair.out1``/``out2``)
and ``load.out``/``out1``/``out2`` (the load's actual output node(s)) are
wired to *separate* nets, and a topology template's ``output_type``
(``"single_ended"`` or ``"fully_differential"``) determines which of the
output-side ports get a net at all:

- ``load.in1``/``in2`` are always wired, in every topology.
- ``load.out1``/``out2`` are wired to ``net_loadout1``/``net_loadout2`` only
  in ``fully_differential`` topologies (sensed by ``cmfb``/``second_stage*``/
  ``comp*``); ``single_ended`` topologies never connect them.
- ``load.out``/``out2`` are wired to the stage's single output node only in
  ``single_ended`` topologies; ``fully_differential`` topologies never connect
  ``load.out``.

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
  ``out1``/``out2`` are never wired by ``slot_connections`` (no
  ``net_loadout1``/``net_loadout2`` net is defined there), so the cascode
  device whose drain is ``out1`` (or ``out2``) becomes a floating,
  disconnected node. -> ``output_cardinality: "differential"``, compatible
  only with ``output_type: "fully_differential"``.

The other 6 ``load`` variants (resistor/active/current-source loads) declare
``out1``/``out2`` as ``alias_of: in1``/``in2`` and ``out`` as ``optional`` --
their devices reference only ``in1``/``in2``, and a net-merge pass (see
:mod:`~circuitgenome.synthesizer.net_aliasing`) collapses ``out1``/``out2``'s
assigned net back onto ``in1``/``in2``'s after assembly, regardless of
``output_type``. They're therefore untagged (``output_cardinality`` is
``None``) and compatible with every topology.

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
