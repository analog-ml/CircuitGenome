Analytical Sizing Flow
======================

*Level-1 square-law equations and their step-by-step derivation.*

This page documents the complete analytical flow used by
:func:`~circuitgenome.sizer.sizer.size_circuit` to size a two-stage
Miller-compensated single-ended op-amp.  It covers the device model,
the five-step constraint derivation order, the CP-SAT integer
linearisation, and the post-sizing performance metric evaluation —
with a concrete numerical walkthrough using the values from
``examples/two_stage_se_specs/spec_generic.yaml``.

----

Scope and circuit topology
--------------------------

The sizer targets every op-amp topology template the synthesizer produces.

This page documents the **Level-1 (Shichman-Hodges)** analytical flow — square-law
drain current in saturation, constant channel-length modulation coefficient λ, no
short-channel or velocity-saturation effects.  This flow is selected for the
card-less ``generic`` technology.  Deep-submicron **PTM nodes and foundry PDKs
(e.g. GF180MCU) use the gm/Id pipeline instead** (LUT-driven, deterministic
geometry — see `gm/Id model (PTM nodes)`_ below); a PTM/SPICE-model node without a
gm/Id LUT raises ``UnsupportedTechError``.  For those technologies the reported
performance numbers are **measured in ngspice** rather than computed from the
Level-1 equations below (see `Post-sizing performance metrics`_).

.. code-block:: text

   ibias ──► [Tail] ──► [Input pair + Load] ──► Rout1 ──┬──► [2nd stage] ──► out
                                                         │          ▲
                                                        Cc ─────────┘
                          Stage 1 dominant pole: ωp1 = 1/(Rout1·Cc)
                          Non-dominant pole (neglected): ωp2 ≈ gm2/CL

The five-step sizing flow in :func:`~circuitgenome.sizer.shared.preprocess.compute_requirements`
derives performance requirements in this order:

.. list-table::
   :header-rows: 1
   :widths: 5 25 70

   * - Step
     - From spec
     - Derives
   * - 1
     - ``cmrr_min_db``
     - :math:`g_{m1}` lower bound — only constraint independent of :math:`C_c`
   * - 2
     - ``slew_rate_min_vps``
     - :math:`C_c` initial upper bound
   * - 3
     - ``gbw_min_hz``
     - :math:`g_{m1}` from :math:`C_c`; refines :math:`C_c` if CMRR pushed :math:`g_{m1}` up
   * - 4
     - ``gain_min_db``
     - :math:`g_{m2}` from the remaining gain budget after :math:`g_{m1}` is fixed
   * - 5
     - ``phase_margin_min_deg``
     - :math:`g_{m2}` from worst-case (grid-rounded) :math:`g_{m1}` — often the binding constraint

----

Device model
------------

Three Level-1 equations underpin the constraints below.  All are implemented
in :mod:`circuitgenome.sizer.shared.equations`.

Transconductance
~~~~~~~~~~~~~~~~

.. math::

   g_m = \sqrt{2\,\mu C_{ox}\,\frac{W}{L}\,|I_{DS}|}

:func:`~circuitgenome.sizer.shared.equations.gm` — ``mu_cox`` in A/V², ``w_um``
and ``l_um`` in µm, ``ids_a`` in A.

:math:`g_m` grows as the square root of gate area at a fixed :math:`I_{DS}`:
doubling :math:`W` (at fixed :math:`L`) gives :math:`\sqrt{2}\,\times\,g_m`,
not :math:`2\times`.  The :math:`g_m / I_{DS} = \sqrt{2\,\mu C_{ox} / (I_{DS}\,W/L)}`
ratio falls as current density increases — a key efficiency trade-off in
low-power design.

Output conductance
~~~~~~~~~~~~~~~~~~

.. math::

   g_d = \lambda\,|I_{DS}|

:func:`~circuitgenome.sizer.shared.equations.gd` — λ (``lam``) in V\ :sup:`-1`.

λ is treated as a **constant**, independent of :math:`V_{DS}`.  In real
silicon λ falls with channel length and rises with :math:`|V_{DS}|`, so
the :math:`R_{out}` computed here is a first-order estimate.  Use cascode
stages or SPICE simulation when accurate :math:`R_{out}` is critical.

Stage output resistance
~~~~~~~~~~~~~~~~~~~~~~~

.. math::

   R_{out} = \frac{1}{g_{d,\text{top}} + g_{d,\text{bot}}}

:func:`~circuitgenome.sizer.shared.equations.rout` — parallel combination of the
upper (load) and lower (drive) transistors' output conductances.

For a PMOS input pair with NMOS active load (generic tech, λ\ :sub:`p`\ =0.05, λ\ :sub:`n`\ =0.04)
biased at :math:`I_{DS}` = 5 µA each:

