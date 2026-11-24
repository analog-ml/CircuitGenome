Single-Stage Phase Margin
=========================

*Where* :math:`\text{PM} = 90° - \arctan(\text{GBW}/f_{mirror})` *comes from.*

A single-stage OTA has no Miller capacitor: the load capacitor ``CL`` is the
compensation.  The sizer judges its stability with
:func:`~circuitgenome.sizer.physics.equations.phase_margin_single_stage_deg`.
This page derives that one-line formula from a single RC pole, in four steps:

1. **One pole and its phase lag** — what an RC low-pass does to a sine wave.
2. **The pole frequency** — rewriting that lag in terms of one number,
   :math:`f_p`.
3. **Where to look** — the frequency at which the loop gain reaches unity.
4. **Add it up** — the two poles of a single-stage OTA give the formula.

What phase margin *is* (and why 180° is the dangerous delay) is explained in
:doc:`rnmc_stability`, section *Feedback, oscillation and phase margin*.

----

1. One pole and its phase lag
-----------------------------

Start with the simplest circuit that has a pole, an RC low-pass:

.. code-block:: text

   Vin ──/\/\/──┬── Vout
          R     │
               ═╧═ C
                │
   ─────────────┴───

Its transfer function, and its value for a sine wave at angular frequency
:math:`\omega` (substitute :math:`s = j\omega`), are

.. math::

   H(s) = \frac{V_{out}}{V_{in}} = \frac{1}{1 + sRC}
   \qquad\Rightarrow\qquad
   H(j\omega) = \frac{1}{1 + j\omega RC}

The denominator :math:`1 + j\omega RC` is a complex number.  Draw any complex
number :math:`z = a + jb` as a point in the plane; it makes a right triangle
with the real axis:

.. code-block:: text

    Im
     ▲
   b ┤ - - - - - ● z = a + jb
     │         ╱ │
     │   |z| ╱   │ b   (perpendicular)
     │     ╱     │
     │   ╱ θ     │
     └──●────────┴──► Re
        0    a (base)

Its **phase angle** :math:`\theta` is the angle at the origin.  From the
triangle, :math:`\tan\theta = \text{perpendicular}/\text{base}`, so

.. math::

   \theta = \arctan\!\left(\frac{b}{a}\right)

For :math:`1 + j\omega RC`, :math:`a = 1` and :math:`b = \omega RC`, so its
phase angle is :math:`\arctan(\omega RC)`.

**When you divide complex numbers, their phase angles are subtracted.**  The
numerator :math:`1` has angle 0°, so the circuit's phase is

.. math::
   :label: rc-phase

   \phi(\omega) = 0 - \arctan(\omega RC) = -\arctan(\omega RC)

The minus sign is the **phase lag**: the output sine wave arrives late.  It
grows from 0° at low frequency to −90° at high frequency — which is why one
pole costs *at most* 90°.

2. The pole frequency
---------------------

Name the frequency where :math:`\omega RC = 1`: it is the **pole's angular
frequency**, and dividing by :math:`2\pi` gives the ordinary frequency in Hz:

.. math::

   \omega_p = \frac{1}{RC}
   \qquad\Rightarrow\qquad
   f_p = \frac{1}{2\pi RC}

Write :eq:`rc-phase` with :math:`\omega = 2\pi f` and divide top and bottom by
:math:`2\pi`:

.. math::

   \phi(f) = -\arctan\!\left(\frac{2\pi f}{1/RC}\right)
           = -\arctan\!\left(\frac{f}{f_p}\right)

This is the only formula the rest of the page needs.  A pole's lag depends
only on **how far the frequency you look at is from the pole**: at
:math:`f = f_p` it is exactly −45°; a decade below, about −6°; a decade above,
about −84°.

3. Where to look: the unity-gain frequency
------------------------------------------

Phase margin asks how much phase lag the poles add **at the frequency where
the amplifier's loop gain reaches unity** (0 dB).  For a single-pole-dominated
amplifier that frequency is the gain-bandwidth product, so

.. math::

   f = f_t = \text{GBW}

A single-stage OTA has two poles worth counting:

