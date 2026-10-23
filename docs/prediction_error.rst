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

From the run recorded below (commit ``f0e9a76``, 1,254 circuits, 1,249
simulated).  These notes are hand-written; re-check them when the snapshot is
regenerated.

* **Gain is over-predicted on one- and two-stage designs**: 7–14 dB
  (median) below the prediction, with a p10 near −20 dB — the cascode bias
  already noted in :doc:`modules/sizer`.  Three-stage designs land within
  ~3 dB, but only the half below the open-loop bench ceiling is counted, so
  their highest-gain designs are not represented.
* **GBW is mildly over-predicted** (median −12 %).  Buffered templates miss by
  15–21 %; unbuffered three-stage ones are within ~5 %.
* **Phase margin is close on RNMC, conservative elsewhere** (median +5.5°).
  Every RNMC template is within ±4° (median) — the model now sees the
  follower, compensation resistors and drain capacitance (#264, #268, #270)
  — and so are the buffered NMC and one-stage templates.  Two-stage designs
  measure 10–23° more margin than predicted and unbuffered NMC 15–23° more.
  The bench reads the **worst** of every 0 dB crossing (#270), so a gain
  that dips under 0 dB and comes back no longer reads as a large margin.
* **Slew rate is now predicted within ±5 % (median) on 9 of 13 templates**
  (was −65 % overall): the model takes the slowest current-limited node,
  including a follower driving the load (#258).  The misses — two-stage and
  unbuffered NMC single-ended at +11–12 %, unbuffered RNMC single-ended at
  −7 % and two-stage fully-differential at −6.5 % — come from multi-phase
  large-signal edges rather than a missing current limit.
* **Power is over-predicted** by 6–50 % on most templates.  Fully-differential
  RNMC ones are now *under*-predicted by 7–11 %: their mirror outputs carry
  the planned current since #259.  The single-ended three-stage RNMC
  templates still split in two: about half land with the other templates,
  and the other half (52 of 100 on each) draw 2–40× the predicted power
  (worst +4,260 %).  Which half a design falls in follows how the polarity of
  its non-inverting third stage pairs with the first-stage load — a model or
  sizing gap worth its own issue.
* **Output swing** is within ~0.25 V on single-ended templates.
  Fully-differential ones still measure 0.4–1.2 V less swing than predicted
  on each side (worst on two-stage FD); whether the model or the
  fully-differential swing bench is at fault is not settled by this data.
* **CMRR and PSRR are under-predicted** by 13–50 dB (median) on every
  template but the one-stage PSRR (13 dB over), and spread widely: the
  closed-form estimates are first-order, as the sizer documents.
  Fully-differential templates are now included — measured from PDK device
  mismatch (#261) — and land like the single-ended ones (CMRR +27–34 dB,
  PSRR +13–22 dB median).

.. include:: prediction_error/tables.inc
