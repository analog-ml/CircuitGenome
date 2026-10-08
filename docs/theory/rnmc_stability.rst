RNMC Stability: Phase Margin and Damping
========================================

*Why a reversed-nested-Miller (RNMC) three-stage loop can pass the
phase-margin check and still ring, and how the sizer computes both numbers.*

A three-stage amplifier can pass the usual phase-margin check and still ring,
or even oscillate.  For every RNMC design it sizes, the sizer therefore
computes two numbers — the **phase margin** and the **damping** :math:`\zeta`
of a *pole pair* — with
:func:`~circuitgenome.sizer.physics.equations.phase_margin_rnmc_deg` and
:func:`~circuitgenome.sizer.physics.equations.rnmc_pole_damping`.  The sizing
strategy that uses them
(:func:`~circuitgenome.sizer.physics.rnmc.design_rnmc`) is described in
:ref:`rnmc-inner-pair`.

The page builds up in order:

1. **Background** — transfer functions, poles, and what phase margin measures.
2. **Intuition** — why two coupled poles can ring.
3. **Definition** — the damping ratio :math:`\zeta` that measures it.
4. **Why phase margin alone is not enough** — a pencil-and-paper example, then
   the real amplifier.
5. **From theory to code** — how the sizer computes both numbers, step by step.

----

Background: transfer functions, poles and phase margin
------------------------------------------------------

Transfer functions and s = jω
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The **transfer function**
:math:`V_{out}/V_{in}` captures (1) how mucch the input signal grows or shrinks
and (2) how much it is delayed at every frequency at once.  It is
written in the frequency variable :math:`s`. The transfer function is often obtained by
solving the circuit's node
equations always gives a ratio of two polynomials in :math:`s`:

.. math::

   \frac{V_{out}}{V_{in}} = \frac{N(s)}{D(s)}

To ask what happens to a sine wave at frequency :math:`\omega`, substitute
:math:`s = j\omega`.  

Poles
~~~~~

The **poles** are the roots of the denominator :math:`D(s)`; the roots of the
numerator :math:`N(s)` are the **zeros**.  Each pole contributes a 20 dB/decade drop in gain and adds up to another 90° of phase lag.

Every node that carries a capacitor contributes roughly one pole.  The rule of thumb is
**number of poles = number of nodes with capacitance**: a three-stage amplifier
(three stage outputs) has a cubic denominator and three poles.
The lowest pole is the **dominant pole**: it sets the bandwidth.  The rest are
**non-dominant** poles.

Feedback, oscillation and phase margin
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An op-amp is used inside a **feedback loop**: part of the output is fed back
and subtracted from the input.  The **open-loop gain** :math:`A(s)` describes
what happens to a signal going once around that loop.

The feedback *subtracts*.  If, at some frequency, the loop also delays the
signal by half a cycle (−180°), the subtraction turns into an addition: the
signal comes back reinforcing itself.  If it also comes back at least as large
as it left (:math:`|A| \ge 1`, i.e. 0 dB), it keeps itself going with no input
at all — the amplifier **oscillates**, like a microphone squealing next to its
speaker.

Two standard checks measure how far a loop is from that condition:

.. list-table::
   :header-rows: 1
   :widths: 20 45 35

   * - Check
     - Question it answers
     - Where it is read
   * - **Phase margin**
     - When the signal comes back the same size, how far is its delay from
       the dangerous −180°?  :math:`\text{PM} = 180° + \angle A`.
     - where :math:`|A|` crosses 0 dB
   * - **Gain margin**
     - When the delay reaches −180°, how much smaller than 1 is the signal?
     - where :math:`\angle A = -180°`

For most amplifiers the phase margin alone is enough: the gain falls through
0 dB once and keeps falling, so by the time the delay reaches −180° the signal
is far too small to matter.  The rest of this page is about the case where that
reasoning breaks.

----

Intuition: pole pairs and ringing
---------------------------------

A single pole is the root of a *linear* factor :math:`1 + s/\omega_p`, which is
always a real number.  It contributes a 20 dB/decade drop in gain and a 90° phase lag, but it does not ring.
Following the same reasoning, a second-order factor is a *quadratic*:
  

.. math::

   1 + a_1 s + a_2 s^2 = 0

A quadratic's roots are either two real numbers (two ordinary poles, no
ringing), or a **complex-conjugate pair**

.. math::

   s = -\sigma \pm j\omega

which rings at the frequency :math:`\omega`.  That is a **pole pair**.
Complex roots of a polynomial with real coefficients always come in such
pairs, so a denominator of any degree factors into single real poles and pole
pairs.

----

Definition: the damping ratio ζ
-------------------------------

The standard form
~~~~~~~~~~~~~~~~~

As the R–L–C shows, a pole pair's quadratic sits in the denominator, so the
pair's transfer function is one over it:

