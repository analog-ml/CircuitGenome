RNMC Full Model: Poles, Damping and Phase Margin
================================================

*How the sizer judges a reversed-nested-Miller (RNMC) three-stage loop — and
the background needed to read that code.*

This page explains what
:func:`~circuitgenome.sizer.physics.equations.rnmc_pole_damping` and
:func:`~circuitgenome.sizer.physics.equations.phase_margin_rnmc_deg` compute,
why the sizer needs *both*, and how the code gets them from a small nodal
model of the amplifier.  The sizing strategy that uses them
(:func:`~circuitgenome.sizer.physics.rnmc.design_rnmc`) is described in
:ref:`rnmc-inner-pair`.

The running example is the frozen ptm45 FD fixture from ``tests/test_rnmc.py``
with the second stage raised to :math:`g_{m2} = 1.5` mS, and no third-stage
mirror or output buffer:

.. code-block:: text

   gm1 = 169 µS   gm2 = 1.5 mS   gm3 = 1.73 mS
   g1  = 0.5 µS   g2  = 15 µS    g3  = 37 µS        (stage output conductances)
   c1  = 1 fF     c2  = 160 fF                      (stage-output parasitics)
   Cc1 = 500 fF   Cc2 = 125 fF   CL  = 2 pF

----

Goal
----

For every candidate :math:`(g_{m2}, C_{c2}, g_{m3})` the sizer needs two
numbers:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Number
     - Answers
     - Why it is needed
   * - **Damping** :math:`\zeta`
     - Will the non-dominant **pole pair** ring, or go unstable?
     - The pair can resonate *above* the 0 dB crossing, where the phase
       margin does not look.
   * - **Phase margin**
     - How safe is the loop at the 0 dB crossing?
     - It is the spec, and SPICE verifies it.

Both come from one model of the circuit, solved numerically.

----

Background
----------

Poles
~~~~~

Every node that carries a capacitor contributes roughly one **pole** — a
frequency past which that capacitor starts to short the signal out.  Past each
pole the gain falls another 20 dB/decade and the phase lags up to another 90°.

Poles are the roots of the transfer function's denominator.  A circuit with
:math:`N` capacitive nodes has a denominator of degree :math:`N` (see
`Why the denominator is a cubic`_), so :math:`N` poles.  The lowest one is the
**dominant pole**; it sets the bandwidth.  The rest are **non-dominant**.

Pole pairs
~~~~~~~~~~

A single pole is the root of a *linear* factor :math:`1 + s/\omega_p`, which is
always real.  It bends the gain down smoothly and cannot ring.

Two poles that are coupled — because capacitors link their nodes — come out
together as the two roots of one *quadratic*:

.. math::

   1 + a_1 s + a_2 s^2 = 0

Its roots are either two real numbers (two ordinary poles, no ringing), or a
**complex-conjugate pair**

.. math::

   s = -\sigma \pm j\omega

which rings at :math:`\omega`.  That is a **pole pair**.  Complex roots of a
real polynomial always come in such pairs, so a denominator of any degree
factors into single real poles and pole pairs.

Physically: one water tank draining can only empty smoothly; two tanks joined
by a pipe can slosh back and forth.  Ringing needs two energy stores trading
energy.

Damping ratio ζ
~~~~~~~~~~~~~~~

Any pole pair can be written in the standard form

.. math::

   1 + \frac{2\zeta}{\omega_n}\,s + \frac{s^2}{\omega_n^2},
   \qquad
   s = -\zeta\omega_n \pm j\,\omega_n\sqrt{1-\zeta^2}

with two independent knobs: the **natural frequency** :math:`\omega_n` (how
fast) and the **damping ratio** :math:`\zeta` (how bouncy).  On the complex
plane :math:`\omega_n` is the poles' distance from the origin and
:math:`\zeta` is the cosine of their angle from the negative real axis:

.. figure:: /images/rnmc_pole_plane.svg
   :alt: A pole pair p = −σ ± jω on the complex plane, with the ray from the
         origin to the upper pole (length ωn), the angle θ it makes with the
         negative real axis, and the decay rate σ and ringing frequency ω as
         its projections onto the axes.
   :align: center
   :width: 600px

   A pole pair.  Its distance from the origin is :math:`\omega_n`; its angle
   :math:`\theta` from the negative real axis sets :math:`\zeta = \cos\theta`.
   Moving along the dotted circle keeps :math:`\omega_n` and changes only
   :math:`\zeta`: toward the imaginary axis :math:`\zeta \to 0` (rings
   forever), toward the real axis :math:`\zeta \to 1` (no ringing), and past
   the imaginary axis :math:`\zeta < 0` (unstable).

