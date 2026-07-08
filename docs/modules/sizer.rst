Sizer
=====

Overview
--------

The **Sizer (SZ)** takes an FBR slot assignment plus a performance specification and
returns minimum transistor W/L values for every device in the circuit.  It
supports every op-amp topology template the synthesizer produces and targets DC
specs — gain, GBW, phase margin, slew rate, CMRR, power, and output swing.

The sizer has two paths, selected by technology:

- The card-less ``generic`` tech uses the **analytical Level-1 sizer**: a
  square-law device model whose ``gm`` constraints linearise into an integer
  program solved with **OR-Tools CP-SAT**.
- PTM nodes and foundry PDKs (e.g. GF180MCU) use the **gm/Id sizer**, which
  chooses geometry deterministically from a SPICE-characterised gm/Id lookup
  table, capturing moderate/weak-inversion and short-channel behaviour the
  square law misses.

Both paths are described below and derived in full on their theory pages.  Every
sized design is then checked in **ngspice** (see `SPICE verification`_).

Entry points
------------

- :func:`~circuitgenome.sizer.sizer.size_circuit` — size a circuit against a
  :class:`~circuitgenome.sizer.shared.models.SizingSpec`.
- :func:`~circuitgenome.sizer.shared.loader.load_tech` /
  :func:`~circuitgenome.sizer.shared.loader.load_spec` — load a technology
  config or a performance spec.

Performance specification
-------------------------

:class:`~circuitgenome.sizer.shared.models.SizingSpec` bundles the **operating
point** (supply, bias, load, per-stage current ratios) with the **performance
targets** the sizer solves against:

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Field
     - Unit
     - Description
   * - ``vdd`` / ``vss``
     - V
     - Supply rails
   * - ``ibias``
     - A
     - Tail bias current (each input device carries ``ibias/2``)
   * - ``cl``
     - F
     - Output load capacitance
   * - ``second_stage_current_ratio``
     - —
     - ``iDS_2 = ratio × ibias`` (default 2.0)
   * - ``third_stage_current_ratio``
     - —
     - ``iDS_3 = ratio × ibias`` (three-stage only; default 5.0)
   * - ``gain_min_db``
     - dB
     - Minimum open-loop DC voltage gain
   * - ``gbw_min_hz``
     - Hz
     - Minimum unity-gain bandwidth
   * - ``phase_margin_min_deg``
     - °
     - Minimum phase margin (dominant-pole model)
   * - ``slew_rate_min_vps``
     - V/s
     - Minimum slew rate (``ibias / Cc``)
   * - ``cmrr_min_db``
     - dB
     - Minimum common-mode rejection ratio
   * - ``power_max_w``
     - W
     - Maximum quiescent power
   * - ``output_swing_max_v`` / ``output_swing_min_v``
     - V
     - Output voltage swing limits

Analytical Sizer
----------------

The card-less ``generic`` tech sizes with a **Level-1 (Shichman-Hodges)
square-law model**.  Because each device's ``IDS`` is fixed by KCL and the bias
current before any geometry is chosen, the nonlinear ``gm ≥ gm_req`` constraint
linearises to ``2·µCox·IDS·W ≥ gm_req²·L`` — a linear constraint over the
discrete W/L grid, solved for minimum gate area with **OR-Tools CP-SAT**.  The
required transconductances are derived in a fixed CMRR → SR → GBW → gain → PM
order so the specs stay mutually consistent after the integer grid rounds values
up.

See :doc:`../theory/sizing_flow` for the full derivation, the CP-SAT integer
linearisation, the CMRR/GBW/SR compatibility limits, and a worked numerical
example.

gm/Id Sizer
-----------

PTM nodes and foundry PDKs size through the **gm/Id pipeline** instead.  With
``IDS`` fixed by KCL and a ``gm/Id`` target chosen per device, a
SPICE-characterised lookup table turns ``IDS/W`` straight into ``W`` — geometry
is *computed* in a single deterministic forward pass rather than searched, so it
captures the moderate/weak-inversion and short-channel behaviour the square law
misses.  A PTM/SPICE-model node without a gm/Id LUT raises
``UnsupportedTechError``.

See :doc:`../theory/gmid_sizing_flow` for the five-phase pipeline, the role vs
functional-building-block device tagging that drives the per-device ``gm/Id``
choice, and runnable per-phase snippets.