.. list-table::
   :header-rows: 1
   :widths: 22 38 40

   * - Pole
     - Where it is
     - Its frequency
   * - **Output (main) pole**
     - the output node, which drives ``CL``
     - very low: ``CL`` is large and the output resistance is high
   * - **Current-mirror pole**
     - the mirror's gate node, inside the OTA
     - :math:`f_{mirror} = \dfrac{1}{2\pi\,R_{mirror}\,C_{mirror}}`

For the mirror pole, the R and C are both transistor quantities:

- :math:`R_{mirror}` is the **resistance of the mirror's diode-connected
  transistor**, :math:`1/g_m`.  Its gate is tied to its drain, so a voltage
  :math:`v` on that node makes a current :math:`g_m v` flow — it behaves like
  a resistor of :math:`1/g_m`.
- :math:`C_{mirror}` is the **transistors' parasitic capacitance** on that
  node: the gate capacitance :math:`C_{gs}` of every transistor whose gate is
  tied to it (the diode device itself and the devices copying its current).

So the code computes

.. math::

   f_{mirror} = \frac{g_m}{2\pi \sum C_{gs}}

in :func:`~circuitgenome.sizer.physics.stage_chain._mirror_pole_hz`.  It
counts only gate capacitance, not the smaller drain-junction capacitance.

4. Add it up
------------

Each pole lags by :math:`\arctan(f/f_p)`, and the lags add.  Phase margin is
what is left of 180° at the GBW:

.. math::

   \text{PM} &= 180° - \arctan\!\left(\frac{\text{GBW}}{f_{out}}\right)
                     - \arctan\!\left(\frac{\text{GBW}}{f_{mirror}}\right) \\
             &\approx 180° - 90° - \arctan\!\left(\frac{\text{GBW}}{f_{mirror}}\right)
               \quad\text{(GBW} \gg f_{out}\text{)} \\
             &= 90° - \arctan\!\left(\frac{\text{GBW}}{f_{mirror}}\right)

The output pole sits far below the GBW (GBW is the DC gain times
:math:`f_{out}`), so by the GBW it has taken almost its full 90°: with 60 dB of
DC gain, :math:`\arctan(1000) = 89.94°`.  The code rounds this to exactly
90° — that is the ``remaining_phase_deg = 180.0 - 90.0`` line — and computes
the mirror pole's lag as ``phase_lag_deg``.

Worked examples
---------------

The same formula covers a healthy design, a borderline one and a failing one:

.. list-table::
   :header-rows: 1
   :widths: 16 16 16 18 34

   * - GBW
     - :math:`f_{mirror}`
     - Mirror lag
     - PM
     - Reading
   * - 2 MHz
     - 1 GHz
     - 0.11°
     - **89.89°**
     - Mirror pole 500× above the GBW: almost no lag.  The usual case, since
       ``CL`` is far larger than any gate capacitance.
   * - 33.3 MHz
     - 100 MHz
     - 18.43°
     - **71.57°**
     - GBW a third of the mirror pole: still passes a 60° spec.
   * - 100 MHz
     - 100 MHz
     - 45.00°
     - **45.00°**
     - GBW pushed onto the mirror pole: fails a 60° spec.  This is the case
       the formula is here to catch.

The first and last rows are the cases checked in
``tests/test_sizer.py::test_single_stage_phase_margin_formula``; the middle row
is an illustrative value.

Reference
---------

.. autofunction:: circuitgenome.sizer.physics.equations.phase_margin_single_stage_deg
   :no-index:

.. autofunction:: circuitgenome.sizer.physics.stage_chain._mirror_pole_hz
   :no-index:

Scope
-----

- **No mirror, no phase margin.**  When the load has no current mirror (a
  resistor load), the stage has one pole in this model and the phase margin
  would be 90° for every sizing.  :func:`~circuitgenome.sizer.physics.stage_chain._mirror_pole_hz`
  returns ``None`` there, and the sizer reports no phase margin rather than a
  number that passes any spec.
- **Two-stage Miller op-amps** use
  :func:`~circuitgenome.sizer.physics.equations.phase_margin_two_stage_deg`
  instead.  There the Miller capacitor sets the main pole and the *output* is
  the second pole — the roles of ``CL`` and the internal node are swapped, so
  the two cannot share a formula.
- **Three-stage RNMC loops** are judged from a small nodal model; see
  :doc:`rnmc_stability`.