.. math::

   \zeta = \cos\theta = \frac{\sigma}{\omega_n} = \frac{-\mathrm{Re}(p)}{|p|}

:math:`\sigma/\omega_n` is the textbook form; :math:`-\mathrm{Re}(p)/|p|` is
the same thing written from the pole itself.  Reading the parts off
:math:`p = -\zeta\omega_n \pm j\,\omega_n\sqrt{1-\zeta^2}`: the real part
gives :math:`-\mathrm{Re}(p) = \zeta\omega_n = \sigma`, and the distance from
the origin is

.. math::

   |p| = \sqrt{(\zeta\omega_n)^2 + \omega_n^2(1-\zeta^2)}
       = \sqrt{\omega_n^2(\zeta^2 + 1 - \zeta^2)} = \omega_n

so :math:`-\mathrm{Re}(p)/|p| = \zeta\omega_n/\omega_n = \zeta`.  The code uses
the second form because a numerical solver returns each pole as one complex
number, not as :math:`\sigma` and :math:`\omega_n` separately:

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Textbook symbol
     - From the complex pole ``p``
   * - :math:`\sigma` (decay rate)
     - ``-p.real``
   * - :math:`\omega` (ringing frequency)
     - ``abs(p.imag)``
   * - :math:`\omega_n` (natural frequency)
     - ``abs(p)``
   * - :math:`\zeta`
     - ``-p.real / abs(p)``

Two textbook formulas tie :math:`\zeta` to what you would see on a scope or a Bode
plot:

.. math::

   \text{step overshoot} = e^{-\pi\zeta/\sqrt{1-\zeta^2}},
   \qquad
   \text{gain peak} = \frac{1}{2\zeta\sqrt{1-\zeta^2}}\quad(\zeta < 0.707)

.. list-table::
   :header-rows: 1
   :widths: 15 20 20 45

   * - :math:`\zeta`
     - Step overshoot
     - Gain peak
     - Meaning
   * - ≥ 1
     - 0 %
     - none
     - two real poles; no ringing
   * - **0.707**
     - 4 %
     - **none**
     - Butterworth — the fastest response with no peak (``ZETA_TARGET``)
   * - 0.5
     - 16 %
     - +1.2 dB
     - ``ZETA_FLOOR``
   * - 0.25
     - 44 %
     - +6.3 dB
     -
   * - 0.125
     - 67 %
     - +12 dB
     -
   * - 0
     - —
     - —
     - poles on the imaginary axis: rings forever (an oscillator)
   * - < 0
     - —
     - —
     - poles in the right half plane: ringing grows (unstable)

:math:`\zeta = 1/\sqrt{2} \approx 0.707` is the standard target for the
non-dominant pair of a three-stage amplifier: Leung & Mok size nested-Miller
compensation for a third-order Butterworth response, which places the pair
exactly there.

Which amplifiers have a pole pair
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Amplifier
     - Poles after the dominant one
     - Can they ring?
     - Phase margin enough?
   * - Single-stage OTA
     - 1 (mirror node)
     - no — a single pole is real
     - yes
   * - Two-stage Miller
     - 1 (output, :math:`\approx g_{m2}/C_L`)
     - no
     - yes
   * - Three-stage NMC / RNMC
     - 2, coupled through :math:`C_{c1}`, :math:`C_{c2}`
     - **yes — a pair**
     - **no**: also needs :math:`\zeta`

In a two-stage amplifier the two poles also come from one quadratic, but the
Miller capacitor *splits* them so far apart that they stay real.  In a
three-stage amplifier :math:`C_{c1}` splits off the dominant pole, and nothing
forces the remaining two apart.

Why phase margin alone is not enough
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Phase margin is read at one frequency: where the gain falls through 0 dB.  The
pair sits higher, and a lightly damped pair adds a resonance bump there:

.. code-block:: text

   gain (dB)
      │╲
      │  ╲                      ← dominant pole: gain falls steadily
    0 ┼────╲─────────────────── 0 dB
      │      ╲      ▲
      │        ╲   ╱ ╲          ← the pair's resonance (height set by ζ)
      │          ╲╱    ╲
      └─────┬───────┬──────────→ frequency
        PM read    ωn of the pair
        here       (the danger is here)

