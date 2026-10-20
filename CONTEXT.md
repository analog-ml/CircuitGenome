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

## Sizing metrics

**Predicted metrics**:
Performance figures (gain, GBW, phase margin, slew rate, …) computed by the
sizer's device model — the Level-1 square-law or the gm/Id lookup table — with no
circuit simulation. Always what a sizing result carries, for every technology.
_Avoid_: Analytical metrics (collides with the Level-1 *analytical* sizer, yet gm/Id
results are predicted too), estimated metrics.

**Measured metrics**:
The same performance figures read back from a SPICE (ngspice) simulation of the
sized circuit, at one process corner. The authority whenever it disagrees with the
predicted metrics.
_Avoid_: Simulated metrics, SPICE metrics.

## Frequency compensation

**Pole pair**:
Two non-dominant poles that arise together as the roots of one quadratic and
can therefore turn complex (`−σ ± jω`) and ring. Three-stage amplifiers (NMC,
RNMC) have one; single- and two-stage amplifiers have only a single real
non-dominant pole and cannot ring.
_Avoid_: Inner poles, inner loop (when the pole pair itself is meant), complex poles
(the pair may also be two real roots).

**Damping (ζ)**:
The damping ratio of a pole pair — `ζ = −Re(p)/|p|`, the cosine of the poles'
angle from the negative real axis. ζ ≥ 0.707 means no resonance peak; ζ = 0 rings
forever; ζ < 0 is unstable. A sizing target alongside phase margin, because
phase margin is read at the 0 dB crossing and cannot see a pair resonating
above it.
_Avoid_: Damping factor (in prose — same quantity), stability margin.

**Full model**:
The RNMC open loop solved from its nodal equations — parasitics, stage output
conductances, the third-stage mirror and an output buffer included — as opposed
to the **closed forms** derived from the simplified quadratic. The full model
decides; the closed forms explain and seed the search.
_Avoid_: Exact model, numerical model, SPICE model (SPICE gives *measured metrics*).
