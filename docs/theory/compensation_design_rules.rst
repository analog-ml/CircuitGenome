Compensation Design Rules: RNMC Damping and the Nulling Resistor
================================================================

*How strong must the second stage of a reversed-nested-Miller (RNMC) op-amp
be, and what resistor goes in series with a compensation capacitor?*

Two short functions in :mod:`~circuitgenome.sizer.physics.equations` answer
those questions:

.. list-table::
   :header-rows: 1
   :widths: 34 40 26

   * - Function
     - Question it answers
     - Applies to
   * - :func:`~circuitgenome.sizer.physics.equations.rnmc_min_gm2_for_damping`
     - What is the smallest :math:`g_{m2}` that stops the two extra poles from
       ringing?
     - RNMC, no output buffer
   * - :func:`~circuitgenome.sizer.physics.equations.nulling_resistor_ohm`
     - What resistor stops the signal sneaking forward through a compensation
       cap?
     - Every compensation branch that has a resistor

Pole pairs and the damping ratio :math:`\zeta` are explained in
:doc:`rnmc_stability`.  Here it is enough to know that :math:`\zeta` scores
how bouncy the amplifier is: below 0 the bounces grow (unstable), at 0 they go
on forever, and at 0.7 they die out quickly.

----

The circuit
-----------

The standard RNMC toplogy has three stages, two compensation capacitors and a load: 
(Stages 1 and 2 are inverting, stage 3 is non-inverting. )

.. code-block:: text

                  ┌──────────── Cc1 ────────────┐
                  ├──── Cc2 ────┐               │
                  │             │               │
   vin ──▷ gm1 ───●── ▷ gm2 ────●──── ▷ gm3 ────●── vout
                  V1            V2              V3
                                ═╧═ C2          ═╧═ CL
                           (parasitic)        (load)

1. The RNMC pole pair and its damping
-------------------------------------

The rest of this section derives the damping :math:`\zeta`, in four moves:

1. solve the circuit for its denominator;
2. factor the denominator into the dominant pole and the pole pair;
3. tidy the pair's two coefficients, :math:`a_1` and :math:`a_2`;
4. read the damping :math:`\zeta` off them.

Solve the circuit
~~~~~~~~~~~~~~~~~

Write Kirchhoff's current law at each node, with each stage reduced to its gm.
Output conductances and the parasitic at :math:`V_1` are left out:

.. math::

   \begin{aligned}
   V_1&:\; g_{m1}V_{in} + sC_{c2}(V_1-V_2) + sC_{c1}(V_1-V_3) = 0\\
   V_2&:\; g_{m2}V_1 + sC_2V_2 + sC_{c2}(V_2-V_1) = 0\\
   V_3&:\; -g_{m3}V_2 + sC_LV_3 + sC_{c1}(V_3-V_1) = 0
   \end{aligned}

Sort the terms by the voltage they multiply:

.. math::

   \underbrace{\begin{bmatrix}
   s(C_{c1}+C_{c2}) & -sC_{c2} & -sC_{c1}\\
   g_{m2}-sC_{c2} & s(C_2+C_{c2}) & 0\\
   -sC_{c1} & -g_{m3} & s(C_L+C_{c1})
   \end{bmatrix}}_{M(s)}
   \begin{bmatrix} V_1\\ V_2\\ V_3 \end{bmatrix}
   =
   \begin{bmatrix} -g_{m1}V_{in}\\ 0\\ 0 \end{bmatrix}

