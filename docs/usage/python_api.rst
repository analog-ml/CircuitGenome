Python API
==========

High-level API
--------------

The simplest entry point is :func:`~circuitgenome.synthesizer.synthesizer.synthesize`.
It loads the built-in YAML configs, applies your filters, and returns a list of
:class:`~circuitgenome.synthesizer.models.SynthesizedCircuit` objects.

.. code-block:: python

   from circuitgenome import synthesize
   from circuitgenome.synthesizer import to_flat_spice, to_hierarchical_spice

   # All 2-stage single-ended circuits
   circuits = synthesize({"stages": 2, "output_type": "single_ended"})
   print(f"{len(circuits)} circuits")  # 1890

   # Serialize the first circuit
   print(to_flat_spice(circuits[0], name="my_ota"))
   print(to_hierarchical_spice(circuits[0], name="my_ota_hier"))

Supported filter keys for ``synthesize(config)``:

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Key
     - Type
     - Description
   * - ``topology``
     - str
     - Exact topology name (e.g. ``"one_stage_opamp"``)
   * - ``stages``
     - int
     - Number of amplifier stages (``1``, ``2``, or ``3``)
   * - ``output_type``
     - str
     - ``"single_ended"`` or ``"fully_differential"``
   * - ``compensation_scheme``
     - str
     - 3-stage only: ``"nested_miller"`` or ``"reversed_nested_miller"``

.. code-block:: python

   # All 3-stage single-ended circuits using Reversed Nested Miller Compensation
   circuits = synthesize({
       "stages": 3,
       "output_type": "single_ended",
       "compensation_scheme": "reversed_nested_miller",
   })

Inspecting a circuit
--------------------

.. code-block:: python

   c = circuits[0]

   print(c.topology)      # "two_stage_opamp_single_ended"
   print(c.external_ports)  # ["ibias", "in1", "in2", "out", "vdd!", "gnd!"]

   for slot_name, variant in c.variant_map.items():
       print(f"  {slot_name}: {variant.display_name}")

   # Flat device list (after net substitution)
   for ref, device in c.devices:
       print(ref, device.type, device.terminals)

Streaming with ``enumerate_circuits``
--------------------------------------

For large enumerations, use the iterator API to avoid building the full list
in memory at once:

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer import enumerate_circuits, to_flat_spice
   from pathlib import Path

   modules = load_modules()
   topology = next(
       t for t in load_topologies()
       if t.name == "two_stage_opamp_single_ended"
   )

   out_dir = Path("./circuits")
   out_dir.mkdir(exist_ok=True)

   for i, circuit in enumerate(enumerate_circuits(topology, modules), start=1):
       (out_dir / f"circuit_{i:04d}.ckt").write_text(to_flat_spice(circuit))

Using custom YAML definitions
------------------------------

Pass explicit file paths to load your own module or topology definitions:

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer import enumerate_circuits

   modules = load_modules("my_modules.yaml")
   topologies = load_topologies("my_topologies.yaml")

   for circuit in enumerate_circuits(topologies[0], modules):
       print(circuit.name)

See :doc:`../extending` for the YAML schema.

Recognizer
----------

:mod:`circuitgenome.recognizer` recovers a circuit's ``variant_map`` from its
flat SPICE netlist -- the structural inverse of ``synthesize`` +
``to_flat_spice``. The current MVP covers ``one_stage_opamp`` circuits built
from ``differential_pair_nmos`` / ``active_load_pmos`` /
``current_mirror_tail_nmos`` / ``diode_connected_mosfet_bias``:

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer.synthesizer import enumerate_circuits
   from circuitgenome.synthesizer.netlist import to_flat_spice
   from circuitgenome.recognizer import parse, recognize, assign_slots

   modules = load_modules()
   topology = next(t for t in load_topologies() if t.name == "one_stage_opamp")
   circuit = next(enumerate_circuits(topology, modules))

   # Layer 0: flat SPICE -> ParsedNetlist
   parsed = parse(to_flat_spice(circuit))

   # Layer 1: ParsedNetlist -> SubcircuitRecognitionResult
   sr_result = recognize(parsed)
   assert sr_result.unrecognized_devices == []

   # Layer 2: SubcircuitRecognitionResult + TopologyTemplate -> variant_map
   fbr_result = assign_slots(sr_result, topology)

   for slot_name, variant in circuit.variant_map.items():
       recovered = fbr_result.slot_assignments[slot_name].pattern_name
       assert recovered == variant.name

See :doc:`../overview` for the recognizer's 3-layer pipeline and pattern
schema.
