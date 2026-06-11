Extending CircuitGenome
=======================

All module variants and topology templates are defined in plain YAML files
inside the package.  No code changes are required to add new variants or
topologies — edit (or replace) the YAML and the synthesizer picks them up
automatically.

Adding a module variant
-----------------------

Open ``circuitgenome/synthesizer/config/opamp_modules.yaml`` and append an
entry under the ``modules`` list.

Required fields:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Description
   * - ``name``
     - Unique snake_case identifier.
   * - ``category``
     - One of the six canonical categories: ``input_pair``, ``load``,
       ``tail_current``, ``bias_generation``, ``compensation``,
       ``second_stage``.
   * - ``display_name``
     - Human-readable label shown in ``--list-modules``.
   * - ``ports``
     - List of port objects ``{name, role}``.  Must include all ports from
       the canonical interface for the category (see :doc:`overview`).
   * - ``devices``
     - List of device objects (see below).

Device fields:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Values / description
   * - ``ref``
     - Reference designator within the module (e.g. ``m1``, ``r2``).
   * - ``type``
     - ``nmos``, ``pmos``, ``resistor``, or ``capacitor``.
   * - MOSFET terminals
     - ``d`` (drain), ``g`` (gate), ``s`` (source), ``b`` (bulk).
   * - Resistor terminals
     - ``t1``, ``t2``.
   * - Capacitor terminals
     - ``p`` (plus), ``m`` (minus).

Terminal values are **local net names** inside the module.  Names that match
a port name are replaced by the global net during synthesis.  All other names
become internal nets prefixed with the slot name (e.g.
``input_pair_internal_node``).

Example — CMOS inverter-pair input stage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   - name: cmos_inverter_pair
     category: input_pair
     display_name: "CMOS Inverter-Pair Input Stage"
     ports:
       - {name: in1,  role: input}
       - {name: in2,  role: input}
       - {name: out1, role: output}
       - {name: out2, role: output}
       - {name: tail, role: optional}
       - {name: vdd,  role: supply}
       - {name: gnd,  role: supply}
     devices:
       - {ref: mp1, type: pmos, d: out1, g: in1, s: vdd, b: vdd}
       - {ref: mn1, type: nmos, d: out1, g: in1, s: gnd, b: gnd}
       - {ref: mp2, type: pmos, d: out2, g: in2, s: vdd, b: vdd}
       - {ref: mn2, type: nmos, d: out2, g: in2, s: gnd, b: gnd}

Port roles
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Role
     - Meaning
   * - ``input``
     - Signal input.
   * - ``output``
     - Signal output.
   * - ``supply``
     - Power rail (``vdd`` / ``gnd`` auto-connect to ``vdd!`` / ``gnd!``).
   * - ``supply_in``
     - Driven supply node (e.g. tail current output driving the diff pair).
   * - ``optional``
     - Port exists in the canonical interface but this variant does not use
       it.  Unconnected in the topology template.

Adding a topology template
--------------------------

Open ``circuitgenome/synthesizer/config/opamp_topologies.yaml`` and append an
entry under the ``topologies`` list.

Required fields:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Description
   * - ``name``
     - Unique identifier.
   * - ``config``
     - Dict with ``stages`` (int), ``output_type`` (str), and optionally
       ``compensation_scheme`` (str).  Used for filtering via
       ``synthesize(config=...)``.
   * - ``external_ports``
     - Ordered list of top-level subcircuit port names (SPICE order).
   * - ``slots``
     - List of ``{name, category}`` objects — one per module slot.
   * - ``connections``
     - List of ``{slot, port, net}`` objects mapping each module port to a
       global net name.

Supply ports (``vdd`` / ``gnd``) on every slot auto-connect to ``vdd!`` /
``gnd!`` even if not listed in ``connections``.

.. note::

   Standard 3-stage op-amps using Nested Miller (NMC) and Reversed Nested
   Miller (RNMC) compensation already ship as built-in templates —
   ``three_stage_opamp_nmc_single_ended``,
   ``three_stage_opamp_rnmc_single_ended``, and their fully-differential
   counterparts.  See :doc:`overview` for details.  The example below shows
   a third compensation arrangement (one Miller cap per stage, in cascade)
   to illustrate the general schema for adding your own variants.

Example — 3-stage op-amp with cascade (single-Miller-per-stage) compensation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   - name: three_stage_opamp_cascade_miller
     config:
       output_type: single_ended
       stages: 3
       compensation_scheme: cascade_miller
     external_ports: [ibias, in1, in2, out, vdd!, gnd!]
     slots:
       - {name: input_pair,    category: input_pair}
       - {name: load,          category: load}
       - {name: tail_current,  category: tail_current}
       - {name: bias_gen,      category: bias_generation}
       - {name: comp_1,        category: compensation}
       - {name: second_stage,  category: second_stage}
       - {name: comp_2,        category: compensation}
       - {name: third_stage,   category: second_stage}
     connections:
       - {slot: input_pair,   port: in1,  net: in1}
       - {slot: input_pair,   port: in2,  net: in2}
       - {slot: input_pair,   port: out1, net: net_diff1}
       - {slot: input_pair,   port: out2, net: net_mid1}
       - {slot: input_pair,   port: tail, net: net_tail}
       - {slot: load,         port: in1,       net: net_diff1}
       - {slot: load,         port: in2,       net: net_mid1}
       - {slot: load,         port: out1,      net: net_diff1}
       - {slot: load,         port: out2,      net: net_mid1}
       - {slot: load,         port: out,       net: net_mid1}
       - {slot: load,         port: bias1,     net: net_bias1}
       - {slot: load,         port: bias2,     net: net_bias2}
       - {slot: load,         port: bias3,     net: net_bias3}
       - {slot: load,         port: bias_cmfb, net: net_bias4}
       - {slot: tail_current, port: out,  net: net_tail}
       - {slot: tail_current, port: bias, net: net_tail_bias}
       - {slot: bias_gen,     port: ibias, net: ibias}
       - {slot: bias_gen,     port: out1,  net: net_bias1}
       - {slot: bias_gen,     port: out2,  net: net_bias2}
       - {slot: bias_gen,     port: out3,  net: net_bias3}
       - {slot: bias_gen,     port: out4,  net: net_bias4}
       - {slot: second_stage, port: in,   net: net_mid1}
       - {slot: second_stage, port: out,  net: net_mid2}
       - {slot: second_stage, port: bias, net: net_bias1}
       - {slot: comp_1,       port: in,   net: net_mid1}
       - {slot: comp_1,       port: out,  net: net_mid2}
       - {slot: third_stage,  port: in,   net: net_mid2}
       - {slot: third_stage,  port: out,  net: out}
       - {slot: third_stage,  port: bias, net: net_bias1}
       - {slot: comp_2,       port: in,   net: net_mid2}
       - {slot: comp_2,       port: out,  net: out}

Using custom files at runtime
------------------------------

Pass paths directly to the Python API:

.. code-block:: python

   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer import enumerate_circuits

   modules = load_modules("my_modules.yaml")
   topologies = load_topologies("my_topologies.yaml")

   for circuit in enumerate_circuits(topologies[0], modules):
       print(circuit.name)