If the bump climbs back above 0 dB the loop crosses 0 dB a second time with the
phase already past −180°, and the amplifier oscillates at :math:`\omega_n`.

The fixture shows it.  With its third-stage mirror pole (840 MHz) in place and
only :math:`g_{m2}` varied:

.. list-table::
   :header-rows: 1
   :widths: 15 15 20 25 25

   * - :math:`g_{m2}`
     - :math:`\zeta`
     - Phase margin
     - Gain where phase = −180°
     - Verdict
   * - 0.23 mS
     - −0.08
     - 0°
     - —
     - unstable (as SPICE found)
   * - 1.0 mS
     - 0.08
     - −69°
     - +4.5 dB
     - oscillates
   * - **1.5 mS**
     - **0.13**
     - **82°**
     - **−1.3 dB**
     - looks excellent, is on the edge
   * - 5.0 mS
     - 0.32
     - 83°
     - −12.9 dB
     - fine
   * - 10 mS
     - 0.46
     - 83°
     - −17.3 dB
     - solid

At 1.5 mS the phase margin reads 82°, yet a ~10 % drop in :math:`g_{m2}` (one
process corner) makes it oscillate.  The phase margin barely moves between 1.5
and 10 mS while the real safety goes from 1.3 dB to 17 dB; :math:`\zeta`
tracks it.  The same failure hit nested Miller first: gf180 NMC designs rang at
~25 MHz while the open-loop bench read PM ≈ 88° (#247), and NMC sizing has
used a :math:`\zeta` rule since.

Why the denominator is a cubic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each node equation is first order in :math:`s`, because every capacitor current
is :math:`sCV`.  Solving :math:`N` coupled node equations divides by the
determinant of the :math:`N \times N` system (Cramer's rule), and each term of
a determinant takes one entry from each row — at most :math:`s^N`.  Three
nodes (:math:`V_1, V_2, V_3`) give a cubic; the third-stage mirror and the
output buffer add one node each (degree 4 or 5).  Rule of thumb: **number of
poles = number of nodes with capacitance**.

The textbook RNMC result (:ref:`rnmc-inner-pair`) simplifies that cubic.  Its
constant term is a product of the tiny output conductances
:math:`g_1 g_2 g_3`; setting it to zero ("high stage gains") factors out
:math:`s`:

.. math::

   s\,C_{c1} g_{m2} g_{m3}\,\bigl(1 + a_1 s + a_2 s^2\bigr)

— the dominant pole pushed to :math:`s \approx 0`, and the pair as the
remaining quadratic.  In the fixture the dominant pole sits at 34 Hz and the
pair at 314 MHz, seven decades apart, so the simplification is fair for the
textbook formula; the full model below keeps the exact polynomial anyway.

----

The full model, step by step
----------------------------

Step 1 — Shrink the amplifier to a small-signal model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Phase margin and :math:`\zeta` describe tiny wiggles around the bias point, and
for tiny wiggles a transistor is linear: a current source :math:`g_m v` plus an
output conductance :math:`g`.  Keeping only the gm's, the output conductances
and the capacitors that matter leaves the half circuit:

.. code-block:: text

              ┌──────────── Cc1 ────────────┐
              ├──── Cc2 ────┐               │
    Vin ─[gm1]─● V1 ──[gm2]─● V2 ──[gm3]────● V3 ── CL
              │g1,c1        │g2,c2          │g3
             gnd           gnd             gnd

Stage 1 and stage 2 invert; stage 3 does not.  SPICE would be far too slow
inside the sizer's search (dozens of candidates per design); it verifies the
final design instead.

Step 2 — One current equation per node
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Kirchhoff's current law at each node (currents leaving sum to zero):

.. math::

   \begin{aligned}
   V_1&:\; g_{m1}V_{in} + g_1V_1 + s c_1V_1 + sC_{c2}(V_1-V_2) + sC_{c1}(V_1-V_3) = 0\\
   V_2&:\; g_{m2}V_1 + g_2V_2 + s c_2V_2 + sC_{c2}(V_2-V_1) = 0\\
   V_3&:\; -g_{m3}V_2 + g_3V_3 + sC_LV_3 + sC_{c1}(V_3-V_1) = 0
   \end{aligned}

:math:`C_{c1}` and :math:`C_{c2}` tie :math:`V_1` to :math:`V_3` and
:math:`V_2`: the nodes depend on each other, so all three equations must be
solved **together**.

Step 3 — Write them as :math:`(G + sC)\,v = b`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Sort every term by the voltage it multiplies (the columns) and by whether it
carries :math:`s`: **G** holds the gm's and conductances, **C** the
capacitors, **v** is the unknown vector of node voltages, and **b** the input
term moved to the right-hand side.  With :math:`V_{in} = 1`:

.. code-block:: text

            G (µS)                       C (fF)                v        b (µS)
   ⎛ [  0.5     0      0 ]        [  626  −125  −500 ] ⎞   [ V1 ]   [ −169 ]
   ⎜ [ 1500    15      0 ]  + s · [ −125   285     0 ] ⎟ · [ V2 ] = [    0 ]
   ⎝ [   0   −1730    37 ]        [ −500     0  2500 ] ⎠   [ V3 ]   [    0 ]
        V1     V2     V3              V1    V2    V3

Multiplying out row 2 gives back the :math:`V_2` equation:
:math:`(1500 - 125s)V_1 + (15 + 285s)V_2 = 0`, i.e.
:math:`(g_{m2} - sC_{c2})V_1 + (g_2 + s(c_2 + C_{c2}))V_2 = 0`.

Splitting off :math:`s` lets the same two tables serve every frequency, and
gives the poles directly (Step 4).  In the code each physical part is one line;
two helpers write its numbers into the right cells, so the builder reads like
the schematic:

.. code-block:: python

   cap(V1, V3, cc1_f)     # Cc1 between V1 and V3: ±Cc1 in four cells of C
   cap(V1, V2, cc2_f)     # Cc2 between V1 and V2
   vccs(V2, V1, gm2)      # stage 2: current gm2·V1 drawn out of V2
   vccs(V3, V2, -gm3)     # stage 3: current gm3·V2 pushed into V3
   cap(V3, None, cl_f)    # CL to ground

**Mirror.** A third-stage current mirror delays gm3 by a pole at
:math:`\omega_m`.  It becomes one extra node :math:`V_m` with
:math:`V_m(1 + s/\omega_m) = V_2`, and :math:`V_m` (instead of :math:`V_2`)
drives gm3.

**Buffer.** A source-follower output buffer becomes one extra node
:math:`V_{out}` after :math:`V_3`: :math:`C_L` moves onto it, the follower's
:math:`C_{gs}` links it to :math:`V_3`, and it is driven by
:math:`g_{m,f}(V_3 - V_{out})`.  The follower then sits inside the
:math:`C_{c1}` loop, which matters: a follower driving a capacitor presents a
negative input resistance above :math:`g_{m,f}/C_L`.

Steps 4–6 are unchanged by the extra nodes; the tables just grow to 4×4 or 5×5.

Step 4 — Find the poles
~~~~~~~~~~~~~~~~~~~~~~~

The poles are the values of :math:`s` where :math:`(G + sC)` cannot be solved,
:math:`\det(G + sC) = 0` — numerically, the eigenvalues of :math:`-C^{-1}G`.
No polynomial has to be derived by hand:

.. code-block:: python

   poles = np.linalg.eigvals(-np.linalg.solve(C, G))

For the example (in Hz, :math:`p/2\pi`):

.. code-block:: text

   −34 Hz                     ← dominant pole (real)
   −91.7 MHz ± j·300 MHz      ← the pole pair, |p| = 314 MHz

Step 5 — Read ζ off the poles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.physics.equations.rnmc_pole_damping` drops the
dominant (smallest) pole and reports the **worst** :math:`\zeta` among the
rest; a real pole counts as :math:`\zeta = 1`:

.. code-block:: python

   non_dominant = sorted(poles, key=abs)[1:]
   zeta = min(-p.real / abs(p) for p in non_dominant)

For the example :math:`-\mathrm{Re}(p) = 91.7` MHz and
:math:`|p| = \sqrt{91.7^2 + 300^2} = 313.8` MHz, so
:math:`\zeta = 91.7 / 313.8 = 0.29`: stable (positive) but ringing (below
0.7), so the sizer would keep raising :math:`g_{m2}`.  With the mirror or
buffer there may be more than one pair; taking the worst covers them all.

Two properties of the ``-p.real / abs(p)`` form matter here:

- **The sign comes for free.**  A right-half-plane pole has a positive real
  part, so it gives :math:`\zeta < 0` — that is how
  :func:`~circuitgenome.sizer.physics.equations.rnmc_pole_damping` reports an
  unstable pair, with no separate check.
- **Real poles read as 1.**  A real pole gives :math:`-p/|p| = 1`, so an
  *overdamped* pair (true :math:`\zeta > 1`, two real roots) reads 1, not its
  true :math:`\zeta`.  That is harmless: the code takes the worst value and
  only compares it with 0.5 and 0.7, so anything at 1 counts as well damped.

Step 6 — Sweep the frequency for the phase margin
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.physics.equations.phase_margin_rnmc_deg` solves
the system at 4000 log-spaced frequencies, from two decades below the lowest
pole to two decades above the highest — one batched call:

.. code-block:: python

   v = np.linalg.solve(G + 1j * w * C, b)    # all node voltages, every frequency
   gain = v[out]                             # Vin = 1, so V_out is the gain

and reads the margin :math:`180° + \angle A` at each 0 dB crossing.  For the
example:

.. code-block:: text

     56 MHz   −0.1 dB    −98°   ← 0 dB crossing: PM = 180° − 98° = 82°
    300 MHz   −8.4 dB   −179°   ← the pair: the gain stops falling

The phase margin (82°) is read at 56 MHz; only :math:`\zeta = 0.29` reports the
pair at 300 MHz.  The rules for the edge cases:

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Situation
     - Result
   * - Any pole in the right half plane
     - ``0.0`` — unstable, no margin to speak of
   * - The gain crosses 0 dB more than once
     - the **worst** margin over all crossings — negative when the pair's
       bump comes back above 0 dB past −180°
   * - The gain never reaches 0 dB
     - ``None``
   * - A stage output conductance of 0
     - floored at :math:`10^{-9} g_m`, which keeps the DC gain finite without
       moving anything near crossover

----

Closed forms and the full model
-------------------------------

The textbook closed forms come from the simplified quadratic
:math:`1 + a_1 s + a_2 s^2` (:ref:`rnmc-inner-pair`).  Matching it to the
standard form gives the pair's damping and natural frequency,

.. math::

   \zeta = \frac{a_1}{2\sqrt{a_2}}, \qquad \omega_n = \frac{1}{\sqrt{a_2}},

and :math:`\zeta > 0` exactly when :math:`a_1 > 0`, i.e. the stability
condition :math:`g_{m3} < g_{m2}(1 + C_L/C_{c1})`.  On the bare loop (no
mirror, buffer or output conductances) the full model reproduces both — it
flips sign at that boundary and reads :math:`\zeta = 0.7` where the closed form
does — and ``tests/test_rnmc.py`` checks it.  Only one closed form is code:

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Function
     - Computes
     - Sees mirror, buffer, conductances?
   * - :func:`~circuitgenome.sizer.physics.equations.rnmc_min_gm2`
     - the closed-form :math:`\zeta` solved for the smallest :math:`g_{m2}`
       reaching a target — where the sizer's :math:`g_{m2}` search starts
     - no
   * - :func:`~circuitgenome.sizer.physics.equations.rnmc_pole_damping`
     - :math:`\zeta` from the full model's poles (Steps 1–5)
     - **yes**
   * - :func:`~circuitgenome.sizer.physics.equations.phase_margin_rnmc_deg`
     - phase margin from the full model's sweep (Steps 1–4, 6)
     - **yes**

The closed forms explain the physics and give the search a good start; the
full model decides.  The difference is real: a buffered gf180 candidate looks
stable with :math:`C_L` on the third stage, yet with the source follower in
place the pair lands in the right half plane — and SPICE's settling bench saw
it ring (``test_output_follower_inside_the_cc1_loop_is_seen``).

Further reading
---------------

- K. N. Leung and P. K. T. Mok, "Analysis of multistage amplifier-frequency
  compensation," *IEEE Transactions on Circuits and Systems I*, Sept. 2001 —
  nested-Miller design for a third-order Butterworth response
  (:math:`\zeta \approx 0.707` for the non-dominant pair).
- A. D. Grasso, G. Palumbo and S. Pennisi — analytical design and comparison of
  reversed-nested-Miller compensation for three-stage amplifiers.
