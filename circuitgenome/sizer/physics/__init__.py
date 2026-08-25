"""Op-amp physics: everything both sizers stand on, above the device model.

The two sizing pipelines disagree about *how* to choose a geometry — CP-SAT
search over a discrete grid versus a deterministic forward pass off a measured
LUT — but not about the physics. Topology relationships (KCL, the gain product,
the Miller pole, a cascode's ``1 + gm·R`` boost) are properties of the circuit,
not of the transistor model, so they live here once:

* :mod:`.circuit_view` — the structural view a solved netlist presents
* :mod:`.taxonomy` — the slot and net naming conventions that view assumes
* :mod:`.device_model` — the one interface behind which square-law
  (:mod:`.equations`) and gm/Id (:mod:`.gmid_lut`) primitives are swappable
* :mod:`.preprocess` — currents, gm targets and compensation caps from the spec
* :mod:`.stage_chain` — the small-signal chain a solved sizing presents
* :mod:`.metrics` — predicted metrics and spec margins, as algebra over it

Nothing here decides a width. Nothing here runs a simulator.
"""