.. math::

   R_{out,1} = \frac{1}{0.05 \times 5\,\mu\text{A} + 0.04 \times 5\,\mu\text{A}}
             = \frac{1}{0.45\,\mu\text{A/V}} = 2.22\,\text{M}\Omega

gm/Id model (PTM nodes)
~~~~~~~~~~~~~~~~~~~~~~~~

The single fitted :math:`(\mu C_{ox}, V_{th}, \lambda)` triple above cannot
capture moderate/weak inversion, velocity saturation, or
:math:`\lambda \propto 1/L` — so on deep-submicron PTM nodes the Level-1
predictions diverge sharply from SPICE.  A technology that carries a
**gm/Id lookup table** (the ``gmid_lut`` field of
:class:`~circuitgenome.sizer.shared.models.TechParams`, e.g. ``ptm45``) instead drives
sizing from a SPICE-characterized table indexed by :math:`(g_m/I_{DS}, L)`.

In this path the small-signal primitives :math:`g_m`, :math:`g_{ds}`,
:math:`V_{DS,sat}` and :math:`V_{GS}` are read from the table — most importantly
:math:`g_{ds} = g_m / (g_m/g_{ds})`, with the intrinsic-gain ratio
:math:`g_m/g_{ds}` a genuine function of :math:`L` rather than a constant
:math:`\lambda`.  Because :math:`I_{DS}` is fixed by KCL, a target :math:`g_m`
fixes :math:`g_m/I_{DS}` and the table yields :math:`I_{DS}/W \rightarrow W`
directly, so geometry is **computed, not searched** (no CP-SAT): a deterministic
forward pass with grid snapping, matched-pair symmetry, and exact current-mirror
ratios.

The model is selected per-tech by
:func:`~circuitgenome.sizer.shared.device_model.build_device_model`
(:class:`~circuitgenome.sizer.shared.device_model.Level1Model` vs
:class:`~circuitgenome.sizer.shared.device_model.GmIdModel`); the table interface is
:class:`~circuitgenome.sizer.shared.gmid_lut.GmIdLut` and the geometry pass is
:func:`~circuitgenome.sizer.gmid.geometry.assign_geometry_gmid`.  The Level-1
flow described below is unchanged for the card-less generic tech.

----

Operating-point assignment
--------------------------

Reference: :func:`~circuitgenome.sizer.shared.preprocess.assign_ids`

:math:`I_{DS}` for every transistor is determined by **KCL and the external
bias current** before any W/L is chosen.  This is the critical insight that
keeps the sizing problem tractable: because :math:`I_{DS}` is a known constant
for each device, the nonlinear :math:`g_m \geq g_{m,req}` constraint
linearises in W and L (see `CP-SAT integer linearisation`_).

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Slot
     - :math:`I_{DS}` per transistor
     - Rationale
   * - ``input_pair``
     - :math:`I_{bias} / n` (n = number of same-type devices)
     - Tail current splits equally across the two input transistors
   * - ``load``
     - :math:`I_{bias} / n`
     - Mirror-image current from the input pair
   * - ``tail_current``
     - :math:`I_{bias}`
     - Carries the full tail current
   * - ``bias_gen``
     - :math:`I_{bias}`
     - Conservative estimate (one bias leg)
   * - ``second_stage``
     - :math:`I_{bias} \times \text{ratio}`
     - Set by ``second_stage_current_ratio`` in the spec (default 2.0)

----

Five-step constraint derivation
--------------------------------

Reference: :func:`~circuitgenome.sizer.shared.preprocess.compute_requirements`

Each step below follows the same structure: **equation → derivation →
intuition → why this position in the ordering → numerical example** using
``ibias`` = 10 µA, ``cl`` = 20 pF (``examples/two_stage_se_specs/spec_generic.yaml``).

Step 1 — CMRR sets the gm\ :sub:`1` floor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::

   \text{CMRR} \approx \frac{g_{m1}}{2\,g_{d,\text{tail}}}
   = \frac{g_{m1}}{2\,\lambda_{\text{tail}}\,I_{bias}}

Inverting for the minimum :math:`g_{m1}`:

.. math::

   g_{m1} \;\geq\; \text{CMRR}_{\text{lin}} \cdot 2\,\lambda_{\text{tail}}\,I_{bias}

where :math:`\text{CMRR}_{\text{lin}} = 10^{\,\text{CMRR}_{\text{dB}}/20}`.

**Intuition:** The tail current source has finite output resistance
:math:`1/g_{d,\text{tail}}`.  A common-mode input shift modulates the tail
node, injecting a differential-mode error current :math:`g_{d,\text{tail}}\,v_{cm}`
into the input pair.  The CMRR measures how well :math:`g_{m1}` suppresses
this error.  Higher :math:`I_{bias}` or higher λ degrades :math:`g_{d,\text{tail}}`
and demands a larger :math:`g_{m1}` floor.

