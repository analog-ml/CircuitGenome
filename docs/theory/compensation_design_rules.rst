Compensation Design Rules: the RNMC gm2 Floor and the Nulling Resistor
======================================================================

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
   * - :func:`~circuitgenome.sizer.physics.equations.rnmc_min_gm2`
     - What is the smallest :math:`g_{m2}` that stops the two extra poles from
       ringing?
     - RNMC, no output buffer
   * - :func:`~circuitgenome.sizer.physics.equations.compensation_resistor_ohm`
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

1. Two coupled poles: the closed forms
--------------------------------------

The rest of this section derives the damping :math:`\zeta`, in four steps:

1. solve the circuit for its denominator;
2. factor the denominator into the dominant pole and the pole pair;
3. tidy the pair's two coefficients, :math:`a_1` and :math:`a_2`;
4. read the damping :math:`\zeta` off them.

Step 1 — Solve the circuit
~~~~~~~~~~~~~~~~~~~~~~~~~~

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

Step 4 — Read the damping
~~~~~~~~~~~~~~~~~~~~~~~~~

Dividing by :math:`A` left the bracket starting with 1, so it matches the
standard form of a pole pair term by term:

.. math::

   1 + a_1\,s + a_2\,s^2
   \quad\leftrightarrow\quad
   1 + \frac{2\zeta}{\omega_n}\,s + \frac{1}{\omega_n^2}\,s^2

The :math:`s^2` terms give :math:`\omega_n = 1/\sqrt{a_2}`, and the :math:`s`
terms then give

.. math::

   \zeta = \frac{a_1\,\omega_n}{2} = \frac{a_1}{2\sqrt{a_2}}

:math:`\sqrt{a_2}` is always positive, so :math:`\zeta` takes the sign of
:math:`a_1`, and the sign of :math:`a_1` is the tug-of-war:

.. math::

   \underbrace{(C_L+C_{c1})\,g_{m2}}_{\text{stage 2 adds damping}}
   \;-\;
   \underbrace{C_{c1}\,g_{m3}}_{\text{stage 3 takes it away}}

The two terms are the two loops of the sign table.  Multiplied by the
:math:`C_{c2}` in front of the bracket in :math:`B`, the second is
:math:`C_{c1}C_{c2}\,g_{m3}`: both capacitors and stage 3, but no
:math:`g_{m2}`, because that loop bypasses stage 2.

A positive difference means stable: the bounce dies out eventually.  How *well*
it dies out is a second question, with a stricter answer (:math:`\zeta = 0.7`).

With numbers
~~~~~~~~~~~~

With :math:`g_{m2}` = 0.4 mS, :math:`g_{m3}` = 1 mS, :math:`C_{c1}` = 2 pF,
:math:`C_{c2}` = 1 pF, :math:`C_L` = 8 pF and no parasitic:

.. code-block:: text

   A  = 2 pF · 0.4 mS · 1 mS                        = 8.0e-19
   B  = 1 pF · (10 pF · 0.4 mS − 2 pF · 1 mS)       = 2.0e-27
   E  = 8 pF · 2 pF · 1 pF                          = 1.6e-35

   a1 = B / A                                       = 2.5e-9
   a2 = E / A                                       = 2.0e-17
   ζ  = 2.5e-9 / (2 · √2.0e-17) = 2.5e-9 / 8.94e-9  = 0.28

Stage 2 adds more damping than stage 3 takes away, so :math:`\zeta` is positive
and the pair is stable.  But 0.28 is far from 0.7, so it rings.  Being stable is
not enough, so section 2 asks for more.

2. The second-stage floor: ``rnmc_min_gm2``
-------------------------------------------

Section 1 gives :math:`\zeta` for a known :math:`g_{m2}`.  This function runs
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

By hand
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

Worked examples
~~~~~~~~~~~~~~~

The same circuit at other second-stage strengths.  Both :math:`\zeta` columns
agree; the last column is
:func:`~circuitgenome.sizer.physics.open_loop_response.phase_margin_rnmc_deg`
at :math:`g_{m1}` = 50 µS:

