RNMC Full Model: Poles, Damping and Phase Margin
================================================

*How the sizer decides whether a reversed-nested-Miller (RNMC) three-stage
amplifier is stable and well-behaved — and the background needed to read that
code.*

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

Feed a sine wave into a linear circuit and a sine wave of the same frequency
comes out — larger or smaller, and delayed.  The **transfer function**
:math:`V_{out}/V_{in}` captures both effects at every frequency at once.  It is
written in the frequency variable :math:`s`, and solving the circuit's node
equations always gives a ratio of two polynomials in :math:`s`:

.. math::

   \frac{V_{out}}{V_{in}} = \frac{N(s)}{D(s)}

To ask what happens to a sine wave at frequency :math:`\omega`, substitute
:math:`s = j\omega`.  The reason: for a sine wave, taking a time derivative is
the same as multiplying by :math:`j\omega` — a capacitor's current
:math:`C\,dv/dt` becomes :math:`j\omega C v` — and every :math:`s` in the
formula stands for that derivative.  The result is one complex number:

- its **size** is the gain (how much the wave grows or shrinks);
- its **angle** is the phase (how much the wave is delayed).

The :math:`j` is what produces the phase: multiplying by :math:`j` rotates a
complex number by 90°, which is why :math:`1/(j\omega)` has an angle of −90°.
The only rule the arithmetic below needs is :math:`j^2 = -1`.

Poles
~~~~~

The **poles** are the roots of the denominator :math:`D(s)`; the roots of the
numerator :math:`N(s)` are the **zeros**.  Near a pole the response is large,
because the denominator is close to zero.  Each pole bends the gain plot down
by another 20 dB/decade and adds up to another 90° of phase lag.