**Why first:** CMRR is the only performance spec that is independent of
:math:`C_c`.  Setting it first lets Step 3 use the correct :math:`g_{m1}`
floor when refining :math:`C_c` from the GBW constraint.  If CMRR were
evaluated after :math:`C_c`, the GBW step might fix :math:`g_{m1}` too low
to satisfy CMRR, forcing a retroactive change to :math:`C_c` that could
violate SR.

Implementing function: :func:`~circuitgenome.sizer.shared.equations.cmrr_db`

**Numerical example** (CMRR = 50 dB, not specified in the example spec
but shown here for illustration):
λ\ :sub:`tail`\ = 0.05 V\ :sup:`-1`, :math:`I_{bias}` = 10 µA:

.. math::

   g_{m1} \;\geq\; 316.2 \times 2 \times 0.05 \times 10\,\mu\text{A}
          = 316\;\mu\text{A/V}

.. note::

   ``examples/two_stage_se_specs/spec_generic.yaml`` does *not* specify ``cmrr_min_db``
   because CMRR = 50 dB + GBW = 2.5 MHz + SR = 3.5 V/µs are mutually
   exclusive at :math:`I_{bias}` = 10 µA (see `Spec compatibility`_).
   The bound above is shown purely to illustrate the formula.

Step 2 — SR sets the C\ :sub:`c` ceiling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::

   SR = \frac{I_{bias}}{C_c}
   \;\implies\;
   C_c \;\leq\; \frac{I_{bias}}{SR_{\min}}

:math:`C_c` is then clamped to ``[cap.min, cap.max]`` from the technology config.

**Intuition:** During large-signal slewing, the input stage saturates and
the tail current is the only drive available to charge or discharge
:math:`C_c`.  A larger :math:`C_c` slows SR linearly.

**Why second:** SR provides the **upper bound** on :math:`C_c`.  All
subsequent steps can only grow :math:`C_c` (never shrink it), so setting
the SR ceiling first guarantees SR is never violated by later adjustments.

Implementing function: :func:`~circuitgenome.sizer.shared.equations.slew_rate_vps`

**Numerical example** — :math:`I_{bias}` = 10 µA, SR\ :sub:`min` = 3.5 V/µs:

.. math::

   C_c = \frac{10\;\mu\text{A}}{3.5\;\text{V/µs}} = 2.857\;\text{pF}

Step 3 — GBW fixes g\ :sub:`m1`, refines C\ :sub:`c`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::

   GBW = \frac{g_{m1}}{2\pi C_c}
   \;\implies\;
   g_{m1} \;\geq\; 2\pi \cdot GBW_{\min} \cdot C_c

If Step 1 (CMRR) already set a higher :math:`g_{m1}` floor, :math:`C_c`
must grow to maintain GBW:

.. math::

   C_c \;\gets\; \max\!\left(C_c,\; \frac{g_{m1}}{2\pi \cdot GBW_{\min}}\right)

re-clamped to ``cap.max``.

**Intuition:** In a two-stage Miller amplifier the dominant pole sits at
:math:`1/(R_{out1}\,C_c)` and the unity-gain frequency (where the gain
magnitude first crosses 0 dB) is :math:`g_{m1}/(2\pi C_c)`.  GBW is
essentially proportional to :math:`g_{m1}` at a fixed :math:`C_c`.

**Why after SR:** The GBW equation can be satisfied by any
(:math:`g_{m1}`, :math:`C_c`) pair on the line :math:`g_{m1} = 2\pi \cdot
GBW \cdot C_c`.  Without the SR constraint the sizer would pick the smallest
:math:`C_c` (cheapest in power), but that would violate SR.  Step 2 pins
:math:`C_c` from above; Step 3 reads that value to derive :math:`g_{m1}`.

Implementing function: :func:`~circuitgenome.sizer.shared.equations.unity_gain_bw`

**Numerical example** — GBW\ :sub:`min` = 2.5 MHz, :math:`C_c` = 2.857 pF from Step 2:

.. math::

   g_{m1} = 2\pi \times 2.5\;\text{MHz} \times 2.857\;\text{pF}
           = 44.88\;\mu\text{A/V}

Step 4 — Gain fixes g\ :sub:`m2`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The two-stage open-loop DC gain is:

.. math::

   A_0 = g_{m1}\,R_{out1} \cdot g_{m2}\,R_{out2}

With :math:`g_{m1}` now known, solve for the minimum :math:`g_{m2}`:

.. math::

   g_{m2} \;\geq\; \frac{A_0}{g_{m1}\,R_{out1}\,R_{out2}}

where :math:`R_{out1} = 1/(g_{d,ip} + g_{d,ld})` and
:math:`R_{out2} = 1/(g_{d,n2} + g_{d,p2})` are computed from
:math:`g_d = \lambda\,|I_{DS}|` at the bias point
(see :func:`~circuitgenome.sizer.shared.equations.rout`).