.. list-table::
   :header-rows: 1
   :widths: 14 16 18 16 36

   * - :math:`g_{m2}`
     - ζ (closed form)
     - ζ (open-loop model)
     - Phase margin
     - Reading
   * - 0.1 mS
     - −0.28
     - −0.28
     - 0°
     - Unstable: stage 3 takes away more damping than stage 2 adds.
   * - 0.4 mS
     - 0.28
     - 0.28
     - 82.7°
     - Stable, but it rings, and the phase margin does not show it.
   * - 0.987 mS
     - **0.70**
     - **0.70**
     - 82.8°
     - The floor: damped.

With a parasitic :math:`C_2` = 0.5 pF, ``k`` grows to 1.81 and the floor rises
to 1.51 mS.

Where the sizer uses it
~~~~~~~~~~~~~~~~~~~~~~~

The floor is a starting point, not the final answer.
:func:`~circuitgenome.sizer.physics.rnmc.design_rnmc` starts its
:math:`g_{m2}` search there and raises :math:`g_{m2}` until the open-loop
model, which includes everything left out here, reaches the same
:math:`\zeta` and meets the phase-margin spec.  Behind an output buffer the
third stage no longer drives :math:`C_L`, so the formula does not apply and
the search starts from the current :math:`g_{m2}`.  :ref:`rnmc-inner-pair` has
the full strategy.

3. The nulling resistor: ``compensation_resistor_ohm``
------------------------------------------------------

The previous section chose a stage's strength.  This one deals with a side
effect of the capacitor itself.

The problem: a capacitor conducts both ways
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A compensation cap is there to carry the output *back* to the stage's input.
At high frequency it also carries the input *forward*, past the stage:

.. code-block:: text

        ┌──── Cc ─── R ────┐      forward path: not inverted
        │                  │
   vin ─●── ▷ gm_out ──────●── vout
            (inverts)     ═╧═ C

The stage inverts and the shortcut does not, so the two fight.  Where they
cancel exactly there is a **zero**, and without a resistor it costs phase
(a *right-half-plane* zero).

Where the zero lands
~~~~~~~~~~~~~~~~~~~~

At the zero the output does not move, so the cap branch must deliver exactly
the current the transistor draws:

.. math::

   g_{m,out}\,v_{in} = \frac{v_{in}}{R + 1/(sC_c)}
   \quad\Rightarrow\quad
   z = \frac{g_{m,out}}{C_c\,(1 - g_{m,out}R)}

.. list-table::
   :header-rows: 1
   :widths: 30 25 45

   * - Resistor
     - Zero
     - Effect
   * - :math:`R = 0`
     - :math:`+g_{m,out}/C_c`
     - right half plane: costs phase
   * - :math:`R = 1/g_{m,out}`
     - at infinity
     - **nulled**: the shortcut is cancelled
   * - :math:`R > 1/g_{m,out}`
     - negative
     - left half plane: gives phase back

The rule
~~~~~~~~

The output node has a pole near :math:`-g_{m,out}/C`
(:doc:`two_stage_phase_margin`, step 2).  Put the zero on top of it and the two
cancel:

.. math::

   \frac{g_{m,out}}{C_c\,(1 - g_{m,out}R)} = -\frac{g_{m,out}}{C}
   \quad\Rightarrow\quad
   R = \frac{C_c + C}{g_{m,out}\,C_c}
     = \underbrace{\frac{1}{g_{m,out}}}_{\text{null the shortcut}}
     + \underbrace{\frac{C}{g_{m,out}\,C_c}}_{\text{cancel the output pole}}

A two-stage example with :math:`g_{m1}` = 0.1 mS, :math:`g_{m,out}` = 1 mS,
:math:`C_c` = 2 pF, :math:`C = C_L` = 8 pF and 0.1 pF on the first stage's
output puts :math:`g_{m,out}/C` at 19.9 MHz.  Phase margins are from
:func:`~circuitgenome.sizer.physics.open_loop_response.two_stage_loop`:

