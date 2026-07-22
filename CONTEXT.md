# CircuitGenome — Ubiquitous Language

Shared vocabulary across synthesis, recognition, and gm/Id sizing of
operational-amplifier topologies. Terms are grouped by area; the amplifier
**stage** roles especially pin down where positional and structural notions are
easy to conflate.

## Documentation

**Walkthrough** — A self-contained, hand-authored HTML deep-dive page (with
inline SVG figures) explaining how a module's code works. Walkthroughs live in
`docs/walkthrough/` and are **living documents**: edited in place when the
described module's behavior materially changes, with stable, undated filenames
(history and dating come from git). Not to be confused with a *tutorial*.

**Tutorial** — A follow-along, task-oriented guide kept current with the
shipped release. CircuitGenome's walkthroughs are *not* tutorials and must not
be labeled as such in navigation.

## Amplifier stages

**Input stage**:
The differential input pair that converts the differential input voltage to a
current. Always the first stage; the `input_pair` slot.
_Avoid_: First stage, diff pair (as a role name).

**Gain stage**:
A voltage-gain stage (A > 1) in the signal chain. The numbered signal slots
(`second_stage`, `third_stage`) are gain stages; the recognizer names them
positionally as `gain_stage_N`. Common-source by structure.
_Avoid_: Amplification stage (the code's category name — use "gain stage" in prose),
second/third stage (those are *positions*, not the role).

**Output stage**:
A dedicated unity-gain (A ≈ 1) source-follower *buffer* placed after the last gain
stage to drive the load without adding gain — the `output_stage` slot, appearing only
in `*_buffered_*` topologies. This is a **structural** role (common-drain follower),
**not** "whichever stage drives the load."
_Avoid_: Output buffer, final stage, last stage — and never use "output stage" to mean
the last load-driving gain stage.

**Load-driving stage**:
Whichever stage's output is wired to the external output net — `second_stage` in a
2-stage amp, `third_stage` in a 3-stage, or the output-stage follower in a buffered
topology. A **positional** property, deliberately kept distinct from "output stage":
in an unbuffered amp the load-driving stage is a *gain stage*, not an output stage.
_Avoid_: Output stage (see above), final gain stage.
