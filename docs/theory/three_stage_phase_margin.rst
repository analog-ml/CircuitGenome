Three-Stage Phase Margin
========================

*Where* :math:`\text{PM} = 90° - \arctan(\omega_t/\omega_{p2}) - \arctan(\omega_t/\omega_{p3})`
*comes from, and why it is only a sizing estimate.*

A three-stage **nested-Miller (NMC)** op-amp has two Miller capacitors.  The
outer one, :math:`C_{c1}`, wraps stages 2 and 3; the inner one,
:math:`C_{c2}`, wraps stage 3 alone:

.. code-block:: text

                 ┌───────────────── Cc1 ─────────────────┐
                 │               ┌─────── Cc2 ───────────┤
                 │               │                       │
   vin ──▷ gm1 ──┴── n1 ──▷ gm2 ─┴── n2 ──▷ gm3 ─────────┴── vout ──┬──
                          (non-inverting)  (inverting)              ═╧═ CL

Stage 3 inverts and stage 2 does not, so both caps feed back with the sign
that stabilizes (negative feedback).

The formula is the two-stage one with one more pole.  The main pole has
already used up 90° of phase lag by the unity-gain frequency, and each of the
two other poles takes away its own lag there:

.. math::

   \text{PM} = 180° - 90°
               - \arctan\!\left(\frac{\omega_t}{\omega_{p2}}\right)
               - \arctan\!\left(\frac{\omega_t}{\omega_{p3}}\right)

So three numbers are needed: :math:`\omega_t`, :math:`\omega_{p2}` and
:math:`\omega_{p3}`.  Each one reuses a step from
:doc:`two_stage_phase_margin`, so read that page first.

----

1. The unity-gain frequency ωt
------------------------------

.. math::

   \omega_t = \frac{g_{m1}}{C_{c1}}

This is two-stage step 1 with :math:`C_{c1}` in place of :math:`C_c`.  Stages
2 and 3 together act as one high-gain stage, so node ``n1`` barely moves, and
the first stage's current :math:`g_{m1} v_{in}` all goes into :math:`C_{c1}`.

2. The inner pole ωp2
---------------------

.. math::

   \omega_{p2} = \frac{1}{RC} = \frac{1/R}{C} \approx \frac{g_{m2}}{C_{c2}}

Here :math:`R = 1/g_{m2}` and :math:`C = C_{c2}`.  The easiest way to read
:math:`g_{m2}/C_{c2}` is to notice that stages 2 and 3 with :math:`C_{c2}`
form a **two-stage Miller amplifier of their own**.  By two-stage step 1,
that inner amplifier's own unity-gain frequency is :math:`g_{m2}/C_{c2}`.
Below it, the inner amplifier still has gain, and that gain is what keeps
``n1`` still for the outer loop.  Above it, the inner amplifier runs out of
gain, which shows up as the next pole.

3. The output pole ωp3
----------------------

.. math::

   \omega_{p3} = \frac{1}{RC} = \frac{1/R}{C} \approx \frac{g_{m3}}{C_L}

This is two-stage step 2 (pole splitting) applied to the last stage.  Near
:math:`\omega_t`, the Miller caps tie stage 3's input to its output, so its
transistor is diode-connected and looks like :math:`R = 1/g_{m3}` driving
:math:`C = C_L`.

4. Add it up
------------

.. math::

   \text{PM} = 90°
       - \arctan\!\left(\frac{g_{m1}\, C_{c2}}{g_{m2}\, C_{c1}}\right)
       - \arctan\!\left(\frac{g_{m1}\, C_L}{g_{m3}\, C_{c1}}\right)

As on the other pages, the main pole's lag at :math:`\omega_t` is just under
90°, and the code rounds it to exactly 90°.

Used backwards: choosing gm2 and gm3
------------------------------------

The sizer never calls this function, but it uses the same two-pole picture in
reverse.  :func:`~circuitgenome.sizer.physics.preprocess.compute_requirements`
**splits the lag budget equally**: each of the two poles may take at most

.. math::

   \theta = \frac{90° - \text{PM}_{min}}{2}

which gives one lower bound per stage:

.. math::

   g_{m2} \ge \frac{g_{m1}\, C_{c2}}{C_{c1} \tan\theta}
   \qquad
   g_{m3} \ge \frac{g_{m1}\, C_L}{C_{c1} \tan\theta}

For a 60° spec, :math:`\theta = 15°`, so each pole must sit at
:math:`1/\tan 15° = 3.73\times\,\omega_t`.  :doc:`sizing_flow`, *Phase margin
derivation*, has the full three-stage sizing table.