.. math::

   H(s) = \frac{1}{1 + a_1 s + a_2 s^2}

:math:`a_1` and :math:`a_2` are just numbers.  Engineers rewrite them with two
knobs that have a physical meaning:

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Knob
     - Meaning
     - Relation to the coefficients
   * - :math:`\omega_n`, **natural frequency**
     - *how fast*: the frequency where the pair acts
     - :math:`a_2 = 1/\omega_n^2`
   * - :math:`\zeta`, **damping ratio**
     - *how bouncy*: how much it rings
     - :math:`a_1 = 2\zeta/\omega_n`

Substituting gives the **standard form** of a pole pair:

.. math::

   H(s) = \frac{1}{1 + (2\zeta/\omega_n)\,s + s^2/\omega_n^2}

It is the same quadratic, written with :math:`\omega_n` and :math:`\zeta`
instead of :math:`a_1` and :math:`a_2`; going back the other way,

.. math::

   \omega_n = \frac{1}{\sqrt{a_2}}, \qquad \zeta = \frac{a_1}{2\sqrt{a_2}}

At :math:`s = 0`, :math:`H = 1`: the pair passes low frequencies unchanged, and
:math:`H` describes only where it acts and how it bounces.


.. math::

   s^2 + 2\zeta\omega_n\, s + \omega_n^2 = 0
   \quad\Rightarrow\quad
   s = -\zeta\omega_n \pm \omega_n\sqrt{\zeta^2 - 1}

For :math:`\zeta < 1` the square root is of a negative number,
:math:`\sqrt{\zeta^2 - 1} = j\sqrt{1 - \zeta^2}`, so the two poles are a
complex pair — the :math:`-\sigma \pm j\omega` of
`Intuition: pole pairs and ringing`_, with :math:`\sigma = \zeta\omega_n`:

.. math::

   p = -\zeta\omega_n \pm j\,\omega_n\sqrt{1-\zeta^2}

On the complex plane, every pole is at distance :math:`\omega_n` from the
origin, and :math:`\zeta` is the cosine of the poles' angle from the negative
real axis:

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

we can write the damping ratio directly from the pole itself:

.. math::

   \zeta = \cos\theta = \frac{\sigma}{\omega_n} = \frac{-\mathrm{Re}(p)}{|p|}

and because 

.. math::

   |p| = \sqrt{(\zeta\omega_n)^2 + \omega_n^2(1-\zeta^2)}
       = \sqrt{\omega_n^2(\zeta^2 + 1 - \zeta^2)} = \omega_n

so :math:`-\mathrm{Re}(p)/|p| = \zeta\omega_n/\omega_n = \zeta`.  So when we use numpy to find the poles of a transfer function, we can read :math:`\zeta` straight from the complex pole :math:`p`:

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

What ζ means in practice
~~~~~~~~~~~~~~~~~~~~~~~~
ζ tells us how bouncy the pole pair is.  A low-ζ pair rings a lot, and a high-ζ pair does not. 

----

The real RNMC amplifier
~~~~~~~~~~~~~~~~~~~~~~~

**The toy is the RNMC loop, simplified.**  A three-stage amplifier has a cubic
denominator (`Poles`_).  The textbook RNMC result (:ref:`rnmc-inner-pair`)
simplifies it: its constant term is a product of the tiny output conductances
:math:`g_1 g_2 g_3`, and setting that to zero ("high stage gains") factors out
:math:`s`:

.. math::

   D(s) \approx s\,C_{c1} g_{m2} g_{m3}\,\bigl(1 + a_1 s + a_2 s^2\bigr)

— the dominant pole pushed to :math:`s \approx 0`, and the pole pair as the
remaining quadratic.  With the numerator about :math:`g_{m1} g_{m2} g_{m3}`,
the :math:`g_{m2} g_{m3}` cancels:

.. math::

   \frac{V_{out}}{V_{in}}
   = \frac{g_{m1} g_{m2} g_{m3}}{s\,C_{c1} g_{m2} g_{m3}\,(1 + a_1 s + a_2 s^2)}
   = \underbrace{\frac{g_{m1}/C_{c1}}{s}}_{1/s\ \text{part}}
     \times \underbrace{\frac{1}{1 + a_1 s + a_2 s^2}}_{H(s)}

So :math:`A(s) = (1/s)\times H(s)` *is* the amplifier's
:math:`V_{out}/V_{in}`, written as a product: the :math:`1/s` part carries the
dominant pole **and all the gain** (it crosses 0 dB at
:math:`\omega_t = g_{m1}/C_{c1}`), and :math:`H(s)` carries the pole pair.


----

Step-by-step: how the sizer computes :math:`\zeta` and phase margin
-------------------------------------------------------------------

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
:math:`1 + a_1 s + a_2 s^2` (:ref:`rnmc-inner-pair`).  As in
`The standard form`_, its damping and natural frequency are

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