**Intuition:** The total gain is the product of two stage gains.  Stage 1's
gain is :math:`g_{m1}\,R_{out1}` — fixed once :math:`g_{m1}` is known and
the operating point is set.  Stage 2's gain budget fills the remainder.

**Why gm2 and not gm1:** :math:`g_{m1}` is already locked by GBW + SR.
If the gain spec were met by inflating :math:`g_{m1}` instead, the GBW
equation would require a proportionally larger :math:`C_c` (since
:math:`C_c = g_{m1}/(2\pi\,GBW)`), which violates the SR ceiling from
Step 2.  Assigning the gain responsibility to :math:`g_{m2}` decouples gain
from the SR-limited :math:`C_c`.

Implementing function: :func:`~circuitgenome.sizer.shared.equations.open_loop_gain_db`

**Numerical example** — gain\ :sub:`min` = 80 dB, :math:`g_{m1}` = 44.88 µA/V:

With the generic tech and ``second_stage_current_ratio`` = 2.5 → :math:`I_{DS,2}` = 25 µA:

.. math::

   g_{d,ip} = 0.05 \times 5\;\mu\text{A} = 0.25\;\mu\text{A/V}, \quad
   g_{d,ld} = 0.04 \times 5\;\mu\text{A} = 0.20\;\mu\text{A/V}

.. math::

   R_{out1} = \frac{1}{0.45\;\mu\text{A/V}} = 2.22\;\text{M}\Omega

.. math::

   g_{d,n2} = 0.04 \times 25\;\mu\text{A} = 1.00\;\mu\text{A/V}, \quad
   g_{d,p2} = 0.05 \times 25\;\mu\text{A} = 1.25\;\mu\text{A/V}

.. math::

   R_{out2} = \frac{1}{2.25\;\mu\text{A/V}} = 444\;\text{k}\Omega

.. math::

   g_{m2,\text{gain}} = \frac{10{,}000}{44.88\;\mu\text{A/V}
                         \times 2.22\;\text{M}\Omega \times 444\;\text{k}\Omega}
                      = 226\;\mu\text{A/V}

Step 5 — PM (worst-case g\ :sub:`m1`) fixes g\ :sub:`m2`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dominant-pole phase margin approximation (non-dominant mirror pole neglected):

.. math::

   PM \approx 90° - \arctan\!\left(\frac{g_{m1}\,C_L}{g_{m2}\,C_c}\right)

Solving for the minimum :math:`g_{m2}`:

.. math::

   g_{m2} \;\geq\; \frac{g_{m1,\text{worst}}\,C_L}{C_c\;\tan(90° - PM_{\min})}

The solver minimises gate area (width), so it will pick the smallest integer
:math:`W` that satisfies the :math:`g_{m1}` constraint — exactly
:math:`W_{\text{ceil}}`:

.. math::

   W_{\text{ceil}} = \left\lceil\frac{g_{m1,\text{req}}^2 \cdot L_{\min,\text{int}}}{
                    2\,\mu C_{ox}\,I_{DS}}\right\rceil

.. math::

   g_{m1,\text{worst}} = \sqrt{2\,\mu C_{ox}\,I_{DS} \cdot
                          \frac{W_{\text{ceil}}}{L_{\min,\text{int}}}}

where :math:`W_{\text{ceil}}` and :math:`L_{\min,\text{int}}` are in
integer grid-step units.

**Intuition:** The CP-SAT solver picks the *minimum* :math:`W` satisfying
the :math:`g_{m1} \geq g_{m1,\text{req}}` constraint, which is :math:`W_{\text{ceil}}`.
Because of the square-root relationship between :math:`g_m` and :math:`W`,
ceiling-rounding :math:`W` raises the *actual* :math:`g_{m1}` above the
required value.  A larger :math:`g_{m1}` shifts the non-dominant pole
frequency upward relative to :math:`g_{m2}`, degrading PM.  Sizing
:math:`g_{m2}` against :math:`g_{m1,\text{worst}}` — the actual
post-rounding value — ensures PM is satisfied on the integer grid.

Implementing function: :func:`~circuitgenome.sizer.shared.equations.phase_margin_two_stage_deg`

**Numerical example** — PM\ :sub:`min` = 60°, :math:`C_L` = 20 pF:

Input pair is PMOS (µ\ :sub:`p`\ C\ :sub:`ox` = 90 µA/V²), :math:`I_{DS,ip}` = 5 µA,
:math:`L_{\min,\text{int}}` = 1 step (1 µm):

.. math::

   \text{lhs} = 2 \times 90\;\mu\text{A/V}^2 \times 5\;\mu\text{A}
               = 900 \times 10^{-12}\;\text{A}^2/\text{V}^2

.. math::

   W_{\text{ceil}} = \left\lceil\frac{(44.88\;\mu\text{A/V})^2 \times 1}{
                    900 \times 10^{-12}}\right\rceil
                  = \lceil 2.24 \rceil = 3\;\text{steps}
                  \;\to\; W = 3\;\mu\text{m}