The catch: the two poles are a pair
-----------------------------------

Steps 2 and 3 treat :math:`\omega_{p2}` and :math:`\omega_{p3}` as two
separate, real poles.  They are not.  Solving the circuit gives one quadratic
for both (Leung & Mok):

.. math::

   1 + s\,\frac{C_{c2}\,(g_{m3} - g_{m2})}{g_{m2}\, g_{m3}}
     + s^2\,\frac{C_L\, C_{c2}}{g_{m2}\, g_{m3}}

Its roots come out close to :math:`g_{m2}/C_{c2}` and :math:`g_{m3}/C_L` only
when those two are far apart, which needs
:math:`g_{m3}\, C_{c2} \gg g_{m2}\, C_L`.  Otherwise they are a **pole pair**
that can ring (see :doc:`rnmc_stability` for pole pairs and damping).  How much it
rings is set by the damping ratio

.. math::

   \zeta = \frac{r-1}{2}\sqrt{\frac{C_{c2}}{r\, C_L}},
   \qquad r = \frac{g_{m3}}{g_{m2}}

The PM formula cannot see :math:`\zeta`.  A design can show a healthy phase
margin and still ring after every step (issue #208).  So for NMC the sizer
also floors :math:`g_{m3} \ge 3.5\, g_{m2}` and sizes :math:`C_{c2}` for
:math:`\zeta = 0.3`.

Worked examples
---------------

Both rows use :math:`g_{m1}` = 100 µA/V, :math:`C_{c1}` = 2 pF
(:math:`f_t` = 7.96 MHz), :math:`C_{c2}` = 0.5 pF and :math:`C_L` = 10 pF.
"Open-loop model" is
:class:`~circuitgenome.sizer.physics.open_loop_response.OpenLoopResponse`, wired as
:data:`~circuitgenome.sizer.physics.open_loop_response.NESTED_MILLER`, with ideal
stages (no parasitic capacitance), which treats the poles as the pair they
are:

.. list-table::
   :header-rows: 1
   :widths: 12 12 6 13 13 9 13 22

   * - :math:`g_{m2}`
     - :math:`g_{m3}`
     - r
     - Lags
     - PM (formula)
     - ζ
     - PM (open-loop model)
     - Reading
   * - 93.3 µA/V
     - 1866 µA/V
     - 20
     - 15.0° + 15.0°
     - **60.00°**
     - 0.48
     - 72.83°
     - The equal-split rule at a 60° spec.  Damped above the sizer's 0.3
       target, but the two poles are not far apart, so the formula is
       pessimistic.
   * - 1250 µA/V
     - 2500 µA/V
     - 2
     - 1.1° + 11.3°
     - **77.54°**
     - 0.08
     - 88.85°
     - Both PMs look excellent, but ζ = 0.08 rings: the #208 failure.

These numbers are illustrative.  They were computed with this function, the
:math:`\zeta` formula above and ``OpenLoopResponse``, not taken from a
captured run.

What the formula leaves out
---------------------------

- **The pole pair** and its damping, above.
- **Parasitics, an output buffer, and the compensation resistors' zeros.**
  In SPICE the formula ran 15–35° pessimistic (issue #274).  So the sizer
  reports the phase margin from
  :class:`~circuitgenome.sizer.physics.open_loop_response.OpenLoopResponse`, which
  includes them.
- **Reversed nested Miller (RNMC).**  There both caps start at ``n1``, and
  the pair can even sit in the right half plane.  This formula is the wrong
  one for it.  On the RNMC fixture in ``tests/test_rnmc.py`` it reads 58.2°
  while the open-loop model reads 0° (unstable).  RNMC is wired as
  :data:`~circuitgenome.sizer.physics.open_loop_response.REVERSED_NESTED_MILLER`, see
  :doc:`rnmc_stability`.

Reference
---------

.. autofunction:: circuitgenome.sizer.physics.equations.phase_margin_three_stage_deg
   :no-index:

.. autoclass:: circuitgenome.sizer.physics.open_loop_response.OpenLoopResponse
   :members: gbw_hz, phase_margin_deg, damping
   :no-index:

Scope
-----

- **Two-stage op-amps** have one Miller cap and one extra pole; see
  :doc:`two_stage_phase_margin`.
- **Single-stage OTAs** have no Miller cap; see
  :doc:`single_stage_phase_margin`.
- **Damping and pole pairs** in depth, including RNMC: :doc:`rnmc_stability`.