.. list-table::
   :header-rows: 1
   :widths: 34 22 20 24

   * - Resistor
     - Zero
     - Phase margin
     - Reading
   * - 0
     - +79.6 MHz
     - 63.0°
     - the shortcut costs phase
   * - 1 kΩ = :math:`1/g_{m,out}`
     - none
     - 68.4°
     - shortcut nulled
   * - 5 kΩ = :math:`(C_c+C_L)/(g_{m,out}C_c)`
     - −19.9 MHz
     - 88.6°
     - output pole cancelled too

Which C to use
~~~~~~~~~~~~~~

The formula is always the same; the caller chooses ``c_out_f``:

.. list-table::
   :header-rows: 1
   :widths: 34 30 36

   * - Amplifier
     - ``c_out_f``
     - Resistor
   * - Two-stage and nested Miller
     - :math:`C_L`
     - :math:`(C_c+C_L)/(g_{m,out}C_c)`
   * - RNMC, no output buffer
     - 0
     - :math:`1/g_{m,out}`
   * - RNMC with an output buffer
     - the buffer's gate capacitance
     - just above :math:`1/g_{m,out}`

RNMC is different because the larger resistor backfires there:

- **Behind a buffer** it added a pole inside the inner loop, and designs
  oscillated (issue #262; :doc:`rnmc_buffered_oscillation` covers the related
  bare-capacitor case).
- **Without a buffer** it held the loop gain near 0 dB until it crossed again
  with almost no phase left, and designs rang at about 28 MHz (issue #269).

The section 2 circuit shows the second case.  It is sized at the 1.51 mS floor
for :math:`C_2` = 0.5 pF, with :math:`g_{m1}` = 0.2 mS, a resistor in series
with :math:`C_{c1}` only, and small losses added (0.1 pF at :math:`V_1`; output
conductances 1, 10 and 10 µS):

.. list-table::
   :header-rows: 1
   :widths: 30 14 20 36

   * - Resistor in series with :math:`C_{c1}`
     - ζ
     - Phase margin
     - Reading
   * - 0
     - 0.70
     - 59.6°
     - the section 2 design, as sized
   * - 1 kΩ = :math:`1/g_{m3}`
     - 0.51
     - 64.8°
     - more margin, some damping spent
   * - 5 kΩ = :math:`(C_{c1}+C_L)/(g_{m3}C_{c1})`
     - 0.30
     - **3.9°**
     - on the edge of oscillation

The resistor is not free even at :math:`1/g_{m3}`, so
:func:`~circuitgenome.sizer.physics.rnmc.design_rnmc` scores every candidate
with its resistors in place.

Every caller passes the **last gain stage's** gm as ``gm_out``.  For RNMC's
:math:`C_{c2}`, which wraps stage 2, the resistor is therefore
:math:`1/g_{m3}` and not :math:`1/g_{m2}`, so its zero is nulled exactly only
when the two are equal.  The open-loop model includes the actual resistors, so
it sees the difference.

What calls what
---------------

.. code-block:: text

   design_rnmc()                         RNMC: choose gm2, Cc2, gm3
   ├── rnmc_min_gm2()                    where the gm2 search starts
   └── comp_resistances()                the resistors of each half
       └── compensation_resistor_ohm()

   size_resistors()                      the resistors in the netlist
   └── compensation_resistor_ohm()

   metrics (two-stage and nested Miller) GBW and phase margin
   └── compensation_resistor_ohm()

Sizing and metrics share one resistor formula, so the reported phase margin
belongs to the resistor that is built.

Reference
---------

.. autofunction:: circuitgenome.sizer.physics.equations.rnmc_min_gm2
   :no-index:

.. autofunction:: circuitgenome.sizer.physics.equations.compensation_resistor_ohm
   :no-index:

.. autofunction:: circuitgenome.sizer.physics.rnmc.design_rnmc
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