.. math::

   g_{m1,\text{worst}} = \sqrt{900 \times 10^{-12} \times 3}
                       = 51.96\;\mu\text{A/V}

.. math::

   g_{m2,\text{PM}} = \frac{51.96\;\mu\text{A/V} \times 20\;\text{pF}}{
                      2.857\;\text{pF} \times \tan(30°)}
                    = \frac{1039.2\;\text{fA·F/V}}{1.650\;\text{pF}}
                    = 630\;\mu\text{A/V}

Final :math:`g_{m2}` = max(226, 630) = **630 µA/V** ← PM is the binding constraint.

.. note::

   The non-dominant mirror pole at :math:`\approx g_{m,\text{load}}/C_{gs,\text{load}}`
   is ignored by this formula.  The actual PM is lower than the formula
   predicts when :math:`C_c` is small relative to :math:`C_{gs,\text{load}}`.
   Verify with SPICE AC simulation before tape-out.

----

CP-SAT integer linearisation
-----------------------------

Reference: :func:`~circuitgenome.sizer.analytical.constraints.build_model`

W and L for each transistor are **integer decision variables** in units of
the technology grid step.  The key observation is that once :math:`I_{DS}` is
fixed (Step 3 of the pipeline, before the solver runs), every constraint
becomes **linear** in the integer W and L variables.

g\ :sub:`m` lower-bound constraint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Starting from :math:`g_m \geq g_{m,\text{req}}`:

.. math::

   \sqrt{2\,\mu C_{ox}\,\frac{W}{L}\,I_{DS}} \;\geq\; g_{m,\text{req}}

Squaring both sides (both positive):

.. math::

   2\,\mu C_{ox}\,I_{DS} \cdot W \;\geq\; g_{m,\text{req}}^2 \cdot L
   \quad \text{[linear in integer } W, L \text{]}

In code (``constraints.py``):

.. code-block:: python

   lhs = round(2.0 * params.mu_cox * abs(ids_a) * _SCALE)   # coefficient of W
   rhs = round(gm_req ** 2 * _SCALE)                         # coefficient of L
   model.add(lhs * W[ref] >= rhs * L[ref])

V\ :sub:`DS,sat` upper-bound constraint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The overdrive constraint :math:`V_{DS,sat} \leq V_{DS,sat,\max}` likewise
linearises:

.. math::

   \sqrt{\frac{2\,I_{DS}\,L}{\mu C_{ox}\,W}} \;\leq\; V_{DS,sat,\max}
   \;\implies\;
   2\,I_{DS} \cdot L \;\leq\; \mu C_{ox}\,V_{DS,sat,\max}^2 \cdot W
   \quad \text{[linear]}

Integer coefficient scaling
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Raw floating-point coefficients (e.g. :math:`\mu C_{ox} \approx 90\;\mu\text{A/V}^2`,
:math:`I_{DS} \approx 5\;\mu\text{A}`) would give CP-SAT coefficients of order
:math:`10^{-9}`, which must be integers.  All coefficients are multiplied by
``_SCALE = 10``\ :sup:`12` before rounding:

.. math::

   2 \times 90\;\mu\text{A/V}^2 \times 5\;\mu\text{A} \times 10^{12}
   = 900 \quad\text{(a small integer ✓)}

Objective and symmetry
~~~~~~~~~~~~~~~~~~~~~~

- **Objective:** minimise :math:`\sum W_i` across all transistors — a proxy
  for total gate area and quiescent power.
- **Symmetry:** matched pairs within the ``input_pair``, ``load``, and
  ``tail_current`` slots are constrained to equal :math:`W` and equal
  :math:`L` (``model.add(W[i] == W[j])``).
- **Branching heuristic:** ``bias_gen`` transistors are prioritised first,
  then all others, using ``SELECT_MIN_VALUE`` to bias towards small W/L
  solutions early in the search tree.

----

Post-sizing performance metrics
--------------------------------

After the solver returns W/L values, :func:`~circuitgenome.sizer.shared.metrics._evaluate_metrics`
computes all performance metrics and their margins against spec.  The
implementing function for each is in :mod:`circuitgenome.sizer.shared.equations`.
These are **analytical (model-based) estimates**; the gm/Id pipeline computes the
same table from LUT-accurate small-signal parameters.  For PTM / foundry-PDK techs
the CLI does not display these numbers — it reports **ngspice-measured** metrics
instead (see `Feasibility verdict and SPICE metrics (PTM / foundry PDKs)`_).

