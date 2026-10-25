Predicted vs. measured metrics
==============================

How far the gm/Id sizer's **predicted metrics** land from the ngspice
**measured metrics** of the same sized circuit, across every topology template
on GF180MCU.  Every number on this page is a **prediction error**, signed
*measured − predicted*: a positive error means the model under-predicted.
Gain, CMRR and PSRR errors are in dB, phase margin in degrees, output swing in
volts, and GBW, slew rate and power as a percentage of the predicted value —
those span decades across templates, so an absolute error would mean little.

Each table gives the **median** (the model's systematic bias), the **p10–p90**
spread, and the **worst** single error.  The `interactive explorer
<prediction_error_explorer/index.html>`__ plots every circuit's predicted
against measured value per metric; hover a point to see which circuit — and
which slot variants — it is.

The tables and figures are a checked-in snapshot.  Re-render them with
``python tools/gen_prediction_error.py render`` (needs ``uv sync --group
docs-data``) and re-sample with its ``run`` command; see the script's
docstring.

Key observations
----------------

From the run recorded below (commit ``7def7c4``, 1,254 circuits, 942
simulated).  These notes are hand-written; re-check them when the snapshot is
regenerated.

* **Fully-differential results cover only resistive-sense CMFB designs.**
  The sizer now knows a difference-difference (DDA) CMFB holds the output
  common mode only within ±0.24 V of mid-supply (#276), so it rejects all 307
  sampled DDA designs against this page's 1.1 V / 2.0 V swing spec — they
  are the *Bias-infeasible (sizer)* column below.  Each fully-differential
  template keeps the 45–53 resistive-sense designs of its 100.
* **Gain lands within ~3 dB on every template** (median −0.8 dB, p10
  −5.2 dB; was 7–14 dB over-predicted on one- and two-stage designs).  The
  first stage is now read at each device's own Vds and Vsb, with the
  cascode's body transconductance (#277).  One-stage and buffered two-stage
  single-ended still read ~3 dB low, and the single-ended three-stage RNMC
  templates keep a tail to −8…−12 dB.  Three-stage designs above the
  open-loop bench ceiling are still not counted.
* **GBW is within 3–7 % (median) on every template** (pooled −5 %; buffered
  templates were 15–21 % over-predicted).  Two-stage and NMC are now read
  off a full small-signal model, with the follower's body-effect gain
  (#279).  One-stage designs keep a long tail (p10 −65 %, worst −92 %) that
  this model change did not touch.
* **Phase margin is within ±1.5° (median) on every multi-stage template**
  (pooled −0.1°, p10–p90 −2.5…+1.5°; two-stage and unbuffered NMC were
  10–23° conservative).  The model reads the worst of every 0 dB crossing,
  as the bench does (#270, #279).  One-stage designs read 2.7° low.
* **Slew rate is unchanged** (pooled +2 %, 9 of 13 templates within ±5 %
  median, #258).  The misses — two-stage and unbuffered NMC single-ended at
  +12 %, unbuffered RNMC single-ended at −7 % — come from multi-phase
  large-signal edges rather than a missing current limit.
* **Power is over-predicted** by 8–49 % (median) on every template but the
  fully-differential RNMC ones, which are 6–8 % under.  The single-ended
  three-stage RNMC split is gone: the bench now measures them in unity
  feedback instead of with the output railed (#275), and they land at
  −13 % (were +241 % and +329 % median, worst +4,260 %).
* **Output swing is within 0.25 V (median) on every template**, and on the
  safe side: the model predicts slightly less swing than SPICE measures
  (pooled +0.15 V at the top, −0.16 V at the bottom).  Buffered
  fully-differential designs used to miss by 0.6–0.8 V on one side; their
  two outputs swing as mirror images around mid-supply, so the follower's
  tighter side bounds both, and the model now says so (#283).  The largest
  remaining misses, up to ~0.5 V, are single-ended NMC designs.
* **CMRR and PSRR are under-predicted** by 16–48 dB (CMRR) and 16–27 dB
  (PSRR) median on every template but the one-stage PSRR (11 dB over), and
  spread widely: the closed-form estimates are first-order, as the sizer
  documents.  Fully-differential values come from PDK device mismatch
  (#261).

.. include:: prediction_error/tables.inc
