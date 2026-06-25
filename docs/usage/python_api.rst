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
plus a :class:`~circuitgenome.sizer.shared.models.SizingSpec` and returns minimum
W/L values for every transistor.

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer import enumerate_circuits, to_flat_spice
   from circuitgenome.recognizer import parse, recognize
   from circuitgenome.recognizer.functional_block_recognizer import assign_slots
   # Public API is re-exported from the package root (internals live under
   # circuitgenome.sizer.shared / .analytical / .gmid).
   from circuitgenome.sizer import size_circuit, load_tech, SizingSpec

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
   tech = load_tech("generic")                # built-in config name, or a YAML path
   result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

   print(result.solver_status)                # e.g. "OPTIMAL" / "FEASIBLE" / "GMID"
   for ref, s in result.transistors.items():
       print(f"  {ref:30s}  W={s.w_um:.2f} µm  L={s.l_um:.2f} µm  IDS={s.ids_a*1e6:.1f} µA")
   if result.cc_pf is not None:
       print(f"  Cc = {result.cc_pf:.2f} pF")
   print(result.metrics, result.bias_feasible)

``result.metrics`` is an **analytical** estimate; for PTM the ``circuitgenome
size`` CLI measures performance in ngspice instead (see :doc:`cli`).  Pass
``--tech ptm45`` to exercise the gm/Id path.

The spec YAML file (used by the CLI) mirrors ``SizingSpec`` field names
directly.  See ``examples/two_stage_se_specs/spec_generic.yaml`` for an annotated
example.

gm/Id sizing with a foundry PDK (GF180MCU)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A technology that carries a gm/Id LUT — the PTM nodes and the **GF180MCU** foundry
PDK — sizes through the gm/Id pipeline instead of the Level-1 CP-SAT solver: the
same :func:`~circuitgenome.sizer.sizer.size_circuit` call, just a different
:func:`~circuitgenome.sizer.shared.loader.load_tech` name.  Performance is then
**measured in ngspice** (BSIM4) across process corners rather than estimated
analytically.

.. code-block:: python

   from circuitgenome.sizer import size_circuit, load_tech, SizingSpec
   from circuitgenome.sizer.shared.spice_sim import ngspice_available, simulate_metrics

   # Reuse `parsed`, `sr_result`, `fbr_result`, `topology`, `netlist_text`
   # built in the example above.
   tech = load_tech("gf180mcu")          # GF180MCU 180nm core 3.3V (gm/Id LUT)

   spec = SizingSpec(
       vdd=3.3, vss=0.0, ibias=40e-6, cl=2e-12,
       second_stage_current_ratio=2.0,
       gain_min_db=45, gbw_min_hz=8e5,
       phase_margin_min_deg=60, slew_rate_min_vps=2e5,
   )

   result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
   assert result.solver_status == "GMID"        # gm/Id pipeline (LUT present)
   for ref, s in result.transistors.items():
       print(f"  {ref:30s}  W={s.w_um:.2f} µm  L={s.l_um:.2f} µm")
   print("bias_feasible:", result.bias_feasible)

   # Measure performance in ngspice (requires ngspice on PATH). `result.metrics`
   # is only the analytical estimate; simulate_metrics gives the measured numbers.
   if ngspice_available():
       nominal = tech.spice_lib.corner                       # "typical"
       at_typ = simulate_metrics(netlist_text, result, tech, spec, corner=nominal)
       print("gain (dB), GBW (Hz) @ typical:", at_typ["gain_db"], at_typ["gbw_hz"])

       # Re-measure the sized design across every configured corner (worst-case).
       for c in tech.spice_lib.corners:                      # typical, ss, ff, sf, fs
           m = simulate_metrics(netlist_text, result, tech, spec, corner=c)
           print(c, m["gain_db"], m["gbw_hz"], m["power_w"])

The gm/Id LUT (``models/gf180mcu_gmid.npz``) is characterized at the ``typical``
corner and drives sizing; the corner loop above re-measures the *sized* design.
A :func:`~circuitgenome.sizer.shared.spice_sim.simulate_metrics` value is ``None``
when ngspice cannot extract that metric (gain/GBW/PM/slew/power are measured;
CMRR/PSRR/output-swing are not).

See :doc:`../overview` for the sizer's constraint derivation order and
CP-SAT linearisation details.