Supported technologies
----------------------

The sizer reads its device parameters from a technology YAML, selected with
``circuitgenome size --tech <file>`` (default: the built-in
``tech_generic``).  Built-in configs live in
``circuitgenome/sizer/shared/config/``:

.. list-table::
   :header-rows: 1
   :widths: 32 16 52

   * - Config
     - Node
     - Notes
   * - ``tech_generic``
     - ~0.25 µm
     - Illustrative defaults; the built-in fallback.  Sizes with the analytical
       Level-1 path.
   * - ``tech_ptm45``
     - 45 nm
     - Planar-bulk BSIM4 from the ASU Predictive Technology Model (see
       :doc:`../references`).  Sizes through the gm/Id pipeline from a
       SPICE-characterised gm/Id LUT; ships ``models/ptm45_gmid.npz``.
   * - ``tech_gf180mcu``
     - 180 nm
     - GlobalFoundries **GF180MCU** open PDK, 3.3 V core (``nmos_3p3``/``pmos_3p3``).
       A foundry PDK: devices are subcircuits and a process corner is selected with
       ``.lib <file> <corner>``.  Sizes from a gm/Id LUT (characterized at the
       ``typical`` corner); ships ``models/gf180mcu_gmid.npz``.

A PTM node or foundry PDK sizes from its gm/Id LUT (LUT-accurate
``gm``/``gds``/``Vdsat`` from the BSIM4 device), while the card-less ``generic``
tech uses *effective* Level-1 square-law fits.  FinFET nodes (≤16 nm in silicon)
need a different device model and are not covered.  Add another PTM node — or
regenerate an existing one's LUT — with ``tools/extract_tech.py`` (requires
ngspice); see :doc:`../references` for the ASU Predictive Technology Model
citation.

SPICE verification
------------------

Analytical and gm/Id sizing are both **model-based, first-order estimates**, so
every sized design is checked in **ngspice** before it is trusted: to confirm it
actually establishes its DC bias point, and to measure the real metrics on the
device model rather than reading them back from the sizing formulas.  ngspice
runs in two roles, using the model from the tech: a BSIM4 ``.pm`` card for the
PTM nodes (``spice_model``), a foundry corner library for a PDK (``spice_lib`` →
``.lib "<file>" <corner>``, e.g. GF180MCU), or a synthesised Level-1 ``.model``
from ``mu_cox``/``vth``/``lam`` for ``generic``:

* **PTM and foundry PDKs (default report).**  For a node with a real device model,
  ``circuitgenome size`` reports ngspice-**measured** metrics directly (BSIM4),
  grounded by a SPICE DC bias-soundness check that yields the INFEASIBLE /
  MARGINAL / FEASIBLE verdict.  ngspice is **required** here — the command errors
  if it is missing.  A foundry PDK additionally re-measures the sized design across
  its configured process corners (``{typical, ss, ff, sf, fs}`` for GF180MCU) and
  prints a corner-verification table; sizing itself stays at the nominal corner.
* **``--simulate`` (generic cross-check).**  On the Level-1 ``generic`` tech,
  ``circuitgenome size --simulate`` prints the analytical metrics next to the
  SPICE-measured ones with the delta — a sanity check on the formulas.  It is
  redundant for PTM / PDK techs (already SPICE-measured).

Measurement is **best-effort**, not sign-off.  Gain/GBW/PM come from an open-loop
AC-coupled-feedback testbench; power from the DC operating point; slew rate from a
unity-gain pulse (the min of the rising and falling edges); output swing from a
unity-buffer DC sweep; CMRR and PSRR+ from the same feedback loop with the AC
stimulus riding on the input common mode / the positive supply.  Single-ended
op-amps are the most robust; fully-differential AC metrics (which depend on the
on-chip CMFB operating point), the single-ended-only swing/slew benches on FD
circuits, and any non-converging measurement are reported as ``n/a`` rather than
as wrong numbers.

Example output
--------------

A two-stage single-ended op-amp sized on the ``generic`` tech::

    circuitgenome size circuit_0001_flat.ckt \
        --topology two_stage_opamp_single_ended \
        --spec examples/two_stage_se_specs/spec_generic.yaml

