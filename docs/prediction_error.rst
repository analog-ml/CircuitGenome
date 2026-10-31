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

From the run recorded below (commit ``604f1a6``, 1,254 circuits, 942
simulated).  These notes are hand-written; re-check them when the snapshot is
regenerated.

* **Fully-differential results cover only resistive-sense CMFB designs.**
  The sizer knows a difference-difference (DDA) CMFB holds the output
  common mode only within ±0.24 V of mid-supply (#276), so it rejects all 307
  sampled DDA designs against this page's 1.1 V / 2.0 V swing spec — they
  are the *Bias-infeasible (sizer)* column below.  Each fully-differential
  template keeps the 45–53 resistive-sense designs of its 100.
* **Every simulated circuit now counts for gain, GBW and phase margin**
  (942, was 617).  47 single-ended designs with a resistor first-stage load
  had their gain withheld by a railing check that misfired in weak inversion
  (#289), and 278 three-stage designs sat above a stale 150 dB open-loop
  bench ceiling that now follows each design's GBW (#292).
* **Gain lands within ~3 dB (median) on every template** (pooled −0.7 dB,
  p10 −5.2 dB).  One-stage designs moved from −3.1 to −1.4 dB once their
  resistor loads were biased at the input common mode (#288).  Buffered
  single-ended templates still read 2–3 dB low, and the buffered RNMC
  templates keep a tail to −11…−12 dB.
* **GBW is within 3.5–7 % (median) on every template** (pooled −5 %).  The
  one-stage tail is gone (p10 −8 %, worst −15 %; was −65 % and −92 %): it was
  the resistor-loaded designs' broken bias (#288).  42 single-ended designs
  with a resistor first-stage load now read the other way, measuring 10–25 %
  above the prediction; half of them are newly counted (#289).
* **Phase margin is within ±1.5° (median) on every multi-stage template**
  (pooled 0.0°, p10–p90 −2.3…+1.8°).  One-stage designs read 2.7° low.
  Three newly counted unbuffered NMC designs (#292) are predicted at 43–49°
  but measure 95–96° (worst +52°).
* **Slew rate is within ±5 % (median) on 10 of 13 templates** (pooled +2 %,
  p90 +11 %, worst +50 %; was +13 % and +70 %).  The bench now resolves fast
  edges and gives no slew rate to an output that oscillates after the step —
  5 unbuffered NMC designs (#295).  The misses — two-stage and unbuffered NMC
  single-ended at +8 % and +12 %, unbuffered RNMC single-ended at −6 % — come
  from multi-phase large-signal edges rather than a missing current limit.
  One-stage resistor-loaded designs now read up to ~30 % low (p10 −27 %),
  likely the current the load resistor itself draws during the edge.
* **Power is within 1–3 % (median) on every template** (pooled +1.7 %,
  p10–p90 +0.5…+3.7 %, worst +5.3 %; was −24 %).  The prediction now sums
  the current leaving the positive rail, as the bench measures it, instead
  of one branch per slot (#291); the off-chip reference current is left out
  of both.
* **Output swing is within 0.25 V (median) on every template**, and on the
  safe side: the model predicts slightly less swing than SPICE measures
  (pooled +0.15 V at the top, −0.16 V at the bottom).  Buffered
  fully-differential designs used to miss by 0.6–0.8 V on one side; their
  two outputs swing as mirror images around mid-supply, so the follower's
  tighter side bounds both, and the model now says so (#283).  The largest
  remaining misses, up to ~0.5 V, are single-ended NMC designs.
* **Single-ended CMRR and PSRR+ are within ~2 dB (median) on every
  single-ended template**, one-stage PSRR+ aside at −3.4 dB (were 16–48 dB
  and 17–27 dB under-predicted).  They now come
  from a DC solve of the whole sized circuit (#294): on the 430 single-ended
  designs without a telescopic cascode load, CMRR lands at −1.1 dB median
  (p10–p90 −18…+6 dB) and PSRR+ at −0.8 dB (−5…+4 dB).  The 219 telescopic
  designs are deliberately kept on the old closed form, which under-predicts
  them (+39 dB CMRR, +41 dB PSRR+ median) — the solve cannot see the ~1 mV
  drain imbalance that sets their floor and would over-predict them instead.
* **Fully-differential CMRR and PSRR stay under-predicted** by 27–34 dB and
  12–22 dB (median).  The bench reports the worst of eight PDK mismatch
  draws (#261); the model has no mismatch term, and the technology files
  carry no mismatch coefficients to build one from.

.. include:: prediction_error/tables.inc
