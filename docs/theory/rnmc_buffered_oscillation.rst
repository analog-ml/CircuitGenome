Why Buffered RNMC Op-Amps Oscillated
====================================

*The analog background behind issue #266: why some buffered
reversed-nested-Miller (RNMC) op-amps oscillated in SPICE although the sizer
accepted them, and what the fixes (#267, #268) change.*

On GF180 (the #256 spec, seed-0 sample) 11 of 100 sized
``three_stage_opamp_rnmc_buffered_fully_differential`` designs kept ringing
after a small disturbance.  Every one had a PMOS second stage, a
``noninverting_stage_nmos`` third stage and a bare inner Miller cap.  This page
builds up the ideas needed to see why, one at a time, and ends with how each
fix maps onto them.  :doc:`rnmc_stability` has the full stability math; this
page is the intuition.

----

1. Gain stages and transconductance
-----------------------------------

A transistor used as an amplifier turns a small input *voltage* into an output
*current*.  Its **transconductance** :math:`g_m` says how much:
:math:`g_m = 0.6` mS means 1 mV in gives 0.6 µA out.  That current flows into
the stage's output node, whose resistance :math:`R` turns it back into a
voltage, so one stage's voltage gain is about :math:`g_m R`.

One stage gives 30–40 dB.  An op-amp needs far more, so it chains stages —
three here, followed by a **source follower**:

.. code-block:: text

    in ─[stage 1]── V1 ─[stage 2]── V2 ─[stage 3]── V3 ─[follower]── out ── CL (5 pF)
          gm1              gm2              gm3          gain ≈ 1

The follower has a voltage gain of about one.  It is an *output buffer*: it
drives the heavy load so the gain stages do not have to.

2. Poles: every node is a small delay
-------------------------------------

Every node has some capacitance — wires, the gates and drains of the
transistors on it — charged through some resistance.  That RC is a **pole**: a
frequency above which the node's signal loses gain (−20 dB per decade) and
lags behind (up to −90° of phase).  More stages mean more poles, and more
delay in total.

3. Feedback, phase margin and damping
-------------------------------------

An op-amp always works inside a feedback loop that *subtracts* part of the
output from the input.  If, where the loop gain has fallen to one (0 dB), the
total delay has reached −180°, the subtraction has turned into an addition:
the loop feeds itself and oscillates.

- **Phase margin** (PM) is how far the delay at that crossing still is from
  −180°.  The spec asks for at least 55°; a small PM rings, 0° or less
  oscillates.
- Two coupled nodes can form a **pole pair** that behaves like a mass on a
  spring.  Its **damping** :math:`\zeta` says how it settles:

  ========================  ==============================================
  :math:`\zeta`             behaviour after a kick
  ========================  ==============================================
  ≈ 0.7                     settles cleanly — the sizer's target
  between 0 and 0.5         rings for a while, then settles
  below 0                   every wiggle *grows*: the circuit oscillates
                            on its own, from noise
  ========================  ==============================================

  A negative :math:`\zeta` means the pair sits in the **right half plane**
  of the pole map below.  #266's designs had an inner pair at about 120 MHz
  with :math:`\zeta \approx -0.08`: each cycle 2.7× larger than the last.

.. figure:: /images/rnmc_pole_plane.svg
   :alt: Poles in the complex plane; damping is the cosine of the angle from the negative real axis
   :width: 70%

   A pole pair's damping is read off its angle; a pair right of the vertical
   axis grows instead of decaying (see :doc:`rnmc_stability`).

4. Miller compensation and the right-half-plane zero
----------------------------------------------------

To tame a multi-stage amplifier, a capacitor :math:`C_c` goes from a stage's
output back to its input — **Miller compensation**.  The stage's gain magnifies
it, so it creates one slow, *dominant* pole that sets the bandwidth
(:math:`\mathrm{GBW} \approx g_{m1}/C_c`) and pushes the others out of the
way.

The catch: the capacitor also carries signal *forward*, around the transistor.
At high frequency that path fights the transistor's own output, which adds
delay where it hurts most, near crossover.  This is a **right-half-plane
(RHP) zero** at about :math:`g_m/C_c`.

The standard cure is a **nulling resistor** of about :math:`1/g_m` in series
with :math:`C_c`: it blocks the forward path and cancels the zero.  In the
module catalog ``miller_cap`` is a bare cap; ``miller_cap_with_nulling_resistor``
and ``indirect_compensation`` are a cap plus a series resistor — electrically
the same thing.

5. Three stages: reversed nested Miller
---------------------------------------

Three stages need two compensation caps.  **Reversed nested Miller** hangs both
off the first-stage output :math:`V_1`: :math:`C_{c1}` to the third-stage
output (the outer loop) and :math:`C_{c2}` to the second-stage output (the
inner loop).

.. code-block:: text

                 ┌──────────── Cc1 ────────────┐
                 ├──── Cc2 ────┐               │
    in ─[gm1]─── V1 ──[gm2]─── V2 ──[gm3]───── V3 ── load

Its two fast poles are a coupled pair, and that pair is stable only while

.. math::

   g_{m3} < g_{m2}\left(1 + \frac{C_{V3}}{C_{c1}}\right)

where :math:`C_{V3}` is the capacitance on the third stage's output.  In
words: the third stage must not be much stronger than the second, unless its
output carries enough capacitance to calm it.  :ref:`rnmc-inner-pair` derives
the condition.

6. Why the output buffer makes it worse
---------------------------------------

Without a buffer :math:`V_3` carries the whole 5 pF load; with
:math:`C_{c1} = 1.25` pF the condition is :math:`g_{m3} < 5\,g_{m2}`, easy to
meet.

With a follower in between, the load hangs on the follower's output instead.
The follower has its own pole at :math:`g_{m,f}/C_L`, about 10 MHz on GF180,
and above that frequency it no longer passes the load capacitance through.  At
the inner loop's ~120 MHz, :math:`V_3` sees only the drains of its own
transistors — about 35 fF — and the condition collapses to

.. math::

   g_{m3} < g_{m2}\left(1 + \frac{35\ \mathrm{fF}}{1.25\ \mathrm{pF}}\right)
   \approx 1.03\, g_{m2}.

The third stage must now be *weaker* than the second.  The failing designs
had :math:`g_{m3} \approx 1.04\, g_{m2}`, on the wrong side by a hair — and
every one had a bare :math:`C_{c2}` on at least one half, whose RHP zero
(about 73 MHz) added the delay that tipped the pair over.

7. Fully differential: two halves
---------------------------------

A fully-differential amplifier is two mirror-image halves (``outp`` and
``outn``) plus a **common-mode feedback** (CMFB) loop that holds their average
at mid-supply.  The synthesizer can give the two halves *different*
compensation variants — a resistor on :math:`C_{c2}` in one, a bare cap in the
other.  One unstable half is enough for the whole amplifier to ring, so the
worse half is the one that counts.

8. Sizing a transistor: gm/Id, width and fingers
------------------------------------------------

The gm/Id sizer picks each transistor's width :math:`W`, length :math:`L` and
current :math:`I_D` (:doc:`gmid_sizing_flow`).  The key number is
:math:`g_m/I_D`, the gm bought per ampere:

- **strong inversion** (≈ 6 /V): small, fast devices, little gm per µA;
- **weak inversion** (≈ 20+ /V): large devices, the most gm per µA.

So :math:`g_m = (g_m/I_D)\cdot I_D`.  At a fixed current, the only way to more
gm is a *wider* device pushed toward weak inversion.  Two limits met here:

- **Width cap.**  GF180's widest single device is 100 µm; a 40 µA PMOS
  second stage there gives only about 0.58 mS.  Wider means several devices
  side by side — **fingers** — which #259 already used for current mirrors.
- **gm floor.**  The third stage runs 100 µA, and the lookup table's strongest
  inversion is :math:`g_m/I_D = 6`, so :math:`g_{m3}` cannot go below 0.60 mS.

:math:`0.60 / 0.58 = 1.04`: just past the 1.03 limit of Section 6.  The RNMC
search (:func:`~circuitgenome.sizer.physics.rnmc.design_rnmc`) had asked for a
:math:`g_{m2}` of 0.96 mS, but the capped device delivered 0.58 mS, and nothing
noticed.

Each transistor also adds **parasitic capacitance** to the nodes it touches:
its *gate* (:math:`C_{gs} \propto W L`), which the model counted, and its
*drain junction* (:math:`C_{db} \propto W`, about 0.4 fF per µm on GF180),
which it did not.  Behind a buffer, the drains are nearly all :math:`V_3`
carries.

9. How SPICE checks stability, and the time-step trap
-----------------------------------------------------

- **The DC operating point** (``.op``) is a balance point where every current
  adds up.  A pencil balanced on its tip is one too, so ``.op`` says nothing
  about stability.
- **The settling check** (:func:`~circuitgenome.sizer.verify.op.check_bias_soundness`)
  kicks one output with a current pulse and asks whether both outputs stop
  moving: less than 1 % of Vdd (33 mV) peak-to-peak late in the window.
- **A 1000× smaller kick rang exactly as hard.**  That proved the balance
  point itself is unstable — a small-signal right-half-plane pair, not a
  large-signal effect.  Inside the amplifier it starts as a 120 MHz wiggle
  and saturates into a nearly rail-to-rail 32 MHz cycle; the output shows only
  50–180 mV of it, because the follower cannot drive 5 pF that fast.
- **A simulator steps through time.**  At the default 1.3 ns step a 120 MHz
  oscillation gets about six points per cycle, and the integrator's rounding
  bleeds a little energy out every cycle.  That was enough to hide a *weakly*
  unstable design (#4620, :math:`\zeta \approx -0.04`): 0.2 mV at the default
  step, 150 mV at any step of 1.2 ns or less.

10. The fixes
-------------

=============================  =========  =====================================================
Fix                            Sections   What changed
=============================  =========  =====================================================
Settling time step (#267)      9          The transient's step is capped at 0.5 ns (16 points
                                          per cycle at 125 MHz), so a weak oscillation can no
                                          longer be rounded away.  The true failure count is
                                          11, not 10.
Model (#268)                   4, 6–8     Behind a buffer, the RNMC model now includes the
                                          drains on :math:`V_3` (a per-technology ``cdb``) and
                                          each compensation branch's series resistor, and it
                                          judges the worse half.  Its verdict follows SPICE.
Sizing (#268)                  6, 8       :math:`g_{m3}` cannot go down, so :math:`g_{m2}`
                                          goes up: a second stage the RNMC search strengthens
                                          is built from parallel fingers past 100 µm (up to
                                          923 µm), so it delivers the :math:`g_{m2}` the search
                                          planned with.
=============================  =========  =====================================================

On the sample, all 100 buffered fully-differential designs now settle (89
before); the buffered designs' measured phase margins move by −0.7° to +3.3°,
and the model no longer predicts 0° for designs that settle.

**Why the wider second stage is only behind a buffer.**  Without one the
third stage drives :math:`C_L` itself, which leaves the inner pair about
:math:`5\times` of room in :math:`g_{m3}/g_{m2}`, so the second stage never
needs widening.  Unbuffered RNMC had a problem of its own, though (issue
#269): its compensation resistors were sized against the 5 pF load, several
times :math:`1/g_{m3}`, which held the loop gain near 0 dB past its first
crossing until a second crossing with almost no phase left.  The SPICE bench
read only the first crossing and reported ~104°; in closed loop those designs
rang.  They are now sized at :math:`1/g_{m3}` as well, and the bench reports
the worst crossing.

**Knobs that were tried and rejected**, applied to the 11 failing designs in
SPICE:

========================================  ===========================  =====================
Knob                                      Failing designs that settle  Lowest measured PM
========================================  ===========================  =====================
:math:`C_{c1}` × 1.5                      0 of 11                      68°
third-stage current × 0.8                 11 of 11                     53.8° (below spec)
**second-stage width × 2**                **11 of 11**                 **55.0°**
========================================  ===========================  =====================

A larger :math:`C_{c1}` cannot help behind a buffer: it shrinks the
:math:`C_{V3}/C_{c1}` term of Section 6.
