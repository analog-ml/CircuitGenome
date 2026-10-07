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

From the run recorded below (commit ``5ee0e4f``, 1,254 circuits, 1,210
simulated).  These notes are hand-written; re-check them when the snapshot is
regenerated.

* **Gain is over-predicted.**  One- and two-stage designs measure 7–14 dB
  (median) below the prediction, with a p10 near −20 dB — the cascode bias
  already noted in :doc:`modules/sizer`.  Three-stage designs land within
  ~2 dB, but only the half below the open-loop bench ceiling is counted, so
  their highest-gain designs are not represented.
* **GBW is mildly over-predicted** (median −11 %).  Buffered templates miss by
  15–20 %; unbuffered three-stage ones are centred on zero.
* **Phase margin is under-predicted** (median +11°): the model is
  conservative, most of all on two-stage and buffered RNMC designs.
* **Slew rate is the largest systematic gap** (median −65 %).  Every buffered
  template measures ~4.1 V/µs — 20 µA into the 5 pF load, i.e. the follower's
  bias current — while the model predicts the internal ``ibias / Cc`` limit.
  Unbuffered designs measure 40–60 % low for the same reason at their own
  output.
* **Power is over-predicted** by 13–50 % on every template *except* the
  single-ended three-stage RNMC ones, which split in two: about half match
  the other templates, and the other half (53 and 52 of 100) draw 4–40× the
  predicted power (worst +4,238 %).  Which half a design falls in follows how
  the polarity of its non-inverting third stage pairs with the first-stage
  load — a model or sizing gap worth its own issue.
* **Output swing** is within ~0.25 V on single-ended templates; fully
  differential ones measure ~1.1 V less swing than predicted on each side.
  Whether the model or the fully-differential swing bench is at fault is not
  settled by this data.
* **CMRR and PSRR** (single-ended only) are under-predicted by 20–50 dB
  (median), and spread widely: the closed-form estimates are first-order, as
  the sizer documents.

.. include:: prediction_error/tables.inc