Every node that carries a capacitor contributes roughly one pole.  Each node
equation is first order in :math:`s` (every capacitor current is :math:`sCV`);
solving :math:`N` coupled node equations divides by the determinant of the
:math:`N \times N` system (Cramer's rule), and each term of a determinant takes
one entry from each row — at most :math:`s^N`.  So the rule of thumb is
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
always a real number.  It bends the gain down smoothly and cannot ring.

Two poles that are coupled — because capacitors link their nodes — come out
*together*, as the two roots of one quadratic:

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

Physically, one water tank draining can only empty smoothly; two tanks joined
by a pipe can slosh back and forth.  Ringing needs two energy stores trading
energy — a pole pair is two of them, coupled.

**The textbook pole pair: a series R–L–C.**  With the output taken across the
capacitor, the circuit is a voltage divider:

.. math::

   \frac{V_{out}}{V_{in}} = \frac{1/(sC)}{R + sL + 1/(sC)}
   = \frac{1}{1 + sRC + s^2 LC}

(multiply top and bottom by :math:`sC`).  The whole transfer function is one
over a quadratic, with :math:`a_1 = RC` and :math:`a_2 = LC`; its numerator is
exactly 1 because at DC the capacitor is open and passes the input straight
through.  Small :math:`R` gives a pair that rings; large :math:`R` gives two
real poles.

**Which amplifiers have a pole pair.**

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

ζ on the complex plane
~~~~~~~~~~~~~~~~~~~~~~

The naming pays off in the roots.  The poles are the values of :math:`s` that
make the denominator zero; call such a value :math:`p` (for *pole*).
Multiplying :math:`1 + (2\zeta/\omega_n)s + s^2/\omega_n^2 = 0` through by
:math:`\omega_n^2` and applying the quadratic formula:

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

.. math::

   \zeta = \cos\theta = \frac{\sigma}{\omega_n} = \frac{-\mathrm{Re}(p)}{|p|}

:math:`\sigma/\omega_n` is the textbook form; :math:`-\mathrm{Re}(p)/|p|` is
the same thing written from the pole itself.  The real part gives
:math:`-\mathrm{Re}(p) = \zeta\omega_n = \sigma`, and the distance from the
origin is

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

What ζ means in practice
~~~~~~~~~~~~~~~~~~~~~~~~

Two textbook formulas tie :math:`\zeta` to what you would see on a scope or a
Bode plot:

.. math::

   \text{step overshoot} = e^{-\pi\zeta/\sqrt{1-\zeta^2}},
   \qquad
   \text{gain peak} = \frac{1}{2\zeta\sqrt{1-\zeta^2}}\quad(\zeta < 0.707)

At exactly :math:`\omega = \omega_n` the pair's gain is :math:`1/(2\zeta)`.

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

----

Why phase margin alone is not enough
------------------------------------

The bump
~~~~~~~~

Phase margin is read at one frequency: where the gain falls through 0 dB.  The
pole pair sits higher, and a lightly damped pair adds a resonance bump there —
right where its own phase lag pushes the loop to −180°:

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

If the bump climbs back above 0 dB, the loop crosses 0 dB a second time with
the phase already past −180°, and the amplifier oscillates at
:math:`\omega_n`.  The phase margin, measured at the first crossing, never sees
it; the gain margin and :math:`\zeta` do.

A minimal example
~~~~~~~~~~~~~~~~~

The same effect in a loop small enough to work by hand.

**The loop.**  Just the two parts of the sketch, one after the other — which,
for transfer functions, means multiplied:

.. math::

   A(s) = \underbrace{\frac{1}{s}}_{\text{dominant pole}}
          \times \underbrace{H(s)}_{\text{pole pair}}

- :math:`1/s` is the dominant pole, idealised to sit at zero.  Its gain
  :math:`1/\omega` falls tenfold per decade and equals 1 (0 dB) at
  :math:`\omega = 1` — where this loop crosses 0 dB.  Its phase is −90° at
  every frequency.
- :math:`H(s)` is the pole pair in standard form, placed ten times above the
  crossing: :math:`\omega_n = 10`, so :math:`2\zeta/\omega_n = 0.2\zeta` and
  :math:`1/\omega_n^2 = 0.01`:

  .. math::

     H(s) = \frac{1}{1 + 0.2\zeta\, s + 0.01\, s^2}

  Nothing new — the standard form with one number substituted, so all the
  arithmetic that follows is plain numbers.

**The danger point: ω = 10.**  The loop is at risk where its total phase
reaches −180°.  :math:`1/s` always contributes −90°, and the pair contributes
exactly −90° at its own frequency, :math:`\omega = 10` — so evaluate there,
:math:`s = j\cdot 10`.  The pair's denominator, term by term:

.. math::

   \begin{aligned}
   0.01\, s^2 &= 0.01\,(j\cdot 10)^2 = 0.01\cdot(-100) = -1\\
   0.2\zeta\, s &= 0.2\zeta\cdot j\cdot 10 = j\cdot 2\zeta\\
   1 + 0.2\zeta\, s + 0.01\, s^2 &= 1 + j\cdot 2\zeta - 1 = j\cdot 2\zeta
   \end{aligned}

The 1 and the −1 cancel — that happens exactly at the pair's own frequency.
What is left is

.. math::

   H(j\cdot 10) = \frac{1}{j\cdot 2\zeta}
   \quad\Rightarrow\quad |H| = \frac{1}{2\zeta},\ \angle H = -90°

— the bump, taller as :math:`\zeta` shrinks (ζ = 0.1 gives a gain of 5).
Times the :math:`1/s` part, :math:`1/(j\cdot 10)` (size 1/10, angle −90°):

.. math::

   |A(j\cdot 10)| = \frac{1}{10}\cdot\frac{1}{2\zeta} = \frac{1}{20\zeta},
   \qquad \angle A(j\cdot 10) = -180°

The loop oscillates when its gain at −180° is at least 1:

.. math::

   \frac{1}{20\zeta} > 1 \iff \zeta < \frac{1}{20} = 0.05

The 20 is the 10 (the pair is ten times above the crossing) times the 2 of the
bump height :math:`1/(2\zeta)`.

**The phase margin: ω = 1.**  Phase margin is :math:`180° + \angle A` where the
gain is 1, which is at :math:`\omega \approx 1`, so evaluate at
:math:`s = j\cdot 1`:

.. math::

   \begin{aligned}
   0.01\, s^2 &= 0.01\cdot(-1) = -0.01\\
   0.2\zeta\, s &= j\cdot 0.2\zeta\\
   1 + 0.2\zeta\, s + 0.01\, s^2 &= 0.99 + j\cdot 0.2\zeta
   \end{aligned}

Nearly 1 — the pair barely touches the signal here
(:math:`|H(j\cdot 1)| = 1/\sqrt{0.99^2 + (0.2\zeta)^2} \approx 1.01`, which is
why the gain still crosses 1 at :math:`\omega \approx 1`).  Its small angle is
all the pair costs at the crossing.  A complex number :math:`x + jy` has angle
:math:`\arctan(y/x)`, and dividing by it gives minus that angle:

.. math::

   \angle H(j\cdot 1) = -\arctan\frac{0.2\zeta}{0.99}
   \qquad\Rightarrow\qquad
   \text{PM} = 180° - 90° - \arctan\frac{0.2\zeta}{0.99}
   = 90° - \arctan\frac{0.2\zeta}{0.99}

**The numbers.**  The phase margin from this formula, the gain at −180° from
:math:`1/(20\zeta)`; a numerical sweep of :math:`A(j\omega)` agrees with both:

.. list-table::
   :header-rows: 1
   :widths: 10 18 22 50

   * - :math:`\zeta`
     - Phase margin
     - Gain at −180°
     - Verdict
   * - 0.5
     - 84.2°
     - −20 dB
     - solid
   * - 0.2
     - 87.7°
     - −12 dB
     - fine
   * - 0.1
     - 88.8°
     - −6 dB
     - getting close
   * - 0.06
     - **89.3°**
     - **−1.6 dB**
     - on the edge
   * - 0.04
     - **89.5°** at the first crossing
     - **+1.9 dB**
     - oscillates — the bump lifts the gain back above 0 dB, adding two more
       crossings around :math:`\omega = 10`; the one just past it has a
       margin of −33° (from the sweep), and
       :func:`~circuitgenome.sizer.physics.equations.phase_margin_rnmc_deg`,
       which reports the worst crossing, would report that

Two things to notice:

- **Phase margin gets better as the design gets worse** — 84° → 88° → 89° →
  89.5°.  The pair's lag at the crossing, :math:`\arctan(0.2\zeta/0.99)`,
  shrinks with :math:`\zeta`: a low-:math:`\zeta` pair packs its phase change
  tightly around its own frequency.  Steering by phase margin alone would walk
  the design straight into oscillation.
- **The gain at −180° tells the truth**, and it is exactly
  :math:`1/(20\zeta)` — :math:`\zeta` is the knob that controls it.  That is
  why the sizer steers by :math:`\zeta`.

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
The constant could go in either factor; keeping it all in the :math:`1/s` part
is what leaves :math:`H` in standard form, :math:`H(0) = 1`.  The toy measures
frequency in units of :math:`\omega_t` (so the crossing sits at 1) and places
the pair at :math:`10\,\omega_t`; in the fixture of
`From theory to code`_ the pair sits about 5.6 × above the crossing.

The simplification drops two things the real loop has: the dominant pole is
not exactly at zero (in the fixture it sits at 34 Hz, seven decades below the
pair at 314 MHz, so that is fair), and the numerator also contains :math:`s`
— **zeros**, from signal leaking forward through the Miller caps.  The full
model keeps both: it solves :math:`V_{out}` directly from the node equations.

**The fixture shows the same thing.**  The frozen ptm45 FD design from
``tests/test_rnmc.py``, with its third-stage mirror pole (840 MHz) in place and
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
process corner) makes it oscillate: at 1.40 mS the margin still reads 82.2°,
at 1.35 mS it is −20°.  The phase margin gives no warning, and barely moves
between 1.5 and 10 mS while the real safety goes from 1.3 dB to 17 dB;
:math:`\zeta` slides smoothly and tracks it.  The same failure hit nested
Miller first: gf180 NMC designs rang at ~25 MHz while the open-loop bench read
PM ≈ 88° (#247), and NMC sizing has used a :math:`\zeta` rule since.

----

From theory to code
-------------------

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

Both come from one model of the circuit — the *full model* — solved
numerically.  The steps below run it on the frozen ptm45 FD fixture from
``tests/test_rnmc.py``, with the second stage raised to
:math:`g_{m2} = 1.5` mS and no third-stage mirror or output buffer:

.. code-block:: text

   gm1 = 169 µS   gm2 = 1.5 mS   gm3 = 1.73 mS
   g1  = 0.5 µS   g2  = 15 µS    g3  = 37 µS        (stage output conductances)
   c1  = 1 fF     c2  = 160 fF                      (stage-output parasitics)
   Cc1 = 500 fF   Cc2 = 125 fF   CL  = 2 pF

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
