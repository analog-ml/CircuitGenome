Two-Stage Phase Margin
======================

*Where* :math:`\text{PM} = 90° - \arctan\!\left(\dfrac{g_{m1} C_L}{g_{m2} C_c}\right)`
*comes from.*

A two-stage op-amp is compensated with a **Miller capacitor** :math:`C_c`
connected across its second stage.  The sizer judges its stability with
:func:`~circuitgenome.sizer.physics.equations.phase_margin_two_stage_deg`.

When an op-amp has two poles, we usually assume the main (dominant) pole has
already used up 90° of phase lag by the unity-gain frequency, so what remains
is :math:`180° - 90° = 90°`.  The second pole then takes away its own lag
:math:`\phi_2`:

.. math::

   \text{PM} = 180° - 90° - \phi_2,
   \qquad
   \phi_2 = \arctan\!\left(\frac{\omega_t}{\omega_{p2}}\right)

:math:`\phi_2` is the phase lag of the second pole **at the unity-gain
frequency** :math:`\omega_t`.  Why one pole lags by
:math:`\arctan(\omega/\omega_p)` is derived from an RC low-pass in
:doc:`single_stage_phase_margin`, steps 1–2.

So we need to find just two numbers, in two steps:

1. **Find** :math:`\omega_t` — where the gain falls to 1.
2. **Find** :math:`\omega_{p2}` — where the second pole sits.

----

1. Find the unity-gain frequency ωt
-----------------------------------

The two stages, with :math:`C_c` across the second one:

.. code-block:: text

                         Cc
                ┌────────┤├────────┐
                │                  │
   vin ──▷ gm1 ─┴── node 1 ──▷ gm2 ─┴── vout ──┬──
                                               ═╧═ CL

A MOSFET stage turns a voltage change into a current:

.. math::
   :label: ts-gm

   i_{out} = g_{m1}\, v_{in}

That current goes into :math:`C_c`.  It all goes there because the second
stage has high gain: node 1 barely moves while :math:`v_{out}` swings, so
:math:`C_c` is the easiest path, and the voltage across it is essentially
:math:`v_{out}`.  A capacitor's current is

.. math::
   :label: ts-cap

   i_c = C_c\, \frac{dV_{out}}{dt}

Setting :eq:`ts-gm` equal to :eq:`ts-cap`:

.. math::

   g_{m1}\, v_{in} = C_c\, \frac{dV_{out}}{dt}

The Laplace transform turns :math:`d/dt` into a multiplication by :math:`s`
(:math:`dV_{out}/dt \to s\,V_{out}(s)`):

.. math::

   g_{m1}\, V_{in}(s) = s\, C_c\, V_{out}(s)
   \qquad\Rightarrow\qquad
   \frac{V_{out}(s)}{V_{in}(s)} = \frac{g_{m1}}{s\, C_c}

With :math:`s = j\omega`, the gain's size is

.. math::

   |A(j\omega)| = \frac{g_{m1}}{\omega\, C_c}

(The second stage inverts, so the true gain has a minus sign; it does not
change the size.)  **Unity gain** means :math:`|A(j\omega)| = 1`, so

.. math::

   \omega_t = \frac{g_{m1}}{C_c}
   \qquad\text{or}\qquad
   f_t = \frac{g_{m1}}{2\pi\, C_c} = \text{GBW}

This is :func:`~circuitgenome.sizer.physics.equations.unity_gain_bw`.

2. Find the second pole ωp2
---------------------------

The second stage, with transconductance :math:`g_{m2}`, drives the output load
:math:`C_L`.  Like any pole, it sits at :math:`1/RC`:

.. math::

   \omega_{p2} = \frac{1}{RC} = \frac{1/R}{C} \approx \frac{g_{m2}}{C_L}

Here :math:`C = C_L`, and the resistance seen at the output is
:math:`R = 1/g_{m2}`.  This is the Miller capacitor's second effect, called
**pole splitting**.  Near :math:`\omega_t`, :math:`C_c` is close to a short
circuit, so it ties the second-stage transistor's gate (node 1) to its drain
(the output).  A transistor with gate tied to drain is diode-connected, and
looks like a resistor of :math:`1/g_{m2}` — much smaller than the stage's
normal output resistance.  So :math:`C_c` pushes the main pole *down* and this
second pole *up*, far apart from each other.

The :math:`\approx` hides one assumption: :math:`C_c` and :math:`C_L` are much
larger than the parasitic capacitance on node 1.

