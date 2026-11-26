# Stop 00 — Analog background for reading the sizer

**For**: a software engineer with little analog experience.
**Goal**: give you just enough circuit intuition that every function in
`circuitgenome/sizer` reads as "oh, that's the code for *that* idea".
**How to use**: read this once top to bottom (≈ 1 hour). Later, every stop file
links back here with links like [gm](#gm), so treat this as the glossary.

Each concept has the same four parts:

- **In plain words**: what it is
- **Analogy**: a picture to hang it on (analogies are approximate; where they
  break down, the text says so)
- **Rule of thumb / formula**: the one or two equations the code actually uses
- **Where it shows up**: the sizer files that implement or depend on it

Formulas use these symbols: `I` current (A), `V` voltage (V), `R` resistance
(Ω), `C` capacitance (F), `f` frequency (Hz), `W`/`L` transistor width/length (µm).

---

## Contents

1. [Electricity in five minutes](#part-1)
   [voltage and current](#voltage-current) ·
   [KCL](#kcl) · [rails](#rails) · [capacitor](#capacitor) · [decibels](#db)
2. [The transistor](#part-2)
   [MOSFET](#mosfet) · [NMOS / PMOS](#nmos-pmos) · [operating regions](#regions) ·
   [Vdsat and headroom](#vdsat) · [threshold and body effect](#vth-body) ·
   [bias point vs small signal](#small-signal) · [gm](#gm) ·
   [output resistance](#rout) · [square law (Level-1)](#square-law) ·
   [inversion levels](#inversion) · [gm/Id method and the LUT](#gmid) ·
   [ft](#ft) · [parasitic capacitances](#parasitic-caps) ·
   [technology, PDK, corners](#technology)
3. [Building blocks](#part-3)
   [common-source stage](#common-source) · [current mirror](#current-mirror) ·
   [differential pair and tail](#diff-pair) · [loads](#loads) ·
   [cascode](#cascode) · [source degeneration](#degeneration) ·
   [source follower](#source-follower) · [bias generator](#bias-generator)
4. [The op-amp](#part-4)
   [what an op-amp is](#opamp) · [stages](#stages) ·
   [fully differential](#fully-differential) · [CMFB](#cmfb) ·
   [headroom and stacking](#headroom) · [stage interface](#stage-interface) ·
   [output swing](#swing)
5. [Speed and stability](#part-5)
   [poles](#poles) · [GBW](#gbw) · [feedback and phase margin](#phase-margin) ·
   [right-half-plane zero](#rhp-zero) · [Miller compensation](#miller) ·
   [nested Miller and RNMC](#nested-miller) · [slew rate](#slew)
6. [The eight spec metrics](#part-6)
   [the spec sheet](#spec-metrics) · [power](#power) · [CMRR](#cmrr) ·
   [PSRR](#psrr) · [nodal analysis](#nodal-analysis)
7. [The sizing problem](#part-7)
   [what "sizing" means](#sizing-problem) · [search vs forward pass](#two-strategies) ·
   [analytical estimate vs SPICE](#analytical-vs-spice) ·
   [where the sizer sits in CircuitGenome](#pipeline-layers)

---

<a id="part-1"></a>
## Part 1 — Electricity in five minutes

<a id="voltage-current"></a>
### Voltage, current, resistance

**In plain words.** *Voltage* is a pressure difference between two points.
*Current* is how much charge flows per second. A *resistor* turns a current into
a voltage drop: `V = I·R` (Ohm's law).

**Analogy.** Water in pipes: voltage = water pressure, current = litres per
second, resistor = a narrow section of pipe. Push the same flow through a
narrower pipe and you lose more pressure across it.

**Where it shows up.** Everywhere. In the sizer, load and bias resistors are sized
by "which `R` gives the drop I want at the current I have": `R = V_drop / I`
(`physics/preprocess.py::size_load_resistors`, `gmid/resistors.py`,
`gmid/bias_levels.py`).

<a id="kcl"></a>
### Kirchhoff's current law (KCL)

**In plain words.** At any junction (a *node*, or *net* in netlist language) the
current flowing in equals the current flowing out. Charge does not pile up.

**Analogy.** A pipe junction: if 10 L/s comes in, 10 L/s leaves, split however
the pipes allow.

**Why it matters for sizing.** If you know the bias current one transistor
carries, KCL often fixes the current of every transistor in series or in parallel
with it. The sizer *does not choose* these currents. They follow from the
reference current `ibias` and the circuit's wiring, and that is the first thing
it computes.

**Where it shows up.** `physics/preprocess.py::assign_ids`,
`gmid/plan.py::assign_currents` (Phase 2), the `HALF_BIAS_SLOTS` /
`FULL_BIAS_SLOTS` groups in `physics/taxonomy.py`. The two transistors of an
input pair each carry half the tail current, which is why they are a "half-bias"
slot.

<a id="rails"></a>
### Supply rails and ground

**In plain words.** A circuit is powered between two fixed voltages: the positive
supply `Vdd` (here 3.3 V for gf180mcu) and the negative supply `Vss` (usually
0 V, ground). Every voltage inside the circuit lives between them.

**Analogy.** The ceiling and the floor of a room. Everything you stack must fit
between them. Low-voltage technologies have a *low ceiling*.

**Where it shows up.** `SizingSpec.vdd` / `vss` (`models.py`), the `RAILS` and
`BIAS_NETS` sets in `physics/taxonomy.py` (net names such as `vdd!`, `gnd!`),
and the headroom checks ([headroom](#headroom)).

<a id="capacitor"></a>
### Capacitor

**In plain words.** Two plates that store charge. A capacitor's voltage cannot
jump. It changes only as current fills or drains it: `dV/dt = I / C`. At low
frequency a capacitor looks like an open circuit; at high frequency it looks
like a short. Its "AC resistance" (impedance) is `1 / (2π·f·C)`.

**Analogy.** A bucket. The water level (voltage) rises at a rate set by the hose
(current) and the bucket's width (capacitance). For quick sloshing (high
frequency) the bucket swallows the motion, so the level barely moves.

**Where it shows up.** The load capacitor `cl` in the spec (what the amplifier
must drive, 5 pF in the gf180 spec), the compensation capacitors `Cc`, `Cc1`,
`Cc2` ([Miller](#miller)), the transistors' own hidden capacitances
([parasitic caps](#parasitic-caps)), and slew rate ([slew](#slew)).
`SizingResult.capacitors` carries the sized values.

<a id="db"></a>
### Decibels (dB)

**In plain words.** A log scale for ratios: `dB = 20·log10(ratio)` for voltage
ratios. ×10 = 20 dB, ×100 = 40 dB, ×1000 = 60 dB, ×10 000 = 80 dB. Multiplying
gains means adding their dB values.

**Where it shows up.** `gain_min_db`, `cmrr_min_db`, `psrr_min_db` in the spec;
`equations.open_loop_gain_db` turns a product of stage gains into dB.

---

<a id="part-2"></a>
## Part 2 — The transistor

<a id="mosfet"></a>
### MOSFET: a voltage-controlled valve

**In plain words.** A MOSFET has four terminals: **gate (g)**, **drain (d)**,
**source (s)** and **bulk/body (b)**. The voltage between gate and source,
`Vgs`, controls how much current flows from drain to source, `Id`. The gate
itself draws (almost) no current. You just apply a voltage to it.

The designer's knobs are the device's **width `W`** and **length `L`**, plus the
**current** you run through it. "Sizing a circuit" means choosing `W` and `L`
for every transistor.

**Analogy.** A water valve. The gate is the handle; turning it (`Vgs`) changes
the flow (`Id`) between the inlet (drain) and outlet (source). A wider valve
(bigger `W`) passes more flow for the same handle position. A longer valve
(bigger `L`) passes less flow but is steadier ([output resistance](#rout)).

**Where it shows up.** `TransistorSizing` in `models.py` (`w_um`, `l_um`,
`fingers`). Netlist devices are `circuitgenome.synthesizer.models.Device`, with a
`terminals` dict keyed `"d"`, `"g"`, `"s"`, `"b"`. You will see
`device.terminals["g"]` all over `physics/`.

<a id="nmos-pmos"></a>
### NMOS and PMOS

**In plain words.** Two mirror-image flavours. An **NMOS** conducts when its gate
is *above* its source and usually sits near ground, pulling current *down*. A
**PMOS** conducts when its gate is *below* its source and usually sits near
`Vdd`, pushing current *down from the top*. Same physics with the signs flipped.
PMOS is roughly 2–4× weaker per µm, so it needs to be wider for the same job.

**Analogy.** A drain valve at the floor (NMOS) and a tap at the ceiling (PMOS).

**Where it shows up.** Almost every formula takes magnitudes (`abs(ids)`,
`|Vgs|`) so one code path handles both. The gm/Id LUT stores one table per
polarity ([LUT](#gmid)). Tech YAMLs have separate `nmos:` / `pmos:` blocks.

<a id="regions"></a>
### Operating regions: off, triode, saturation

**In plain words.** A MOSFET behaves in one of three ways:

| Region | Condition (NMOS) | Behaves like | Want it? |
|---|---|---|---|
| Cutoff (off) | `Vgs < Vth` | an open switch | no (for amplifiers) |
| Triode (linear) | on, but `Vds` small | a **resistor** controlled by `Vgs` | **no**: gain collapses |
| Saturation | on, and `Vds ≥ Vdsat` | a **current source** controlled by `Vgs` | **yes** |

In saturation the current hardly depends on `Vds`. That is what makes it an
amplifier: the gate sets the current, and whatever is connected to the drain
turns that current into a voltage.

**Analogy.** A flow regulator on a garden hose. As long as there is *enough*
pressure across it, it delivers its set flow no matter how much extra pressure
you add (saturation). Starve it of pressure and the flow drops with the pressure,
so it is no longer a regulator, just a narrow pipe (triode).

**Where it shows up.** This is *the* thing the DC checks protect.
`gmid/bias.py` ("the tail drops into triode and sources far less than the
assumed current"), `gmid/stage_interface.py` ("the mirror's output cascode in
triode"), and the SPICE bias-soundness check in `verify/op.py`, which reads each
device's `Id`, `Vds` and `Vdsat` from an ngspice `.op` and infers the region itself
(triode: `|Vds| < |Vdsat|`; starved: `|Id|` below 0.1 µA).

<a id="vdsat"></a>
### Vdsat, overdrive, and "headroom"

**In plain words.** `Vdsat` is the minimum drain–source voltage a device needs
to stay in saturation. In strong inversion `Vdsat ≈ Vov = Vgs − Vth` (the
*overdrive*, how far past threshold the gate is pushed). It is typically
0.1–0.3 V. *Headroom* is the voltage left over for a device after everything
else in its path has taken its share.

**Analogy.** The minimum pressure drop the flow regulator needs to work. If the
pipes before and after it already use up most of the pressure, it has no
headroom left.

**Rule of thumb.** A device with a smaller `Vdsat` needs less headroom.
Lower `Vdsat` comes from running it in weaker inversion (higher gm/Id), which
costs width ([inversion](#inversion)).

**Where it shows up.** `equations.vds_sat` (square-law version); the LUT's
`vdsat` field (`physics/gmid_lut.py`); `vod_max_map` from `compute_requirements` (both paths; on the Level-1 path it
becomes CP-SAT constraints);
`gmid/bias.py` and `gmid/stage_interface.py` both repair headroom problems by
moving devices toward weak inversion to shrink their `Vdsat`.

<a id="vth-body"></a>
### Threshold voltage and body effect

**In plain words.** `Vth` is the gate voltage at which the device starts to
conduct, roughly 0.4–0.7 V depending on technology. When a device's source sits
*above* its bulk (common for stacked NMOS devices whose bulk is tied to ground),
its `Vth` rises. That is the **body effect**. It also gives the device a second,
weaker control input, the body transconductance `gmb`.

**Analogy.** A valve whose handle gets stiffer the higher it is mounted above
the floor.

**Rule of thumb.** `ΔVth = γ·(√(2φF + Vsb) − √(2φF))`, using the `gamma` /
`phi` values in the tech YAML.

**Where it shows up.** `equations.body_vth_shift`, the `gamma`/`phi` fields of
`MosfetParams`, the LUT's optional `gmb_gm` table, `physics/node_levels.py`
(estimates each net's DC level so `Vsb` is known), and `stage_chain.node_rout`
(reads `model.gmb`).

<a id="small-signal"></a>
### Bias point vs small signal: the two views of every circuit

**In plain words.** Analog designers look at a circuit twice:

1. **DC / bias / operating point.** With no input signal, what steady voltage
   sits on every node and what current flows through every device? This must put
   every device in [saturation](#regions).
2. **Small signal / AC.** Now wiggle the input by a tiny amount. How much does
   the output wiggle, and how does that change with frequency? Around the bias
   point each transistor is replaced by a simple linear model: a controlled
   current source `gm·vgs` in parallel with a resistor `rout`, plus a few
   capacitors.

**Analogy.** A car's ride height on its springs (DC: does it clear the ground?)
vs how it bounces over a bump (small signal: how big, how quickly does it
settle?). You must get the ride height right *before* bounce analysis means
anything.

**Why it matters for the code.** The sizer's pipeline follows exactly this order.
It fixes currents (DC), sizes devices, checks/repairs the DC point (`bias.py`,
`stage_interface.py`), and **only then** computes small-signal metrics
(`evaluate.py`). If the DC point is unsound it *withholds* gain metrics rather
than reporting meaningless numbers. `SizingResult.bias_feasible` is the DC
verdict.

<a id="gm"></a>
### gm: transconductance

**In plain words.** `gm = ΔId / ΔVgs`: how many amps of extra current you get
per volt of gate wiggle. It is the transistor's "amplifying strength" and is
measured in siemens (A/V). More current or a wider device gives more `gm`.

**Analogy.** How sensitive the valve handle is. Move it a millimetre: how much
more water flows?

**Rule of thumb.** Square law: `gm = √(2·µCox·(W/L)·Id) = 2·Id / Vov`. There is
a hard ceiling: even in [weak inversion](#inversion), `gm ≤ ~25·Id` (per volt).
You cannot get more gm out of a given current.

**Where it shows up.** `equations.gm`, `equations.gm_ceiling` (`_GM_OVER_ID_MAX
= 25`). `compute_requirements` in `physics/preprocess.py` turns the spec into
*gm requirements* (e.g. "the input pair needs gm1 ≥ 2π·GBW·Cc"). The whole
gm/Id path is about delivering those gm values.

<a id="rout"></a>
### Output resistance (rout, gds, λ) and intrinsic gain

**In plain words.** A real transistor in saturation is not a perfect current
source. Its current rises slightly as `Vds` rises. The *output conductance*
`gds = ΔId/ΔVds` measures that leak. Its inverse is the *output resistance*
`rout = 1/gds`. A higher `rout` means a better current source. In the Level-1
model `gds = λ·Id`, where `λ` (channel-length modulation) shrinks as `L` grows.
**Longer L gives a better current source.**

The product `gm·rout = gm/gds` is the *intrinsic gain*: the most voltage gain a
single transistor can give. Typically 20–200 (26–46 dB).

**Analogy.** The flow regulator leaks a little more when you raise the pressure
across it. `rout` measures how stubborn it is. A longer regulator body is more
stubborn.

**Rule of thumb.** Resistances that hang off the same node from different
devices combine in parallel: `R = 1 / (gds_top + gds_bottom)`
(`equations.rout`).

**Where it shows up.** `equations.gd` / `equations.rout`, the LUT's `gm_gds`
field (intrinsic gain), `DeviceModel.gds`, and the main event,
`physics/stage_chain.py::node_rout`, which walks the circuit to find the
resistance seen at each output node, including [cascode](#cascode) boosting.
The "current-source" role in `device_model.py` uses a longer `L` for exactly
this reason.

<a id="square-law"></a>
### The square law (Level-1 / Shichman–Hodges model)

**In plain words.** The textbook model of a saturated MOSFET:

```
Id   = ½ · µCox · (W/L) · Vov²      (Vov = Vgs − Vth)
gm   = √(2 · µCox · (W/L) · Id)
Vdsat = Vov = √(2 · Id · L / (µCox · W))
gds  = λ · Id
```

`µCox` is a process constant (how conductive the channel is per square). It is
simple, closed-form, and taught in every course. It is **wrong** for modern
short-channel devices (by 2× or worse), especially in moderate and weak
inversion.

**Analogy.** Physics-class projectile motion with no air resistance: a great
first guess, but not how a real ball flies.

**Where it shows up.** `physics/equations.py` (`gm`, `gd`, `vds_sat`,
`vgs_from_ids`), `Level1Model` in `physics/device_model.py`, and the whole
`analytical/` path (Stop 5), used only for the made-up `generic` tech whose YAML
carries `mu_cox`, `vth`, `lam`. A SPICE-model tech (ptm45-style) without a LUT
is refused by the dispatcher (`UnsupportedTechError`), precisely because these
numbers would be fiction. A foundry PDK (`spice_lib`) without a LUT normally
fails earlier, in the loader, because its YAML has no square-law numbers.

<a id="inversion"></a>
### Inversion levels: weak, moderate, strong

**In plain words.** How hard the gate is pushed past threshold:

| Level | gm/Id (1/V) | Vov | Character |
|---|---|---|---|
| Weak (sub-threshold) | ~18–25 | below 0 | most gm per µA (efficient), but needs a wide device and is slow |
| Moderate | ~10–16 | ~0–0.15 V | the usual sweet spot |
| Strong | ~5–8 | ~0.2–0.4 V | fast and compact, but power-hungry; needs more headroom |

**Analogy.** Gears on a bike. Low gear (weak inversion) gets you the most
"push" per unit of effort but you go slowly. High gear (strong inversion) is
fast but costs effort.

**Where it shows up.** `gmid/intent.py`'s design-intent table: signal devices get
their gm/Id *solved* from their gm requirement, while current sources sit at
~10 /V and cascodes at ~8 /V. Repairs in `bias.py` / `stage_interface.py` "move
toward weak inversion" to shrink `Vgs` and `Vdsat`.

<a id="gmid"></a>
### The gm/Id method and the lookup table (LUT)

**In plain words.** Instead of trusting the square law, characterise the real
transistor once with the foundry's accurate simulator model, and store the
results in a table. The key insight is that **gm/Id** (the "efficiency" ratio)
and **L** together pin down nearly everything else about a device,
*independent of its width*:

- `Id/W` (current density, A/µm): how much current per µm of width
- `gm/gds` (intrinsic gain)
- `ft` (speed, [ft](#ft))
- `Vdsat`, `Vgs`

So the design recipe becomes a **forward calculation**, not a search:

```
1. Id is known (KCL)                      → e.g. 10 µA
2. gm is required (spec)                  → e.g. 150 µS
3. so gm/Id = 15 /V  (pick L by policy)   → moderate inversion
4. LUT(gm/Id=15, L) gives Id/W            → e.g. 1 µA/µm
5. W = Id / (Id/W)                        → 10 µm
```

**Analogy.** Fuel efficiency. gm/Id is "km per litre" for transistors. Choose
how efficient you need the device to be, look up the car's spec sheet (the LUT),
and the size of the tank (W) follows.

**Caveat the code cares about.** The table is measured at one bias point,
`Vds = Vdd/2` and `Vsb = 0`. Devices far from that (e.g. under a cascode, at a
small `Vds`) behave differently. That is why there are correction tables
(`gds_scale`, `gmb_gm`) and `physics/node_levels.py`.

**Where it shows up.** `physics/gmid_lut.py::GmIdLut` (bilinear interpolation
over the `(gm/Id, L)` grid, loaded from `config/models/*_gmid.npz`),
`GmIdModel.geometry_for` in `device_model.py` (steps 3–5 above),
`gmid/geometry.py` (does it for every device), and the tech YAML's `gmid_lut:`
key, whose presence alone sends a tech down the gm/Id path (`sizer.py`).

<a id="ft"></a>
### ft: the transistor's speed limit

**In plain words.** `ft = gm / (2π·Cgg)`: the frequency at which the device's
current gain falls to 1. Short `L` and strong inversion give a high `ft` (fast).
Long `L` and weak inversion give a low `ft` (slow, because a big device has big
capacitances).

**Where it shows up.** The LUT's `ft` field; the L-selection policy in
`GmIdModel`; estimating gate capacitance from `gm/ft` in the RNMC and two-stage
models.

<a id="parasitic-caps"></a>
### Parasitic capacitances (Cgs, Cgd, Cdb)

**In plain words.** Every transistor has small unwanted capacitors between its
terminals: gate–source `Cgs ≈ (2/3)·W·L·Cox`, gate–drain `Cgd`, and drain–bulk
`Cdb` (∝ W). They are tiny (femtofarads), but at the nodes inside an amplifier
they create extra [poles](#poles) that eat phase margin.

**Analogy.** Small hidden buckets hanging off every pipe junction. Each one adds
a little sluggishness.

**Where it shows up.** `equations.cgs` / `equations.cdb`, the `cox` and `cdb`
fields of `MosfetParams` (see the long comment in `tech_gf180mcu.yaml`), and the
first-stage node capacitance and third-stage gate capacitance in the
two-stage/RNMC loop models.

<a id="technology"></a>
### Technology, PDK, SPICE models, corners, mismatch, grid

**In plain words.**

- A **technology / process node** (gf180mcu = GlobalFoundries 180 nm,
  sky130 = SkyWater 130 nm, ptm45 = a "predictive" 45 nm model) is a specific
  factory recipe.
- The **PDK** (process design kit) is the factory's data package. The part the
  sizer cares about is the **SPICE model**: accurate (BSIM4) device equations
  that a simulator like **ngspice** uses.
- **Corners** (`typical`, `ss`, `ff`, `sf`, `fs`): the factory's chips vary from
  batch to batch. "ss" = slow NMOS and slow PMOS, and so on. A good design works
  at all corners.
- **Mismatch**: even two "identical" neighbouring transistors differ slightly.
  This matters for CMRR/PSRR of differential circuits.
- **Grid**: `W`, `L`, `C` can only take values on a manufacturing grid
  (`width.step`, `length.step` in the YAML), between a min and a max.
- **Fingers**: a very wide device is drawn as several narrower ones in parallel.
- **`generic`**: a made-up tech with only square-law parameters (no foundry
  model), for fast tests and the analytical path. `verify/` simulates it with a
  matching Level-1 card it writes itself.

**Where it shows up.** `TechParams`, `MosfetParams`, `GridSpec`, `SpiceLib` in
`models.py`; `loader.py::load_tech`; `config/tech_*.yaml`; `verify/deck.py`
(emits the model block); `_with_fingers` in `gmid/gmid_sizer.py`; the CLI
corner sweep.

---

<a id="part-3"></a>
## Part 3 — Building blocks

Op-amps are assembled from a handful of standard sub-circuits. The recognizer
(Layer 1/2) already found them in the netlist and put each device into a named
**slot** (`input_pair`, `load`, `tail_current`, `second_stage`, `bias_gen`,
`compensation`…). The sizer leans on those names (`physics/taxonomy.py`).

<a id="common-source"></a>
### Common-source stage: the basic amplifier

**In plain words.** One transistor with its source on a rail, the input on its
gate, and a *load* on its drain. A small gate wiggle `v_in` changes the drain
current by `gm·v_in`. That current flows through the resistance at the drain
node, making an output wiggle:

```
gain = −gm · Rout        (Rout = everything hanging on the output node, in parallel)
```

The minus sign means it inverts. **Gain = gm × Rout** is the single most
important equation in this codebase.

**Analogy.** A lever: gm is how hard you push, Rout is the length of the lever
arm on the far side.

**Where it shows up.** The `second_stage` / `third_stage` slots (`STAGE_SLOTS` in
`taxonomy.py`; `is_signal_device` picks the transistor whose gate is the
signal). `Stage` in `stage_chain.py` is literally `(gm, rout)`, and
`equations.open_loop_gain_db` multiplies the stage gains.

<a id="current-mirror"></a>
### Current mirror: copying a current

**In plain words.** Connect a transistor's gate to its own drain (*diode-
connected*). Push a current into it and it settles at whatever `Vgs` carries
that current. Share that gate voltage with a second transistor: same `Vgs`, so
the same current density. Make the second one `k×` wider (same `L`) and it
carries `k×` the current.

**Analogy.** A photocopier for currents, with a zoom knob (the width ratio).

**Rule of thumb.** `I_out / I_ref = (W_out/L) / (W_ref/L)` at matched `L`.
Mirrors only copy accurately when both devices see similar `Vds` (hence the
cascoded mirrors in the bias generator).

**Where it shows up.** `gmid/geometry.py` step 4 (mirror ratios: output `W` =
exact current ratio × reference `W`); the "mirror group" repairs in `bias.py` and
`stage_interface.py` move a *whole gate group* together so the ratios survive;
the current-mirror load ([loads](#loads)).

<a id="diff-pair"></a>
### Differential pair and tail current source

**In plain words.** Two matched transistors whose sources share one node, fed
by a single **tail current source** `I_tail`. The pair responds to the
*difference* between its two gate voltages: one side takes more of `I_tail`,
the other less. If both gates move together (a *common-mode* change), ideally
nothing happens because the tail fixes the total.

- **Differential mode (DM)**: `v1 − v2`, the signal we want.
- **Common mode (CM)**: `(v1 + v2)/2`, the average, which we want to ignore.

Each device carries `I_tail/2` at rest. Its gm is `gm1`, the most important gm
in the amplifier ([GBW](#gbw)).

**Analogy.** A seesaw on a fixed pivot. Push one end down and the other comes
up (difference). Lift the whole seesaw (common mode) and, if the pivot is solid
(a perfect tail source), the tilt does not change.

**Where it shows up.** The `input_pair` and `tail_current` slots;
`HALF_BIAS_SLOTS` (each pair device carries ibias/2); `gmid/bias.py` (the tail
needs headroom under the pair); [CMRR](#cmrr) depends on how good the tail is
(`gd_tail`).

<a id="loads"></a>
### Loads: what sits on top of the pair

The input pair's current must be turned into a voltage by a load. The load's
resistance sets the first stage's gain (`gain = gm1·Rout1`). The template
variants differ mostly here:

| Load kind | What it is | Gain | Notes |
|---|---|---|---|
| **Resistor** | a plain resistor to the rail | low (R limited by headroom: `R = V_drop/I`) | `circuit_0001` in the running example; why its gain falls short |
| **Current-mirror (active) load** | a mirror whose diode side is on one pair device, its copy on the other | high: `Rout ≈ ro_pair ∥ ro_load` | converts differential to single-ended *and* doubles the signal; the textbook choice (`circuit_0010`) |
| **Current-source load** | transistors gated by a bias rail | high | needs something else (often CMFB) to set its DC level |
| **Cascode load** | a load with a [cascode](#cascode) stacked in it | very high | costs headroom |

**Half the signal without a mirror.** With a single-ended output taken from one
side of the pair, only that side's current change reaches the output, so the
first stage delivers `gm1·Rout1 / 2`. A current-mirror load copies the other
side's change across and recovers the full `gm1·Rout1`. The code calls this
factor `k_fs` (1 or 0.5, `preprocess._first_stage_gain_factor`). It applies to
gain and to gm1's role in GBW/PM, and it is part of why `circuit_0001` falls
short on gain.

**Analogy.** The load is the spring that the pair pushes against. A stiffer
spring (higher resistance) gives a bigger displacement for the same push.

**Where it shows up.** `gmid/analyze.py::LoadKind` / `classify_load`;
`physics/preprocess.py::size_load_resistors` (rail-referenced resistor loads);
`gmid/geometry.py`'s load margin.

<a id="cascode"></a>
### Cascode: stacking for a much higher output resistance

**In plain words.** Put a second transistor in series on top of a current
source, with its gate on a fixed bias voltage. It holds the lower device's drain
voltage almost constant, so the lower device's leak barely matters. The output
resistance gets **multiplied**:

```
R_out ≈ ro_c · (1 + gm_c · R_below)      ≈  gm_c · ro_c · R_below
```

That is roughly the intrinsic gain (×20–200) in resistance, and so in gain.

The price is **headroom**. Each stacked device needs its own `Vdsat`, so a
cascode stack needs the *sum* of the Vdsats. Two common op-amp styles use this:

- **Telescopic**: cascodes stacked directly above the input pair. High gain and
  low power, but very little headroom.
- **Folded**: the signal current is "folded" into a separate cascode branch.
  More headroom, more current.

**Analogy.** A pressure regulator placed in front of your flow regulator. The
downstream device no longer feels pressure changes, so the combination is far
more stubborn. But you now need enough pressure to run *two* regulators.

**Where it shows up.** `physics/stage_chain.py::node_rout` (the cascode-aware
walk, which computes the first form above); `gmid/analyze.py::cascode_device_refs`;
`gmid/bias.py` pass 2 (the cascode stack budget); `gmid/stage_interface.py` (the
cascode load's saturation *window*); `physics/node_levels.py` (the device under a
cascode sits just above its knee, where the LUT's `gds` is wrong, issue #273).

<a id="degeneration"></a>
### Source degeneration

**In plain words.** A resistor `Rs` between a transistor's source and the rail.
It reduces the effective transconductance to `gm_eff = gm / (1 + gm·Rs)`, which
makes it more linear and less sensitive to the device. As a side effect it
*raises* the output resistance, the same `1 + gm·R` effect as a cascode.

**Where it shows up.** `gmid/resistors.py` (sizes degeneration r1/r2 and reports
the gm1 reduction through `MetricModifiers`), `gmid/evaluate.py` (applies it),
`node_rout` (the resistor version of the cascode boost).

<a id="source-follower"></a>
### Source follower (output buffer)

**In plain words.** The input on the gate, the output taken from the *source*.
The source "follows" the gate one `Vgs` lower. Gain ≈ 1, so it does not amplify,
but its output resistance is low (`≈ 1/gm`), so it can drive a heavy load
without the previous stage noticing. The cost is a **level shift** of `Vgs`,
which eats output swing.

**Analogy.** A power steering assist: you turn the wheel with the same angle
(gain 1), but the assist provides the force.

**Where it shows up.** `OUTPUT_STAGE_SLOTS` in `taxonomy.py` (deliberately kept
out of the gain product); the "output stage" row of `intent.py` (gm/Id 15);
`stage_chain.check_follower_swing`; buffered RNMC in `physics/rnmc.py`.

<a id="bias-generator"></a>
### Bias generator

**In plain words.** A small side circuit that turns the one external reference
current `ibias` into all the gate voltages (*bias rails*) the rest of the circuit
needs: mirror gates, cascode gates, and so on. Its design goal is stable,
accurate currents and voltages, not gain.

**Analogy.** The building's pressure-regulation room that sets the water
pressure on every floor.

**Where it shows up.** The `bias_gen` slot; nets named `net_bias*`, `*_pref`,
`*_ncasc` (excluded from "signal" by `is_signal_device`); the synthesizer builds
the generator from `config/bias_legs.yaml`; `gmid/bias_levels.py` sizes its
level-setting diodes and floor resistors.

---

<a id="part-4"></a>
## Part 4 — The op-amp

<a id="opamp"></a>
### What an op-amp is and why gain matters

**In plain words.** An operational amplifier is a differential amplifier with
*huge* gain (here ≥ 60 dB = 1000×, usually 80 dB+). Nobody uses that gain
directly. The op-amp is wrapped in **negative feedback** (a few resistors or
capacitors), and the huge gain makes the closed-loop behaviour depend only on
those accurate passive parts. More open-loop gain means a more accurate
closed-loop circuit.

**Analogy.** A very strong but clumsy worker supervised by a precise foreman
(the feedback network). The stronger the worker, the more exactly the foreman's
plan gets carried out.

**Where it shows up.** `gain_min_db` in `SizingSpec`; the open-loop gain metric;
`equations.open_loop_gain_measurable` (a gain so huge that SPICE cannot measure it
in its rig gets flagged); every `verify/` testbench wraps the op-amp in a
feedback rig.

<a id="stages"></a>
### Stages: one, two, three

**In plain words.** One stage rarely gives enough gain, so stages are chained.
Total gain is the product of stage gains:

```
gain = (gm1·R1) · (gm2·R2) · (gm3·R3) …
```

- **First stage**: the [differential pair](#diff-pair) plus its [load](#loads).
- **Second (and third) stage**: [common-source](#common-source) gain stages.
- **Optional output stage**: a [source follower](#source-follower) buffer.

Each extra stage adds gain *and* an extra [pole](#poles), so it gets harder to
keep stable ([Miller](#miller), [RNMC](#nested-miller)).

**Where it shows up.** The topology names (`two_stage_opamp_single_ended`, …);
`SECOND_STAGE_SLOTS`, `THIRD_STAGE_SLOTS`, `OUTPUT_STAGE_SLOTS`;
`second_stage_current_ratio` in the spec (stage 2 gets 2.5× the first-stage
current); `StageChain` in `stage_chain.py`.

<a id="fully-differential"></a>
### Single-ended (SE) vs fully differential (FD)

**In plain words.** A *single-ended* op-amp has one output, measured against
ground. A *fully differential* one has two outputs, `out+` and `out−`, and the
signal is their difference. FD gives 2× the swing and rejects noise that hits
both outputs equally, but it creates a new problem: nothing in the differential
loop controls the **average** of the two outputs ([CMFB](#cmfb)).

FD circuits are built as two mirrored *halves* (`second_stage_p` /
`second_stage_n` slots).

**Where it shows up.** Slot names with `_p`/`_n`; `rnmc.worst_half`; FD
branches in `stage_chain.py` and `stage_interface.py` (issue #161); the FD rigs
in `verify/measure.py`.

<a id="cmfb"></a>
### Common-mode feedback (CMFB)

**In plain words.** In an FD amplifier the two outputs' *average level* (the
output common mode) is set by a tug-of-war between PMOS and NMOS current sources
that is never exactly balanced. Left alone, both outputs drift to a rail. A CMFB
circuit **senses** the average of the two outputs (often with a resistor
averager), compares it with a target (usually mid-rail), and **adjusts** a load's
bias to correct it.

**Analogy.** A thermostat. The differential signal is the conversation you're
having; the CMFB just keeps the room temperature (the average level) at 21 °C so
the conversation can happen.

**Where it shows up.** CMFB rows in `gmid/intent.py`; the "CMFB averager loading"
folded into metrics in `gmid/evaluate.py`; `stage_chain.cmfb_sense_range` /
`check_cmfb_sense_swing`; the CMFB-coupled FD halves in the loop model
(`equations._miller_network`). Memory note: FD designs had a CMFB stability
story of their own (high-gain CMFB was unstable).

<a id="headroom"></a>
### Headroom and stacking: does it fit between the rails?

**In plain words.** Follow one current path from `Vdd` down to `Vss`. Every
transistor on it in saturation needs at least its `Vdsat`, a diode-connected one
needs its whole `Vgs`, and a resistor needs `I·R`. All of these must add up to
less than `Vdd − Vss`. At 3.3 V that is usually easy; at the ~1 V of 45 nm it
is tight.

Typical casualty: with a PMOS input pair, the tail's drain sits at
`Vcm + |Vgs_pair|`, leaving the PMOS tail too little room under `Vdd`. It falls
into triode and supplies far less current than planned, so gm1 and GBW collapse
(issue #76).

**Analogy.** Stacking boxes under a ceiling. Every box (device) has a minimum
height. If the stack is too tall, the box at the top gets crushed (falls into
triode).

**Where it shows up.** `gmid/bias.py::check_dc_operating_point` (tail headroom
repair + cascode stack budget); `bias_feasible`; the SPICE `.op` check in
`verify/op.py`.

<a id="stage-interface"></a>
### Stage interface: two stages must agree on one voltage

**In plain words.** The first stage's output node is also the second stage's
input gate. The *second* stage decides that node's DC level: its input device
needs a particular `Vgs`, so the node sits at `Vss + Vgs2`. But the *first*
stage's load (especially a cascode load) needs that same node inside a window
where all its devices stay saturated. If the level the second stage wants is
outside the window the first stage allows, something drops out of saturation.

**Analogy.** Two pipes joined at one junction: the downstream pipe dictates the
pressure at the junction, and the upstream regulator only works within a band of
pressures. They must be designed to agree.

**Where it shows up.** `gmid/stage_interface.py::check_stage_interface`
(issue #124 single-ended, #161 fully differential); the load-resistor bias seed
in `preprocess.size_load_resistors` ("the driven signal device's Vgs").

<a id="swing"></a>
### Output swing

**In plain words.** How far the output can move up and down before some device
at the output leaves saturation. Roughly from `Vss + Vdsat(bottom stack)` to
`Vdd − Vdsat(top stack)`. Cascodes and followers eat into it.

**Where it shows up.** `output_swing_min_v` / `output_swing_max_v` in the spec;
`stage_chain.output_swing_headroom`, `check_follower_swing`,
`check_cmfb_sense_swing`; `gmid/geometry.py::swing_gm_id_floor`; the swing bench
in `verify/measure.py`.

---

<a id="part-5"></a>
## Part 5 — Speed and stability

<a id="poles"></a>
### Poles: every node is a low-pass filter

**In plain words.** Each node in the signal path has a resistance to AC ground
`R` and a capacitance `C`. Above the frequency `f_p = 1/(2π·R·C)` (a *pole*) the
capacitor starts swallowing the signal, so gain falls by 10× per decade of
frequency (−20 dB/decade). Each pole also delays the signal, adding up to 90°
of *phase lag*: 45° at the pole frequency itself, approaching 90° above it.

Plotting gain (dB) and phase against log-frequency gives a **Bode plot**, the
standard picture of an amplifier.

**Analogy.** Each node is a bucket fed through a narrow pipe. Slow changes get
through, but fast sloshing is smoothed out, and what does get through arrives
late.

**Where it shows up.** `equations.py` (the phase-margin functions are sums of
`atan(f/f_p)` lag terms); the numeric pole finder `equations._poles` used by the
nodal loop models.

<a id="gbw"></a>
### GBW: gain-bandwidth product (unity-gain frequency)

**In plain words.** A well-compensated op-amp has one dominant pole that makes
gain fall steadily from its DC value. Along that slope, gain × frequency is
constant, the **gain-bandwidth product**. Equivalently, it is the frequency
where gain drops to 1 (0 dB), the *unity-gain frequency* or 0 dB crossing. For
a Miller-compensated op-amp:

```
GBW = gm1 / (2π · Cc)
```

So GBW is set by just two things: the input pair's `gm1` and the compensation
cap `Cc`. That is why the spec's `gbw_min_hz` becomes a *gm1 requirement* in the
sizer.

**Analogy.** A fixed budget. You can have a lot of gain at low frequency or some
gain at higher frequency, but the product is fixed.

**Where it shows up.** `equations.unity_gain_bw`; `compute_requirements`
(gm1 ≥ 2π·GBW·Cc); `two_stage_loop` / `nested_miller_loop` / `_loop_gbw_pm`
(the full-model GBW: find where the computed gain actually crosses 0 dB,
issue #274); `gbw_min_hz` in the spec.

<a id="phase-margin"></a>
### Feedback, oscillation, and phase margin

**In plain words.** In negative feedback the output is fed back and *subtracted*
from the input. But each pole delays the signal. If the total delay reaches
**180°** while the loop gain is still ≥ 1, the subtraction becomes an *addition*.
The loop then feeds itself and **oscillates**.

**Phase margin (PM)** = 180° − (total phase lag at the 0 dB crossing). It says
how far you are from that cliff:

| PM | Behaviour |
|---|---|
| < 0° | oscillates |
| ~30° | rings badly when stepped |
| **60°** | the usual spec: settles quickly with little overshoot |
| ~90° | very calm but slow |

**Analogy.** Adjusting a shower whose hot water takes a few seconds to arrive.
If you react to the temperature you feel now, the delay makes you overshoot, then
overshoot back, and you oscillate between scalding and freezing. Phase margin is
how much delay you can tolerate before that happens.

**Rule of thumb (two-stage).** The first pole is dominant. The second pole sits
near `gm2/CL`. If it is far above GBW, PM is good:

```
PM ≈ 90° − atan(GBW / f_p2) = 90° − atan(gm1·CL / (gm2·Cc))
```

So to raise PM: raise `gm2`, raise `Cc` (which lowers GBW), or lower `gm1`.

**Where it shows up.** `phase_margin_min_deg` in the spec;
`equations.phase_margin_*_deg` (closed forms); `two_stage_loop`,
`nested_miller_loop`, `phase_margin_rnmc_deg` (numeric full models);
`gmid/two_stage_refine.py` and `gmid/rnmc_refine.py` (re-plan when the full
model disagrees with the closed form). Memory note: an earlier AC bench missed
later 0 dB crossings (#269), which is why the code takes the *worst* PM over all
crossings.

<a id="rhp-zero"></a>
### The right-half-plane (RHP) zero and the nulling resistor

**In plain words.** The Miller cap `Cc` also lets signal sneak *forward* from
input to output of the second stage. At high frequency that creates a *zero*
that adds phase **lag** (the bad kind), at `f_z ≈ gm2 / (2π·Cc)`. Putting a
resistor `Rc` in series with `Cc` moves the zero out of the way, or even uses it
to cancel the second pole.

**Rule of thumb.** `Rc = (Cc + C_out) / (gm2 · Cc)` (cancels the output pole,
`equations.compensation_resistor_ohm`).

**Where it shows up.** The `compensation` slot's resistor, sized in
`gmid/resistors.py`; `rnmc.comp_resistances`; the "bare Miller cap's RHP zero"
that `two_stage_refine.py` accounts for.

<a id="miller"></a>
### Miller compensation: deliberately slowing one node

**In plain words.** A two-stage op-amp has two nodes with high resistance, so
two low-frequency poles close together, which means poor phase margin. The fix
is to connect a capacitor `Cc` across the second stage (from its input to its
output). Because the second stage has gain `A2`, the first-stage node sees that
cap multiplied by `A2` (the *Miller effect*). That pushes the first pole *down*
(dominant), while the second stage's feedback pushes the output pole *up*.
This is called **pole splitting**.

**Analogy.** Two people (poles) arguing at similar volume cause chaos. Make one
speak very quietly (the dominant pole) and the other clearly louder, so the
conversation becomes orderly.

**Rule of thumb.** Common sizing practice: `Cc ≈ 0.2–0.3 × CL`, then
`gm1 = 2π·GBW·Cc`, then `gm2` big enough for PM.

**Where it shows up.** `compute_requirements` in `physics/preprocess.py`
(Cc and gm targets); `size_compensation_caps`; `SizingResult.capacitors`; the
`compensation` slot.

<a id="nested-miller"></a>
### Three stages: nested Miller (NMC) and reversed nested Miller (RNMC)

**In plain words.** Three stages mean three poles, which need two compensation
caps.

- **NMC**: an outer `Cc1` from the output back to the first-stage output, and an
  inner `Cc2` around the last stage.
- **RNMC** (PR #249; the main three-stage scheme here, though NMC templates
  `three_stage_opamp_nmc_*` exist too): *both* caps start at the
  first-stage output. `Cc1` goes to the amplifier output and `Cc2` to the
  second-stage output.

In RNMC the two non-dominant poles become a **complex pair**: they behave like
a spring-and-mass that can *ring*. How much it rings is the **damping ratio ζ**.
ζ ≈ 0.7 is the sweet spot; ζ ≤ 0 means the pair is in the right half plane and
the amplifier oscillates. The pair stays stable only while
`gm3 < gm2 · (1 + CL/Cc1)`.

**Analogy.** Car suspension. Too little damping and the car keeps bouncing after
a bump. Negative damping means each bounce gets bigger. ζ ≈ 0.7 settles in one
smooth motion.

**Rule of thumb.** The knobs, in the order the code tries them: raise `gm2`;
raise `Cc2`; as a last resort cap `gm3` (which costs gain).

**Where it shows up.** `physics/rnmc.py::design_rnmc`;
`equations.rnmc_pole_damping`, `phase_margin_rnmc_deg`, `rnmc_min_gm2`;
`gmid/rnmc_refine.py` (re-run after geometry, because the sized circuit's real
gm and gate caps differ from the estimates); `docs/theory/rnmc_stability.rst`,
`docs/theory/rnmc_buffered_oscillation.rst`.

<a id="slew"></a>
### Slew rate: the large-signal speed limit

**In plain words.** Small-signal analysis assumes tiny wiggles. With a big
input step, the input pair switches fully to one side and dumps its *entire*
tail current into the Miller cap. The output can then only ramp at a fixed rate:

```
SR = I / C       (e.g. I_tail / Cc for the first stage; I_stage2 / (Cc + CL) at the output)
```

The amplifier slews at its *slowest* such node. The code scales the
first-stage term by `k_slew` (`stage_chain.py`): 1 for a single-ended amplifier
with a current-mirror load, 0.5 otherwise (a resistor or plain current-source
load, or a fully differential output), because then only half the tail current
reaches the node being slewed.

**Analogy.** Filling a bucket with a fixed hose. No matter how loudly you shout,
it fills at hose-flow ÷ bucket-area.

**Where it shows up.** `equations.slew_rate_vps`; `slew_rate_min_vps` in the
spec; the slew bench in `verify/measure.py`. Memory note: an earlier
analytical-vs-SPICE slew gap was a bench settling artifact, now fixed.

---

<a id="part-6"></a>
## Part 6 — The eight spec metrics

<a id="spec-metrics"></a>
### The spec sheet (`SizingSpec`)

| # | Metric | Spec field | What it asks | Mainly set by |
|---|---|---|---|---|
| 1 | Gain | `gain_min_db` | open-loop DC gain ≥ X dB | ∏ gm·Rout ([stages](#stages), [cascode](#cascode)) |
| 2 | GBW | `gbw_min_hz` | 0 dB crossing ≥ X Hz | gm1 / Cc ([GBW](#gbw)) |
| 3 | Phase margin | `phase_margin_min_deg` | stable with margin | gm2 vs gm1, Cc, CL ([PM](#phase-margin)) |
| 4 | Slew rate | `slew_rate_min_vps` | large-signal ramp ≥ X V/s | I / C ([slew](#slew)) |
| 5 | Power | `power_max_w` | burn ≤ X W | total supply current ([power](#power)) |
| 6 | Swing | `output_swing_min_v` / `_max_v` | output can reach these levels | output stack Vdsats ([swing](#swing)) |
| 7 | CMRR | `cmrr_min_db` | ignore common mode | tail quality ([CMRR](#cmrr)) |
| 8 | PSRR | `psrr_min_db` | ignore supply noise | bias quality ([PSRR](#psrr)) |

Plus the *operating conditions* (not targets): `vdd`, `vss`, `ibias` (the
reference current, 20 µA in the gf180 spec), `cl` (load cap), and
`second_stage_current_ratio` / `third_stage_current_ratio` (how many times the
first-stage current each later stage gets).

The sizer reports each metric in `SizingResult.metrics` and the *spec margin*
(how much better or worse than asked) in `SizingResult.margins`.

<a id="power"></a>
### Power

`P = (Vdd − Vss) · Σ I_from_supply`. It is just the total current drawn from
`Vdd` times the supply voltage. The subtle part is deciding *which* branches
draw from the supply (`stage_chain.vdd_supply_currents`).
`equations.quiescent_power`.

<a id="cmrr"></a>
### CMRR: common-mode rejection ratio

**In plain words.** How much more the amplifier responds to the *difference* of
its inputs than to their *average*: `CMRR = A_dm / A_cm`, in dB. A perfect tail
current source means zero common-mode response. A leaky tail (`gd_tail > 0`)
lets the common mode through.

**Analogy.** The seesaw's pivot is on a slightly springy floor. Lift the whole
seesaw and the pivot gives a little, which looks like a tilt.

**Rule of thumb (simple form in the code).** `CMRR ≈ gm1 / (2·gd_tail)`
(`equations.cmrr_db`). Most single-ended circuits now get a **nodal** CMRR
instead ([nodal analysis](#nodal-analysis), `reports_nodal_rejection`), and a
CMRR target can force a re-size with a raised gm1 floor
(`preprocess.lift_nodal_cmrr`, issue #296).

<a id="psrr"></a>
### PSRR: power-supply rejection ratio

**In plain words.** How much more the amplifier responds to its input than to
noise on `Vdd`: `PSRR = A_dm / A_supply`. Supply noise leaks in through every
device whose source or bias is tied to `Vdd`.

**Rule of thumb (simple form).** `PSRR ≈ gm2 / gd_bias` (`equations.psrr_db_approx`),
or the nodal version where available.

<a id="nodal-analysis"></a>
### Nodal analysis: solving the small-signal circuit as a matrix

**In plain words.** Replace every transistor by its small-signal model
([gm, gds, caps](#small-signal)), write [KCL](#kcl) at every node, and you get a
linear system `(G + j·2πf·C) · v = b`. `G` holds conductances and `C` holds
capacitances. Solve it at many frequencies to get the exact gain and phase, find
the 0 dB crossing (GBW), read the phase there (PM), or find the poles as the
system's eigenvalues. The closed-form rules of thumb above are simplifications
of this.

**Analogy.** A spreadsheet of "flow in = flow out" balance equations, one row per
junction, solved all at once instead of by hand.

**Where it shows up.** `equations.py`'s `_rnmc_network`, `_miller_network`,
`two_stage_loop`, `nested_miller_loop`, `_poles`, `_crossings`; the nodal
CMRR/PSRR solve in `physics/stage_chain.py`. This is exactly what ngspice does in
an `.ac` analysis, but on a much simpler device model.

---

<a id="part-7"></a>
## Part 7 — The sizing problem

<a id="sizing-problem"></a>
### What "sizing" means

**In plain words.** The topology (which devices exist and how they connect) is
already fixed by earlier layers. Sizing chooses the *numbers*: `W` and `L` for
every transistor, the value of every resistor and capacitor, so that the eight
metrics meet the spec. The sizer's output is `SizingResult`: per-device
`TransistorSizing`, `resistors`, `capacitors`, the predicted `metrics` and
`margins`, a `bias_feasible` verdict, and human-readable `warnings`.

**Analogy.** An architect hands you a floor plan (topology). You choose the
beam thicknesses and pipe diameters (W/L) so that the building meets its code
(spec).

<a id="two-strategies"></a>
### Two strategies: search vs forward pass

| | Analytical (Level-1, `analytical/`) | gm/Id (`gmid/`) |
|---|---|---|
| Device model | [square law](#square-law) | measured [LUT](#gmid) |
| Approach | **search**: write the requirements as integer linear constraints, let a solver (OR-Tools CP-SAT) find W, L | **forward pass**: currents (KCL) → required gm → gm/Id → LUT → W |
| Used for | `generic` (toy tech) | gf180mcu, sky130, ptm45 (real PDKs) |
| Accuracy | qualitative | close to SPICE for most metrics |

Both paths share the *topology math* in `physics/`: [KCL](#kcl) currents, spec →
gm requirements, the stage chain, and metric formulas. Only the device
primitives differ (`DeviceModel`: `Level1Model` vs `GmIdModel`).

**Analogy.** Search = trying combinations on a lock with a smart locksmith.
Forward pass = reading the combination off the manufacturer's spec sheet.

<a id="analytical-vs-spice"></a>
### Analytical estimate vs SPICE: who is the referee?

**In plain words.** Everything the sizers compute is a *hand-calculation-style
estimate* (fast and deterministic). **ngspice** with the foundry model is the
*referee*: it simulates the sized netlist and measures each metric in a
testbench. For LUT techs the CLI *shows the SPICE numbers*. The analytical ones
are a quality signal for tests and for the sizer's own decisions. Gaps between
the two are either model simplifications (known, documented) or bugs.

**Where it shows up.** `gmid/evaluate.py` (analytical) vs `verify/simulate.py`
(SPICE, same metric keys); `verify/op.py::check_bias_soundness` (the SPICE DC
verdict, which the CLI uses to *downgrade* an analytically feasible
`bias_feasible`; it never upgrades one); `docs/` prediction-error
page (#256).

<a id="pipeline-layers"></a>
### Where the sizer sits in CircuitGenome

```
synthesizer   topology templates → many netlist variants (circuit_0001 … )
Layer 0       netlist parser            → ParsedNetlist
Layer 1       subcircuit recognizer (SR) → primitive structures (pairs, mirrors, …)
Layer 2       functional block recognizer (FBR) → every device in a named *slot*
Layer 3       sizer  ← you are here      → SizingResult (W/L/R/C + metrics)
              verify/ (ngspice)          → measured metrics, DC soundness
```

The sizer does not re-discover structure. It trusts the FBR slots and the naming
conventions in `physics/taxonomy.py`. That is why a new topology whose slots
follow the conventions needs no sizer change.
