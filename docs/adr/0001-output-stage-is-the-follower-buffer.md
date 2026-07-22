# "Output stage" is the source-follower buffer, not the last load-driving stage

The gm/Id sizer's design-intent registry (`sizer/gmid/intent.py`) treats the
`output_stage` functional block as the **dedicated unity-gain source-follower
buffer**, matching the meaning the synthesizer (`output_stage` category =
`common_drain_*` followers) and recognizer (`category: output_stage`) already use.
"The stage that drives the load" — which lands on `second_stage`, `third_stage`, or
the follower depending on topology — is a separate, *positional* notion and is
**not** the output stage.

## Considered Options

- **Chain-relative** — "output stage" = whichever stage drives the external load.
  Rejected: it would make the sizer the only module where "output stage" means the
  opposite of what the synthesizer and recognizer mean, deepening a term overload.
- **Structural (chosen)** — "output stage" = the follower buffer, full stop.

## Consequences

- `_SIGNAL_BLOCK` maps `third_stage` to `gain_stage` (it is an `amplification_stage`);
  only the follower slots map to the `output_stage` block.
- The follower gets its own intent (a *fixed* gm/Id + short L), so it no longer
  free-rides the signal fallback. This makes the `output_stage` block the one
  **signal-role block with a fixed gm/Id** — it never receives a gm requirement
  (`preprocess.py`), so its gm/Id can't be "solved" and must be set by intent.
- The relabel of `third_stage` is geometry-neutral: a third-stage device's role,
  gm/Id, L and gm target are unchanged by the block name.