3. Add it up
------------

.. math::

   \text{PM} &= 180° - 90° - \arctan\!\left(\frac{\omega_t}{\omega_{p2}}\right) \\
             &= 90° - \arctan\!\left(\frac{g_{m1}/C_c}{g_{m2}/C_L}\right) \\
             &= 90° - \arctan\!\left(\frac{g_{m1}\, C_L}{g_{m2}\, C_c}\right)

As with the single-stage formula, the main pole's lag at :math:`\omega_t` is
just under 90°, and the code rounds it to exactly 90°.

Read the result as a race between two frequencies: the GBW
(:math:`g_{m1}/C_c`) and the second pole (:math:`g_{m2}/C_L`).  The further the
second pole sits above the GBW, the closer PM gets to 90°.  For a 60° margin
the second pole must sit at :math:`\tan 60° = 1.73\times` the GBW.

Used backwards: choosing gm2
----------------------------

The sizer mostly uses this formula in reverse.  Given :math:`g_{m1}`,
:math:`C_c`, :math:`C_L` and the spec's minimum phase margin, it solves for
the smallest second-stage :math:`g_{m2}`:

.. math::

   g_{m2} \ge \frac{g_{m1}\, C_L}{C_c\, \tan(90° - \text{PM}_{min})}

That is the gm2 rule in
:func:`~circuitgenome.sizer.physics.preprocess.compute_requirements`;
:doc:`sizing_flow`, *Step 5*, works through it with numbers.

Worked examples
---------------

All three use :math:`C_L` = 20 pF:

.. list-table::
   :header-rows: 1
   :widths: 13 13 11 14 14 13 22

   * - :math:`g_{m1}`
     - :math:`g_{m2}`
     - :math:`C_c`
     - GBW
     - :math:`f_{p2}`
     - PM
     - Reading
   * - 70 µA/V
     - 200 µA/V
     - 4 pF
     - 2.79 MHz
     - 1.59 MHz
     - **29.74°**
     - Second pole *below* the GBW: fails any usual spec.
   * - 51.96 µA/V
     - 630 µA/V
     - 2.857 pF
     - 2.90 MHz
     - 5.01 MHz
     - **60.00°**
     - Second pole 1.73× the GBW: exactly the 60° the gm2 rule aims for.
   * - 51.96 µA/V
     - 1890 µA/V
     - 2.857 pF
     - 2.90 MHz
     - 15.0 MHz
     - **79.11°**
     - Three times the gm2: the second pole moves 3× further out.

The first row uses the values in ``tests/test_sizer.py::test_phase_margin_formula``;
the second is the Step 5 example of :doc:`sizing_flow`; the third is
illustrative.

What the formula leaves out
---------------------------

The two-pole picture is a sizing estimate, not the number the sizer reports.
It leaves out:

- **Cc's right-half-plane zero** at :math:`g_{m2}/C_c`.  The signal can pass
  *forward* through :math:`C_c` to the output, which adds phase lag rather
  than the lead an ordinary zero gives.
- **The parasitic capacitance on node 1**, assumed small in step 2.
- **The first stage's current-mirror pole** (the one
  :doc:`single_stage_phase_margin` is built on).

Together they cost a design sized to this formula 2–5° (issue #274).  So for
a two-stage op-amp the sizer reports the phase margin read off the
open-loop model,
:class:`~circuitgenome.sizer.physics.open_loop_response.OpenLoopResponse`, and its
post-sizing re-plan raises :math:`g_{m2}` to win those degrees back.  The
closed form still sets the first :math:`g_{m2}`, and is the fallback metric
for a three-stage chain that has no second Miller capacitor.

Reference
---------

.. autofunction:: circuitgenome.sizer.physics.equations.phase_margin_two_stage_deg
   :no-index:

.. autofunction:: circuitgenome.sizer.physics.equations.unity_gain_bw
   :no-index:

Scope
-----

- **No ringing.**  Pole splitting puts the two poles far apart, so the second
  pole is an ordinary real pole and one :math:`\arctan` term is the whole
  story — there is no pole pair to ring.
- **Single-stage OTAs** have no :math:`C_c`; ``CL`` sets the main pole and the
  current mirror the second.  See :doc:`single_stage_phase_margin`.
- **Three-stage op-amps** have two non-dominant poles that *can* form a
  ringing pair; see
  :doc:`three_stage_phase_margin` and :doc:`rnmc_stability`.