.. list-table::
   :header-rows: 1
   :widths: 22 38 22 18

   * - Metric
     - Equation
     - Function
     - Margin sign
   * - Open-loop gain
     - :math:`A_0 = \prod_j g_{m,j}\,R_{out,j}`, converted to dB
     - :func:`~circuitgenome.sizer.shared.equations.open_loop_gain_db`
     - actual − spec
   * - GBW
     - :math:`GBW = g_{m1}\,/\,(2\pi C_c)`
     - :func:`~circuitgenome.sizer.shared.equations.unity_gain_bw`
     - actual − spec
   * - Phase margin
     - :math:`PM = 90° - \arctan(g_{m1}\,C_L\,/\,(g_{m2}\,C_c))`
     - :func:`~circuitgenome.sizer.shared.equations.phase_margin_two_stage_deg`
     - actual − spec
   * - Slew rate
     - :math:`SR = I_{bias}\,/\,C_c`
     - :func:`~circuitgenome.sizer.shared.equations.slew_rate_vps`
     - actual − spec
   * - CMRR
     - :math:`CMRR = g_{m1}\,/\,(2\,g_{d,\text{tail}})`, dB
     - :func:`~circuitgenome.sizer.shared.equations.cmrr_db`
     - actual − spec
   * - PSRR\ :sup:`+` (approx)
     - :math:`PSRR^+ \approx g_{m2}\,/\,g_{d,\text{bias}}`, dB
     - :func:`~circuitgenome.sizer.shared.equations.psrr_db_approx`
     - actual − spec
   * - Quiescent power
     - :math:`P = (V_{DD} - V_{SS})\sum|I_{supply}|`
     - :func:`~circuitgenome.sizer.shared.equations.quiescent_power`
     - spec − actual
   * - Output swing max
     - :math:`V_{out,\max} = V_{DD} - V_{DS,sat}(\text{PMOS}_2)`
     - (inline in ``_evaluate_metrics``)
     - actual − spec
   * - Output swing min
     - :math:`V_{out,\min} = V_{SS} + V_{DS,sat}(\text{NMOS}_2)`
     - (inline in ``_evaluate_metrics``)
     - spec − actual

A positive margin value means the spec is met with headroom; a negative
margin means the spec is violated (only possible if the spec was not
enforced by a CP-SAT constraint, e.g. PSRR, power, swing).

----

Feasibility verdict and SPICE metrics (PTM / foundry PDKs)
----------------------------------------------------------

For a technology with a real device model (a PTM BSIM4 card, or a foundry PDK such
as GF180MCU), the analytical estimate above would mismatch the device, so the CLI
grounds its report in ngspice instead:

* **Feasibility verdict.**  A SPICE DC operating-point check
  (:func:`~circuitgenome.sizer.shared.spice_sim.check_bias_soundness`) classifies
  the design as:

  * **INFEASIBLE** — the bias point cannot be established (the feedback operating
    point rails, or a device is starved / pushed into triode); performance is not
    reported.
  * **MARGINAL** — biases correctly but misses one or more specs.
  * **FEASIBLE** — biases correctly and meets every measured spec.

* **Measured metrics.**  When feasible, the CLI measures performance in ngspice
  (:func:`~circuitgenome.sizer.shared.spice_sim.simulate_metrics`): open-loop gain,
  GBW, phase margin, slew rate (min of the rising and falling edges), quiescent
  power, CMRR, PSRR+, and output swing.  A metric ngspice cannot extract is shown
  as ``n/a`` (no analytical fallback), and ngspice is **required** — the command
  errors if it is not installed.

* **Corner sweep (foundry PDKs).**  A PDK tech (``spice_lib``, e.g. GF180MCU) is
  sized at its nominal corner, then the sized design is re-measured across the
  configured process corners (``{typical, ss, ff, sf, fs}``) and printed as a
  corner-verification table for worst-case visibility.  The PTM nodes carry a
  single model card and report the nominal corner only.

----

Assumptions and where they break down
--------------------------------------

.. warning::

   **Level-1 (square-law) model.**  The :math:`g_m = \sqrt{2\,\mu C_{ox}\,(W/L)\,I_{DS}}`
   formula is accurate for transistors well into saturation with :math:`L \geq`
   approximately 0.18–0.25 µm (as in the generic tech config).  For deep-submicron
   nodes (28 nm, 7 nm), velocity saturation and other short-channel effects
   dominate; the Level-1 model will significantly over-estimate :math:`g_m`
   at a given :math:`W/L` and :math:`I_{DS}`.

.. warning::

   **Dominant-pole phase margin.**  The formula
   :math:`PM \approx 90° - \arctan(g_{m1}\,C_L / (g_{m2}\,C_c))`
   ignores the non-dominant mirror pole at :math:`\approx g_{m,\text{load}}/C_{gs,\text{load}}`.
   When :math:`C_c` is small (aggressive SR spec) the mirror pole can push
   the actual PM several degrees below the formula's prediction.
   Always verify PM with a SPICE AC sweep before committing to a topology.

.. note::

   **Constant λ.**  :math:`g_d = \lambda\,|I_{DS}|` treats λ as
   VDS-independent.  In practice λ ∝ 1/L and increases with :math:`|V_{DS}|`,
   so :math:`R_{out}` computed here is an approximation.  For designs where
   DC gain is critical, consider cascode stages and use simulation to
   characterise :math:`R_{out}` accurately.