Call the matrix :math:`M(s)`.  Solving the three equations for :math:`V_3`
divides by its determinant (Cramer's rule):

.. math::

   \frac{V_3}{V_{in}} = \frac{N(s)}{D(s)},
   \qquad
   D(s) = \det M(s)

Only the denominator :math:`D(s)` is needed here, because its roots are the
poles.  With the input off, the equations become :math:`M(s)\,V = 0`, and they
allow a non-zero wobble only where :math:`\det M(s) = 0`.

Multiply out the determinant and group the terms by power of :math:`s`:

.. math::

   D(s) = A\,s + B\,s^2 + E\,s^3

.. list-table::
   :header-rows: 1
   :widths: 20 12 68

   * - Coefficient of
     - Name
     - Value
   * - :math:`s`
     - :math:`A`
     - :math:`C_{c1}\,g_{m2}\,g_{m3}`
   * - :math:`s^2`
     - :math:`B`
     - :math:`C_{c2}\,\bigl[(C_L+C_{c1})\,g_{m2} - C_{c1}\,g_{m3}\bigr]`
   * - :math:`s^3`
     - :math:`E`
     - :math:`C_LC_{c1}C_{c2} + C_2\,(C_LC_{c1} + C_LC_{c2} + C_{c1}C_{c2})`


~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Divide every term by the first one, :math:`A\,s`, and write it in front:

.. math::

   D(s) = \underbrace{A\,s}_{\text{dominant pole}}
          \times\Bigl[\,1 + \underbrace{\frac{B}{A}}_{a_1}\,s
                          + \underbrace{\frac{E}{A}}_{a_2}\,s^2\Bigr]



.. math::

   a_1 = \frac{C_{c2}\,\bigl[(C_L+C_{c1})\,g_{m2} - C_{c1}\,g_{m3}\bigr]}
              {C_{c1}\,g_{m2}\,g_{m3}}

Let's define

.. math::

   E = C_LC_{c1}C_{c2}\,K,
   \qquad
   K = 1 + C_2\left(\frac{1}{C_{c2}} + \frac{1}{C_{c1}} + \frac{1}{C_L}\right)

then we have

.. math::

   a_2 = \frac{C_LC_{c1}C_{c2}\,K}{C_{c1}\,g_{m2}\,g_{m3}}
       = \frac{C_L\,C_{c2}\,K}{g_{m2}\,g_{m3}}

Any bouncing system (a spring, a car, this circuit)
can be written with two numbers that have a meaning.  This is the *standard
form* of a pole pair:

.. math::

   1 + \frac{2\zeta}{\omega_n}\,s + \frac{1}{\omega_n^2}\,s^2

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Symbol
     - Question it answers
   * - :math:`\omega_n`
     - How *fast* does it bounce?
   * - :math:`\zeta`
     - How *bouncy* is it?  (The 2 makes :math:`\zeta = 1` the exact borderline
       between bouncing and not bouncing.)

Match it piece by piece.

.. list-table::
   :header-rows: 1
   :widths: 25 20 25 30

   * - In front of
     - Ours
     - Standard form
     - So
   * - :math:`s^2`
     - :math:`a_2`
     - :math:`1/\omega_n^2`
     - :math:`\omega_n = 1/\sqrt{a_2}`
   * - :math:`s`
     - :math:`a_1`
     - :math:`2\zeta/\omega_n`
     - :math:`\zeta = a_1\,\omega_n/2`

Put the first result into the second:

.. math::

   \zeta = \frac{a_1\,\omega_n}{2} = \frac{a_1}{2\sqrt{a_2}}

The pair is stable when :math:`\zeta` is positive.
The bottom, :math:`2\sqrt{a_2}`, is always positive, so :math:`\zeta` takes the
sign of :math:`a_1`.  In :math:`a_1` every symbol is positive, so only the
subtraction in its bracket can make it negative:

.. math::

   \underbrace{(C_L+C_{c1})\,g_{m2}}_{\text{stage 2 calms}}
   \;-\;
   \underbrace{C_{c1}\,g_{m3}}_{\text{stage 3 stirs}}


2. The second-stage floor: ``rnmc_min_gm2_for_damping``
-------------------------------------------------------

In Section 1 we know how to calculate :math:`\zeta` for a known :math:`g_{m2}`.  This function runs
it backwards: it sets :math:`\zeta` to ``zeta_target`` and solves for
:math:`g_{m2}`.

Put :math:`a_1` and :math:`a_2` into :math:`a_1 = 2\,\zeta_{target}\sqrt{a_2}`
and multiply both sides by :math:`C_{c1}\,g_{m2}\,g_{m3}/C_{c2}`:

.. math::

   (C_L+C_{c1})\,g_{m2} - C_{c1}\,g_{m3}
   = 2\,\zeta_{target}\,C_{c1}\sqrt{\frac{C_L K g_{m3}}{C_{c2}}}\;\sqrt{g_{m2}}

Both :math:`g_{m2}` and :math:`\sqrt{g_{m2}}` appear, so this is an ordinary
quadratic in :math:`\sqrt{g_{m2}}`.  The code names its coefficients as any
textbook does:

.. math::

   a\,\bigl(\sqrt{g_{m2}}\bigr)^2 + b\,\sqrt{g_{m2}} + c = 0

.. list-table::
   :header-rows: 1
   :widths: 20 45 35

   * - In the code
     - In the formula
     - Meaning
   * - ``k``
     - :math:`K`
     - parasitic penalty
   * - ``a``
     - :math:`C_L + C_{c1}`
     - capacitance at the output
   * - ``b``
     - :math:`-2\,\zeta_{target}\,C_{c1}\sqrt{C_L K g_{m3}/C_{c2}}`
     - the price of damping; grows with ``zeta_target``
   * - ``c``
     - :math:`-C_{c1}\,g_{m3}`
     - the damping stage 3 takes away
   * - ``sqrt_gm2``
     - :math:`\sqrt{g_{m2}} = \dfrac{-b + \sqrt{b^2 - 4ac}}{2a}`
     - the positive root

(``a`` and ``b`` are not :math:`a_1` and :math:`a_2`, which are the
coefficients of the polynomial in :math:`s`.)  Since :math:`a > 0` and
:math:`c < 0`, exactly one root is positive.  The result is its square.

Example
~~~~~~~

With :math:`g_{m3}` = 1 mS, :math:`C_{c1}` = 2 pF, :math:`C_{c2}` = 1 pF,
:math:`C_L` = 8 pF, no parasitic and ``zeta_target`` = 0.7:

.. code-block:: text

   k        = 1
   a        = 8 pF + 2 pF                             =  10 pF
   b        = −2 · 0.7 · 2 pF · √(8 pF · 1 mS / 1 pF) = −2.504e-13
   c        = −2 pF · 1 mS                            = −2e-15
   sqrt_gm2 = (2.504e-13 + 3.778e-13) / 20 pF         =  0.03141
   gm2      = 0.03141²                                =  0.987 mS


The floor is a starting point, not the final answer.
:func:`~circuitgenome.sizer.physics.rnmc.find_stable_rnmc_design` starts its
:math:`g_{m2}` search there and raises :math:`g_{m2}` until the open-loop
model, which includes everything left out here, reaches the same
:math:`\zeta` and meets the phase-margin spec.  Behind an output buffer the
third stage no longer drives :math:`C_L`, so the formula does not apply and
the search starts from the current :math:`g_{m2}`.  :ref:`rnmc-inner-pair` has
the full strategy.

3. The nulling resistor: ``nulling_resistor_ohm``
-------------------------------------------------

**Objective.**  Without a resistor, the compensation cap creates a
right-half-plane zero.  Like an extra pole, it delays the signal and so reduces
the phase margin.  This function finds the series resistor that moves the zero
onto the output pole, so that the zero cancels the pole.

**Poles, zeros and phase margin.**  Phase margin is a safety budget: how many
more degrees of delay the loop can take, at the frequency where its gain falls
to 1, before it reaches 180° and oscillates.  Every pole uses up some of that
budget, because it delays the signal.  A zero can go either way:

.. list-table::
   :header-rows: 1
   :widths: 44 30 26

   * -
     - Delay
     - Phase margin
   * - pole
     - adds delay
     - costs
   * - zero, left half plane (negative)
     - removes delay
     - **gives back**
   * - zero, right half plane (positive)
     - adds delay, like a pole
     - **costs**

**The problem:** A compensation cap is there to carry the output *back* to the stage's input.
At high frequency it also carries the input *forward*, past the stage.

We will need to come up with the derivation that solves the circuit for its zero and its poles, then sets the
zero equal to the output pole.

**The circuit.**  The first stage delivers a current :math:`i_{in}` into the
gate node.  :math:`C_1` is the small parasitic there.  Output conductances are
left out; they matter only at low frequency.

.. code-block:: text

              ┌──── R ──── Cc ───┐
              │                  │
   i_in ──►──●vin ── ▷ gm_out ──●── vout
              │                  │
             ═╧═ C1             ═╧═ C

**Merge R and Cc.**  In series, impedances add, so the branch has the
admittance

.. math::

   Y = \frac{1}{R + 1/(sC_c)} = \frac{sC_c}{1 + sRC_c}

**Write Kirchhoff's current law** at :math:`v_{out}` and at :math:`v_{in}`:

.. math::

   \begin{aligned}
   g_{m,out}\,v_{in} + (v_{out}-v_{in})\,Y + sC\,v_{out} &= 0 \\
   sC_1\,v_{in} + (v_{in}-v_{out})\,Y &= i_{in}
   \end{aligned}

**Solve for the output.**  The first line gives

.. math::

   v_{in} = v_{out}\,\frac{sC + Y}{Y - g_{m,out}}

Put that into the second line.  The two :math:`Y^2` terms cancel:

.. math::

   \frac{v_{out}}{i_{in}}
   = \frac{Y - g_{m,out}}{(sC+Y)(sC_1+Y) - Y(Y-g_{m,out})}
   = \frac{Y - g_{m,out}}{s^2CC_1 + Y\,[\,g_{m,out} + s(C+C_1)\,]}

Multiply top and bottom by :math:`1 + sRC_c`, which turns :math:`Y` into
:math:`sC_c`:

.. math::

   \frac{v_{out}}{i_{in}}
   = \frac{sC_c\,(1 - g_{m,out}R) - g_{m,out}}
          {s\,\bigl\{g_{m,out}C_c + s\,[\,CC_1 + C_c(C+C_1)\,]
           + s^2\,RC_cCC_1\bigr\}}

**Read the zero** off the top:

.. math::

   z = \frac{g_{m,out}}{C_c\,(1 - g_{m,out}R)}

**Make the zero cancel a pole.**  The lone :math:`s` in front of the bottom is
the dominant pole; with no output conductances it sits at 0.  The braces hold
the other poles.  A zero cancels a pole when the top and the bottom are both
zero at the same :math:`s`, so the braces must be zero at :math:`s = z`:

.. math::

   g_{m,out}C_c + z\,[\,CC_1 + C_c(C+C_1)\,] + z^2\,RC_cCC_1 = 0

To shorten the algebra, write :math:`u = 1 - g_{m,out}R`.  Then

.. math::

   z = \frac{g_{m,out}}{C_c\,u},
   \qquad
   g_{m,out}R = 1 - u

Put :math:`z` in.  The last term needs care: square :math:`z`, cancel one
:math:`C_c`, and replace :math:`g_{m,out}R` with :math:`1 - u`:

.. math::

   z^2\,RC_cCC_1
   = \frac{g_{m,out}^2}{C_c^2u^2}\,RC_cCC_1
   = \frac{g_{m,out}\,(g_{m,out}R)\,CC_1}{C_cu^2}
   = \frac{g_{m,out}\,(1-u)\,CC_1}{C_cu^2}

So the braces become:

.. math::

   g_{m,out}C_c
   + \frac{g_{m,out}}{C_c\,u}\,[\,CC_1 + C_c(C+C_1)\,]
   + \frac{g_{m,out}\,(1-u)\,CC_1}{C_c\,u^2} = 0

Multiply every term by :math:`C_c\,u^2/g_{m,out}` to clear the fractions:

.. math::

   C_c^2u^2 + [\,CC_1 + C_c(C+C_1)\,]\,u + (1-u)\,CC_1 = 0

Multiply out the last term.  The :math:`CC_1u` terms cancel:

.. math::

   C_c^2u^2 + \cancel{CC_1u} + C_c(C+C_1)\,u + CC_1 - \cancel{CC_1u} = 0

What is left factors:

.. math::

   C_c^2u^2 + C_c(C+C_1)\,u + CC_1 = (C_cu + C)\,(C_cu + C_1) = 0

So exactly two resistors put the zero on a pole.  Nothing was approximated:
:math:`C_1` stays in.

.. list-table::
   :header-rows: 1
   :widths: 28 30 42

   * - Condition
     - :math:`R`
     - Cancels
   * - :math:`C_c\,(1 - g_{m,out}R) = -C`
     - :math:`(C_c + C)/(g_{m,out}C_c)`
     - the output pole, the slow one that costs phase margin
   * - :math:`C_c\,(1 - g_{m,out}R) = -C_1`
     - :math:`(C_c + C_1)/(g_{m,out}C_c)`
     - the gate pole, which is fast and harmless

**Pick the output pole** and solve for :math:`R`:

.. math::

   \begin{aligned}
   C_c\,(1 - g_{m,out}R) &= -C \\
   g_{m,out}R &= 1 + \frac{C}{C_c} \\
   R &= \frac{1}{g_{m,out}} + \frac{C}{g_{m,out}\,C_c}
   \end{aligned}

At this :math:`R` the zero sits at :math:`z = -g_{m,out}/C`.

**Check.**  With this :math:`R` the top and the braces share the factor
:math:`g_{m,out} + sC`, which cancels:

.. math::

   \frac{v_{out}}{i_{in}}
   = \frac{-(g_{m,out} + sC)}
          {s\,C_c\,(g_{m,out} + sC)\,(1 + sRC_1)}
   = \frac{-1}{s\,C_c\,(1 + sRC_1)}

The zero has removed the output pole.  All :math:`C_1` leaves is one pole at
:math:`-1/(RC_1)`.

Where these methods sit in the code
-----------------------------------

.. code-block:: text

   find_stable_rnmc_design()             RNMC: choose gm2, Cc2, gm3
   ├── rnmc_min_gm2_for_damping()        where the gm2 search starts
   └── comp_resistances()                the resistors of each half
       └── nulling_resistor_ohm()

   size_resistors()                      the resistors in the netlist
   └── nulling_resistor_ohm()

   metrics (two-stage and nested Miller) GBW and phase margin
   └── nulling_resistor_ohm()

Sizing and metrics share one resistor formula, so the reported phase margin
belongs to the resistor that is built.

Reference
---------

.. autofunction:: circuitgenome.sizer.physics.equations.rnmc_min_gm2_for_damping
   :no-index:

.. autofunction:: circuitgenome.sizer.physics.equations.nulling_resistor_ohm
   :no-index:

.. autofunction:: circuitgenome.sizer.physics.rnmc.find_stable_rnmc_design
   :no-index:

.. autoclass:: circuitgenome.sizer.physics.rnmc.RnmcLoop
   :no-index:

Scope
-----

- **The numbers are illustrative.**  They use round values and ideal stages (no
  output conductance or parasitic unless stated), computed with the functions
  named above, not taken from a captured run.
  ``tests/test_rnmc.py::test_docs_compensation_design_rules`` checks them.
- **What the closed forms leave out:** output conductances, the parasitic on
  the first-stage output, a third-stage mirror, an output buffer and the
  resistors of section 3.  The open-loop model includes all of them; see
  :doc:`rnmc_stability`.
- **Nested Miller** has a different pole pair and its own rule
  (:math:`g_{m3} \ge 3.5\,g_{m2}`): :doc:`three_stage_phase_margin`.
