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
flat SPICE netlist — the structural inverse of ``synthesize`` + ``to_flat_spice``.
It supports all seven topology templates via a 3-layer pipeline:

**Topology mode** — requires a known topology, recovers the exact ``variant_map``:

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer.synthesizer import enumerate_circuits
   from circuitgenome.synthesizer.netlist import to_flat_spice
   from circuitgenome.recognizer import parse, recognize, assign_slots

   modules = load_modules()
   topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
   circuit = next(enumerate_circuits(topology, modules))

   parsed = parse(to_flat_spice(circuit))
   sr_result = recognize(parsed)
   assert sr_result.unrecognized_devices == []

   fbr_result = assign_slots(sr_result, topology)

   for slot_name, variant in circuit.variant_map.items():
       recovered = fbr_result.slot_assignments[slot_name].pattern_name
       assert recovered == variant.name

**Topology-free mode** — no topology needed; groups structures by functional block:

.. code-block:: python

   from circuitgenome.recognizer import parse, recognize, group_by_category

   parsed = parse(netlist_text)
   sr_result = recognize(parsed)
   fbr_result = group_by_category(sr_result, parsed)

   for circuit_block, categories in fbr_result.groups.items():
       for category, candidates in categories.items():
           print(f"[{circuit_block}] {category}: {candidates[0].name}")

See :doc:`../overview` for the recognizer's 3-layer pipeline, pattern schema,
and topology-free disambiguation algorithm.

Initial Sizer
-------------

The sizer takes the FBR result (from :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`)
plus a :class:`~circuitgenome.sizer.models.SizingSpec` and returns minimum
W/L values for every transistor.

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer import enumerate_circuits, to_flat_spice
   from circuitgenome.recognizer import parse, recognize
   from circuitgenome.recognizer.functional_block_recognizer import assign_slots
   from circuitgenome.sizer import size_circuit
   from circuitgenome.sizer.models import SizingSpec
   from circuitgenome.sizer.loader import load_tech

   # 1. Build / load a netlist and run SR + FBR
   topology = next(
       t for t in load_topologies()
       if t.name == "two_stage_opamp_single_ended"
   )
   circuit = next(enumerate_circuits(topology, load_modules()))
   netlist_text = to_flat_spice(circuit)

   parsed = parse(netlist_text)
   sr_result = recognize(parsed)
   fbr_result = assign_slots(sr_result, topology)

   # 2. Define performance specification
   spec = SizingSpec(
       vdd=5.0,
       vss=0.0,
       ibias=10e-6,          # 10 µA tail current
       cl=20e-12,            # 20 pF load
       second_stage_current_ratio=2.5,
       gain_min_db=80,
       gbw_min_hz=2.5e6,     # 2.5 MHz
       phase_margin_min_deg=60,
       slew_rate_min_vps=3.5e6,  # 3.5 V/µs
       power_max_w=1e-3,
   )

   # 3. Load technology and run the sizer
   tech = load_tech("generic_parameterized")
   result = size_circuit(parsed, sr_result, fbr_result, topology, spec, tech)

   print(result.status)          # "OPTIMAL"
   for ref, (w_um, l_um) in result.sizes_um.items():
       print(f"  {ref:30s}  W={w_um:.2f} µm  L={l_um:.2f} µm")
   print(f"  Cc = {result.cc_pf:.2f} pF")

The spec YAML file (used by the CLI) mirrors ``SizingSpec`` field names
directly.  See ``examples/spec_two_stage_opamp.yaml`` for an annotated
example.

See :doc:`../overview` for the sizer's constraint derivation order and
CP-SAT linearisation details.