.. note::

   **PSRR approximation.**  :func:`~circuitgenome.sizer.shared.equations.psrr_db_approx`
   returns a first-order upper bound on PSRR\ :sup:`+`.  Full PSRR — including
   the path through the compensation capacitor — requires an AC simulation.

.. note::

   **No device mismatch.**  The solver assumes perfectly matched differential
   pairs.  Offset voltage and CMRR degradation from mismatch must be estimated
   separately (e.g. Pelgrom's model).

----

Spec compatibility
------------------

CMRR, GBW, and SR all depend on the same three variables (:math:`I_{bias}`,
:math:`C_c`, :math:`g_{m1}`) and are **mutually exclusive** when the bias
current is small.

The conflict chain:

.. math::

   \text{CMRR}_{\min} \;\to\; g_{m1,\min}
   \;\xrightarrow{GBW}\; C_c \geq \frac{g_{m1,\min}}{2\pi\,GBW_{\min}}
   \;\xrightarrow{SR}\; C_c \leq \frac{I_{bias}}{SR_{\min}}

If the GBW-required :math:`C_c` exceeds the SR-limited :math:`C_c`:

.. math::

   \frac{g_{m1,\min}}{2\pi\,GBW_{\min}} > \frac{I_{bias}}{SR_{\min}}

the specification set is physically infeasible at the given :math:`I_{bias}`.
The CP-SAT solver returns ``INFEASIBLE`` in this case.

**Resolution strategies:**

1. Remove one of the three conflicting specs (CMRR, GBW, or SR).
2. Increase :math:`I_{bias}` — this relaxes the SR ceiling on :math:`C_c`
   without affecting the CMRR-derived :math:`g_{m1}` floor.
3. Relax the CMRR target — this lowers :math:`g_{m1,\min}`, reducing the
   :math:`C_c` required to maintain GBW.

----

Three-Stage NMC / RNMC
-----------------------

The sizer also supports all four three-stage topologies
(``three_stage_opamp_nmc_single_ended``, ``three_stage_opamp_rnmc_single_ended``,
``three_stage_opamp_nmc_fully_differential``,
``three_stage_opamp_rnmc_fully_differential``).  The same conservative
equations are applied to both NMC and RNMC.

Circuit topology
~~~~~~~~~~~~~~~~

.. code-block:: text

   ibias ──► [Tail] ──► [Input pair + Load] ──► Rout1 ──► [2nd stage] ──► Rout2 ──► [3rd stage] ──► out
                                          ▲                          ▲
                                          │◄─────────── Cc1 ─────────┼──────────────────────────────┘
                                          │                          │◄──── Cc2 ──────────────────┘

*NMC:* :math:`C_{c1}` closes the outer loop (stage-3 output → stage-1 output);
:math:`C_{c2}` closes the inner loop (stage-3 output → stage-2 output).

*RNMC:* :math:`C_{c1}` closes the inner loop (stage-3 output → stage-2 output);
:math:`C_{c2}` closes the reversed outer loop (stage-2 output → stage-1 output).
The sizer uses the same conservative equations for both schemes.

Design variables
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Variable
     - Source
   * - :math:`C_{c1}` (outer)
     - :math:`\min(I_{bias}/SR,\; g_{m1}/(2\pi \cdot GBW))`
   * - :math:`C_{c2}` (inner)
     - :math:`C_{c1}/4` (Eschauzier–Huijsing heuristic)
   * - :math:`g_{m1}`
     - CMRR + GBW (same as two-stage)
   * - :math:`g_{m2}`
     - Inner-pole PM condition (see below)
   * - :math:`g_{m3}`
     - Gain + outer-pole PM condition (see below)

Phase margin derivation
~~~~~~~~~~~~~~~~~~~~~~~

With two non-dominant poles, the phase margin is:

.. math::

   \text{PM} \approx 90^{\circ}
       - \arctan\!\left(\frac{\omega_t \cdot C_{c2}}{g_{m2}}\right)
       - \arctan\!\left(\frac{\omega_t \cdot C_L}{g_{m3}}\right)

where :math:`\omega_t = g_{m1}/C_{c1}` is the unity-gain frequency
(implemented in :func:`~circuitgenome.sizer.shared.equations.phase_margin_three_stage_deg`).

For a target :math:`\text{PM}_{\min}`, the sizer **splits the phase budget
equally** between the two poles — each is allowed to contribute at most
:math:`(90^{\circ} - \text{PM}_{\min})/2` of lag:

.. math::

   \theta = \frac{90^{\circ} - \text{PM}_{\min}}{2}

This leads to two independent lower bounds:

.. math::

   g_{m2} \;\geq\; \frac{g_{m1} \cdot C_{c2}}{C_{c1} \cdot \tan\theta}
   \qquad \text{(inner pole)}

.. math::

   g_{m3} \;\geq\; \frac{g_{m1} \cdot C_L}{C_{c1} \cdot \tan\theta}
   \qquad \text{(output pole)}

For :math:`\text{PM}_{\min} = 60^{\circ}`: :math:`\theta = 15^{\circ}`,
:math:`\tan\theta \approx 0.268`, so each non-dominant pole must be placed
at :math:`\approx 3.73 \times \omega_t`.

.. note::

   These are **sufficient conditions** — both poles are individually bounded,
   so the actual PM will be ≥ :math:`\text{PM}_{\min}` even if one pole is
   at its minimum.

Gain requirement
~~~~~~~~~~~~~~~~

Once :math:`g_{m2}` is determined from the inner-pole condition, the
three-stage gain formula is used to derive :math:`g_{m3}`:

.. math::

   A_0 = g_{m1} \cdot R_{out1} \cdot g_{m2} \cdot R_{out2} \cdot g_{m3} \cdot R_{out3}

   \Rightarrow\quad g_{m3} \;\geq\; \frac{A_0}{g_{m1} \cdot R_{out1} \cdot g_{m2} \cdot R_{out2} \cdot R_{out3}}

The sizer takes :math:`g_{m3,\text{req}} = \max(g_{m3,\text{gain}},\; g_{m3,\text{PM}})`.

Numerical example
~~~~~~~~~~~~~~~~~

Specification: :math:`I_{bias}=10\,\mu\text{A}`,
:math:`C_L=20\,\text{pF}`, :math:`SR=3.5\,\text{V/µs}`,
:math:`GBW=2.5\,\text{MHz}`, :math:`\text{PM}\geq 60^{\circ}`,
:math:`A_0 \geq 100\,\text{dB}`, :math:`I_{D2}/I_{bias}=2.5`,
:math:`I_{D3}/I_{bias}=5`.

1. **Cc1 from SR:** :math:`C_{c1} = 10\,\mu\text{A} / 3.5\,\text{V/µs} = 2.857\,\text{pF}`
2. **Cc2:** :math:`C_{c2} = 2.857/4 = 0.714\,\text{pF}`
3. **gm1 from GBW:** :math:`g_{m1} = 2\pi \times 2.5\,\text{MHz} \times 2.857\,\text{pF} = 44.88\,\mu\text{A/V}`
4. **θ for PM=60°:** :math:`\theta = 15^{\circ}`, :math:`\tan\theta = 0.2679`
5. **gm2 (inner pole):** :math:`g_{m2} \geq 44.88 \times 0.714/(2.857 \times 0.2679) = 41.9\,\mu\text{A/V}`
6. **gm3 (output pole, PM):** :math:`g_{m3} \geq 44.88 \times 20/(2.857 \times 0.2679) = 1175\,\mu\text{A/V}`
7. **gm3 (gain, 100 dB → 10⁵):** Using typical :math:`R_{out}\sim100\,\text{k}\Omega` per stage:
   :math:`g_{m3,\text{gain}} = 10^5/(44.88 \times 10^5 \times 41.9 \times 10^5 \times 10^5) \ll g_{m3,\text{PM}}`
   → PM dominates.
8. **Binding constraint:** :math:`g_{m3,\text{req}} = 1175\,\mu\text{A/V}` (output pole PM)

.. note::

   The output-stage :math:`g_{m3}` requirement for three-stage is much larger
   than the two-stage :math:`g_{m2}` at the same spec because the phase budget
   is split — each non-dominant pole must be ~3.7× farther than the single
   pole in the two-stage case.

Fully-differential three-stage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For FD topologies, the sizer applies the same equations to each output path
independently (using ``second_stage_p``/``third_stage_p`` as the representative
path).  Power is computed as:

.. math::

   P = V_{DD} \times \bigl(I_{bias} + 2\,I_{D2} + 2\,I_{D3} + I_{bias,gen}\bigr)

Cross-slot symmetry constraints (``second_stage_p`` ↔ ``second_stage_n``
and ``third_stage_p`` ↔ ``third_stage_n``) force identical W/L on both
output paths, ensuring balanced differential swing.

Operating-point mapping for three-stage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Slot
     - :math:`I_{DS}` assignment
   * - ``input_pair``
     - :math:`I_{bias}/2` per transistor
   * - ``load``
     - :math:`I_{bias}/2` per transistor
   * - ``tail_current``
     - :math:`I_{bias}`
   * - ``second_stage``
     - :math:`I_{D2} = \text{ratio}_2 \times I_{bias}`
   * - ``third_stage``
     - :math:`I_{D3} = \text{ratio}_3 \times I_{bias}`
   * - ``comp1`` / ``comp2``
     - capacitors — no :math:`I_{DS}`
   * - ``bias_gen``
     - :math:`I_{bias}` (conservative)
