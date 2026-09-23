# "Output stage" is the dedicated output buffer, not the last gain stage

The gm/Id sizer's design-intent registry (`sizer/gmid/intent.py`) treats the
`output_stage` functional block as the **dedicated unity-gain output buffer**.
The buffered templates use `common_drain_*` followers. The static CMOS
Class-AB variant retains the recognizer category but is wired through the
dedicated `class_ab_stage` slot, which the sizer treats as a gain stage.
"The stage that drives the load" — which lands on `second_stage`, `third_stage`, or
the follower depending on topology — is a separate, *positional* notion and is
**not** the output stage.

## Considered Options

- **Chain-relative** — "output stage" = whichever stage drives the external load.
  Rejected: it would make the sizer the only module where "output stage" means the
  opposite of what the synthesizer and recognizer mean, deepening a term overload.
- **Structural (chosen)** — "output stage" = the dedicated output buffer.

## Consequences

- `_SIGNAL_BLOCK` maps `third_stage` to `gain_stage` (it is an `amplification_stage`);
  only the follower slots map to the `output_stage` block.
- An active single-follower variant gets its own intent (a *fixed* gm/Id + short L), so it no longer
  free-rides the signal fallback. This makes the `output_stage` block the one
  **signal-role block with a fixed gm/Id** — it never receives a gm requirement
  (`preprocess.py`), so its gm/Id can't be "solved" and must be set by intent.
- The relabel of `third_stage` is geometry-neutral: a third-stage device's role,
  gm/Id, L and gm target are unchanged by the block name.
- The parked Class-AB variant requires crossover-distortion and PVT validation
  before it can join default enumeration.
