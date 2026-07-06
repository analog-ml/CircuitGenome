Sizer
=====

The **Sizer (SZ)** takes an FBR slot assignment plus a performance specification and
returns minimum transistor W/L values for every device in the circuit.  It
supports all seven op-amp topology templates (one-stage, two-stage
single-ended/fully-differential, and the four three-stage NMC/RNMC variants) and
targets DC specs — gain, GBW, phase margin, slew rate, CMRR, power, and output
swing.

The sizer has two paths, selected by technology:

- The card-less ``generic`` tech uses a **Level-1 square-law model**, which
  linearises the ``gm`` constraints into an integer program solved with
  **OR-Tools CP-SAT**.
- PTM nodes and foundry PDKs (e.g. GF180MCU) use the **gm/Id pipeline**, which
  chooses geometry deterministically from a SPICE-characterised gm/Id lookup
  table, capturing moderate/weak-inversion and short-channel behaviour the
  square law misses.

Sized designs are verified with **ngspice** — measured directly for real device
models, and cross-checked against the analytical formulas for the ``generic``
tech.

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

Sizing algorithm
----------------

.. note::

   The requirement-derivation order below is shown for the **two-stage** case
   as an illustration. The complete derivation for all seven topologies —
   including the three-stage inner-pole and :math:`g_{m3}` steps — is covered
   in :doc:`../theory/sizing_flow`.

The sizer has two paths, selected by technology.  The **card-less ``generic``
tech** uses a Level-1 MOSFET model where ``gm = √(2·µCox·(W/L)·IDS)`` and
``gd = λ·|IDS|``.  Because ``IDS`` is topology-determined by KCL and the bias
current, the ``gm ≥ gm_req`` constraint linearises to
``2·µCox·IDS·W ≥ gm_req²·L``, a linear integer constraint once W and L are
discrete grid variables — solved with CP-SAT.

**PTM nodes use the gm/Id pipeline instead**: geometry is chosen deterministically
from a SPICE-characterised gm/Id lookup table (no CP-SAT search), which captures
moderate/weak inversion and short-channel behaviour the square law misses.  A
PTM/SPICE-model node without a gm/Id LUT raises ``UnsupportedTechError``.  The
requirement-derivation order below is shared by both paths.

The required transconductances are derived in a fixed order to ensure mutual
consistency after the integer grid rounds values up:

1. **CMRR** — sets ``gm1`` lower bound from the tail's output conductance
   (independent of ``Cc``; computed first so the bound propagates correctly).
2. **SR → Cc** — ``Cc ≥ ibias / SR_min`` (initial upper bound on ``Cc``).
3. **GBW + gm1 → Cc** — ``Cc ≥ gm1 / (2π · GBW_min)``; ``Cc`` may grow if
   CMRR pushes ``gm1`` up.
4. **Gain → gm2** — open-loop gain ``A0 = gm1·Rout1·gm2·Rout2``; gain drives
   ``gm2`` (not ``gm1``) to keep ``gm1`` small and preserve the SR bound.
5. **PM (worst-case gm1) → gm2** — the integer grid ceiling-rounds ``W1`` up,
   increasing the actual ``gm1``; ``gm2`` is computed from the ceiling-rounded
   value so the phase margin holds on the discrete grid.

CP-SAT integer solver
---------------------

W and L for each transistor are integer variables (in units of the
technology grid step).  The solver minimises total gate width (proxy for
power and area) subject to the linearised ``gm`` and ``VDS_sat`` constraints,
plus symmetry constraints (matched pairs within ``input_pair``, ``load``, and
``tail_current`` slots).  The branching heuristic prioritises ``bias_gen``
transistors first, then all others.

Spec compatibility notes
------------------------

The three specs ``CMRR``, ``GBW``, and ``SR`` share the same variables
(``ibias``, ``Cc``, ``gm1``) and can be **mutually exclusive** for small bias
currents.  Specifically, ``CMRR_min + GBW_min`` together fix ``Cc ≥ gm1_cmrr /
(2π · GBW_min)``; if that ``Cc`` exceeds ``ibias / SR_min``, the slew-rate
spec cannot be met.  In that case the solver returns ``INFEASIBLE``.  The
recommended approach is to specify at most two of the three, or relax ``ibias``.

Technology configurations
---------------------------

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
     - Illustrative defaults; the built-in fallback.
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

ngspice runs in two roles, using the model from the tech: a BSIM4 ``.pm`` card for
the PTM nodes (``spice_model``), a foundry corner library for a PDK
(``spice_lib`` → ``.lib "<file>" <corner>``, e.g. GF180MCU), or a synthesised
Level-1 ``.model`` from ``mu_cox``/``vth``/``lam`` for ``generic``:

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

Analysis
--------

.. toctree::
   :maxdepth: 1

   ../theory/sizing_flow
   ../theory/gmid_sizing_flow

API reference
-------------

.. toctree::
   :maxdepth: 1

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
   ../api/sizer/analytical/level1
   ../api/sizer/analytical/constraints
   ../api/sizer/gmid/gmid_sizer
   ../api/sizer/gmid/analyze
   ../api/sizer/gmid/blocks
   ../api/sizer/gmid/plan
   ../api/sizer/gmid/intent
   ../api/sizer/gmid/geometry
   ../api/sizer/gmid/bias
   ../api/sizer/gmid/resistors
   ../api/sizer/gmid/bias_levels
   ../api/sizer/gmid/evaluate