.. code-block:: text

   Netlist: circuit_0001_flat.ckt  |  Topology: two_stage_opamp_single_ended
   Tech: generic_parameterized

   Solver: OPTIMAL
   ⚠ second-stage gm requirement exceeds the weak-inversion ceiling — increase second_stage_current_ratio/ibias or relax gain.

   Transistor sizing:
     m1_input_pair                   W=9.000µm  L=1.000µm  IDS=5.00µA  VGS=-0.611V  VDS_sat=0.111V
     m2_input_pair                   W=9.000µm  L=1.000µm  IDS=5.00µA  VGS=-0.611V  VDS_sat=0.111V
     m1_tail_current                 W=1.000µm  L=1.000µm  IDS=10.00µA  VGS=-0.971V  VDS_sat=0.471V
     m2_tail_current                 W=1.000µm  L=1.000µm  IDS=10.00µA  VGS=-0.971V  VDS_sat=0.471V
     mn1_second_stage                W=29.000µm  L=1.000µm  IDS=25.00µA  VGS=0.580V  VDS_sat=0.080V
     mp1_second_stage                W=5.000µm  L=1.000µm  IDS=25.00µA  VGS=-0.833V  VDS_sat=0.333V
     mnref_bias_gen                  W=1.000µm  L=1.000µm  IDS=10.00µA  VGS=0.772V  VDS_sat=0.272V
     mn5_bias_gen                    W=1.000µm  L=1.000µm  IDS=10.00µA  VGS=0.772V  VDS_sat=0.272V
     mp5_bias_gen                    W=2.000µm  L=1.000µm  IDS=10.00µA  VGS=-0.833V  VDS_sat=0.333V
     mn7_bias_gen                    W=1.000µm  L=1.000µm  IDS=10.00µA  VGS=0.772V  VDS_sat=0.272V
     Cc = 2.9pF
     r1_load                         R=130.00kΩ
     r2_load                         R=130.00kΩ

   Feasibility: MARGINAL — biases, but does not meet spec (see ⚠ above)

   Performance metrics:
     Open-loop gain         63.94 dB          [spec ≥ 80.00 dB]               margin -16.06 dB  ✗
     GBW                    2.51 MHz          [spec ≥ 2.50 MHz]               margin +0.01 MHz  ✓
     Phase margin           63.25 °           [spec ≥ 60.00 °]                margin +3.25 °  ✓
     Slew rate              3.50 V/µs         [spec ≥ 3.50 V/µs]              margin +0.00 V/µs  ✓
     Quiescent power        0.43 mW           [spec ≤ 1.00 mW]                margin +0.57 mW  ✓
     Output swing max       4.67 V            [spec ≥ 4.60 V]                 margin +0.07 V  ✓
     Output swing min       0.08 V            [spec ≤ 0.40 V]                 margin +0.32 V  ✓
     CMRR                   39.08 dB
     PSRR+                  53.98 dB

The verdict and per-metric margin columns make the trade-off explicit: this
device biases and meets every spec except open-loop gain, which the second stage
cannot reach without a higher ``second_stage_current_ratio`` — exactly what the
weak-inversion warning flags.

Further reading
---------------

.. toctree::
   :maxdepth: 1
   :caption: Theory

   ../theory/sizing_flow
   ../theory/gmid_sizing_flow

.. toctree::
   :maxdepth: 1
   :caption: Shared API

   ../api/sizer/sizer
   ../api/sizer/shared/models
   ../api/sizer/shared/loader
   ../api/sizer/shared/device_model
   ../api/sizer/shared/equations
   ../api/sizer/shared/gmid_lut
   ../api/sizer/shared/spice_sim
   ../api/sizer/shared/taxonomy
   ../api/sizer/shared/preprocess
   ../api/sizer/shared/metrics

.. toctree::
   :maxdepth: 1
   :caption: Analytical Sizer

   ../api/sizer/analytical/level1
   ../api/sizer/analytical/constraints

.. toctree::
   :maxdepth: 1
   :caption: gm/Id Sizer

   ../api/sizer/gmid/gmid_sizer
   ../api/sizer/gmid/analyze
   ../api/sizer/gmid/plan
   ../api/sizer/gmid/bias
   ../api/sizer/gmid/evaluate
   ../api/sizer/gmid/blocks
   ../api/sizer/gmid/intent
   ../api/sizer/gmid/geometry
   ../api/sizer/gmid/resistors
   ../api/sizer/gmid/bias_levels
