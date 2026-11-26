# Stop 6 — `verify/`: the ngspice referee

**Files**:
[`simulate.py`](../../../circuitgenome/sizer/verify/simulate.py) (129) ·
[`deck.py`](../../../circuitgenome/sizer/verify/deck.py) (297) ·
[`rig.py`](../../../circuitgenome/sizer/verify/rig.py) (108) ·
[`measure.py`](../../../circuitgenome/sizer/verify/measure.py) (785) ·
[`op.py`](../../../circuitgenome/sizer/verify/op.py) (348) ·
[`__init__.py`](../../../circuitgenome/sizer/verify/__init__.py) (39) — 1,706 lines in all
**Time**: 2–3 h
**After this stop you can**:
- explain what ngspice is given (the deck) and how each of the nine metrics is
  measured on it;
- say why every bench wraps the op-amp in a feedback loop, and how the code
  finds which input is `+`;
- read a `check_bias_soundness` verdict and trace a `None` metric back to its
  cause.

---

## Background for this stop

Concepts you already have (follow the links if they are hazy):

- **Who is the referee.** Everything in Stops 3–5 is an *estimate*. `verify/`
  re-measures the sized circuit with a real simulator
  ([analytical vs SPICE](00_background.md#analytical-vs-spice)).
- **Operating regions.** The DC check asks one question per transistor: is it
  in saturation, in triode, or off? ([regions](00_background.md#regions),
  [Vdsat](00_background.md#vdsat))
- **The metrics.** Gain, [GBW](00_background.md#gbw),
  [phase margin](00_background.md#phase-margin), [slew](00_background.md#slew),
  [power](00_background.md#power), [swing](00_background.md#swing),
  [CMRR](00_background.md#cmrr), [PSRR](00_background.md#psrr). There is one
  testbench per metric (gain, GBW and PM share one).
- **Technology, corners, mismatch.** The deck pulls in the foundry's
  [SPICE model](00_background.md#technology) at a chosen corner. For
  [fully-differential](00_background.md#fully-differential) circuits it can also
  switch on random device mismatch.

New concepts for this stop:

### SPICE and ngspice

**In plain words.** SPICE is a circuit simulator. You give it a list of
components and the nodes they connect to. It writes [KCL](00_background.md#kcl)
("current in = current out") at every node, using the **full nonlinear** device
equations: BSIM4 for gf180mcu, sky130 and ptm45, which have hundreds of
parameters per transistor. Then it solves them. Nonlinear equations are solved
by iteration (Newton–Raphson), and iteration can **fail to converge**. That is
why every function in this package is ready to get nothing back. ngspice is the
open-source SPICE that this repo runs as a subprocess (`ngspice -b deck.sp`).

**Analogy.** The sizer is a hand calculation on a napkin. ngspice is the
wind tunnel: slower and harder to set up, but it tests the real shape.

### Netlist, `.subckt`, deck

For a software engineer:

| SPICE word | What it is | Software analogy |
|---|---|---|
| netlist | text, one component per line: `name node node … model params` | source code |
| `.subckt name ports … .ends` | a reusable block with named ports | a function definition (ports = parameters) |
| `Xdut n1 n2 … name` | one instance of a subckt, wired to nodes `n1 n2 …` | a function call |
| `.include` / `.lib file corner` | pull in device models (a `.lib` picks one section, e.g. `typical`) | `import`, with a build flavour |
| **deck** | the complete file ngspice runs: title line, models, the subckt, sources, analysis commands | a whole program with `main()` |
| `.control … .endc` | a script block: run analyses, `print` values, `wrdata` tables to a file | the body of `main()` |

The circuit under test is called the **DUT** (device under test). Here it is
always the sized op-amp `.subckt`.

### The four analyses the benches use

| Command (in `.control`) | What it computes | Metrics it gives |
|---|---|---|
| `op` | the **DC operating point**: every node voltage and device current with all inputs held still | power (`i(Vsup)`), per-device `id`/`vds`/`vdsat` for the bias check |
| `ac dec 30 1m 10G` | linearise around the operating point, then sweep a 1 V sine from 1 mHz to 10 GHz (30 points per decade) | gain, GBW, PM (one bench); CMRR, PSRR (one frequency each) |
| `tran step stop` | time-domain simulation of a large step | slew rate |
| `dc Vin 0 3.3 step` | repeat the operating point while sweeping one source | output swing |

`ac` is the same kind of linear solve as the sizer's own
[nodal analysis](00_background.md#nodal-analysis), but on the full device model.

### Testbench, rig, bench

**In plain words.** You cannot measure an op-amp by itself. It needs a supply,
its bias reference current, a load capacitor, and a source and a probe for the
quantity you want. That surrounding circuit is a **testbench**.

**Analogy.** A test fixture or harness around a unit under test. In this code
the **rig** ([`rig.py`](../../../circuitgenome/sizer/verify/rig.py)) is the part
every bench shares: supply, bias current, load caps, the `Xdut` line. Each
**bench** in [`measure.py`](../../../circuitgenome/sizer/verify/measure.py) adds
its own stimulus and analysis. It is like a pytest fixture plus one test
function per metric.

### Why the op-amp sits inside a feedback loop with a huge L and C

**The problem.** An op-amp has a gain of about 10,000 (80 dB). Tie both inputs
to the same voltage and the output should sit mid-rail. But a 0.3 mV offset,
which any real or simulated circuit has, times 10,000 is 3 V. The output slams
into a supply rail, every transistor leaves its intended region, and nothing
you measure means anything.

**The trick.** Close the loop **only at DC**
([`measure.py:157`](../../../circuitgenome/sizer/verify/measure.py#L157)):

```
Vcm cm 0 1.65          ← mid-supply reference
Lfb out inn 1e12       ← 1 TH inductor: a short at DC, an open at any real frequency
Cfb inn cm 1e3         ← 1 kF capacitor: an open at DC, a short to cm at AC
Vid inp cm ac 1        ← the 1 V AC stimulus on the non-inverting input
```

At DC the inductor is a wire, so the op-amp is a unity-gain buffer. Negative
feedback moves the inverting input until the output sits at Vcm, which cancels
the offset. At the sweep frequencies the inductor is a near-infinite impedance
(the loop is open) and the capacitor ties `inn` to `cm` (an AC ground). So the
`ac` sweep sees the **open-loop** gain from `inp` to `out`.

**Analogy.** Auto-tare on a very sensitive kitchen scale. A slow hand keeps
re-zeroing the needle so it never pins against its stop. That hand is far too
slow to react to the quick taps you are actually measuring. Where it breaks:
the "hand" is not infinitely slow. At 1 mHz the loop still feeds back a fraction
β = 1/(ω²·L·C) ≈ 2.5·10⁻¹¹ of the output. So a gain above about 212 dB would
close the loop even there. That is the constant
[`AC_LOOP_LIMIT_DB`](../../../circuitgenome/sizer/verify/measure.py#L48), and the
sizer imports it to know which gains the bench can measure
([`equations.py:18`](../../../circuitgenome/sizer/physics/equations.py#L18)).

### Input polarity: which input is `+`?

The netlist ports are just `in1` and `in2`. Which one is non-inverting depends
on the topology: how many inverting stages follow the pair, and which side the
mirror load turns around. The DC loop must go to the **inverting** input. If it
goes to the other one, it is positive feedback and the output latches to a rail.
The benches therefore **try both assignments**
([`rig._POLARITIES`](../../../circuitgenome/sizer/verify/rig.py#L19)). The AC
bench picks the one that settles with a plausible measurement and hands it to
the later benches as a **hint**. The hint can be wrong (see 6.4), so the
large-signal benches still check it.

---

## 6.1 `simulate.py` (129 lines)

**Job in one sentence.** Run every bench on one sized circuit and return the
same nine metric keys that the analytical `evaluate_metrics` returns, with
`None` for anything that could not be measured.

**Design knowledge it encodes.**
- SPICE is the [referee](00_background.md#analytical-vs-spice). The keys match
  `SizingResult.metrics` so the CLI can print the two columns side by side.
- [CMRR](00_background.md#cmrr) and [PSRR](00_background.md#psrr) are *ratios
  against* the differential gain. Without a trusted gain they mean nothing.
- [Corners](00_background.md#technology): one parameter re-runs everything at
  `ss`, `ff`, and so on.

**Walkthrough.**

1. [`simulate.py:20`](../../../circuitgenome/sizer/verify/simulate.py#L20):
   the signature `simulate_metrics(netlist_text, result, tech, spec, corner=None)`.
   It takes the **unsized** netlist plus the `SizingResult` and does the sizing
   injection itself. `corner=None` means the tech's nominal corner (`typical`
   on gf180).
2. [`simulate.py:41-44`](../../../circuitgenome/sizer/verify/simulate.py#L41):
   build the DUT once. Parse the `.subckt`, list MOSFETs that have no size
   (`unsized_mos_refs`), inject W/L/R/C (`_inject_sizes`), and wrap it in the
   model block (`_dut`). All of these are covered in 6.2.
3. [`simulate.py:45-49`](../../../circuitgenome/sizer/verify/simulate.py#L45):
   `_Topo(ports)` decides single-ended (SE) vs fully differential (FD) **from
   the port names alone** (`outp`+`outn` → FD). `vcm = (vdd + vss)/2` is the
   mid-supply point every bench biases at. `args` is the 8-tuple every bench
   takes.
4. [`simulate.py:51-56`](../../../circuitgenome/sizer/verify/simulate.py#L51):
   **every key starts as `None`**. A bench only ever overwrites a key with a
   number.
5. [`simulate.py:63-69`](../../../circuitgenome/sizer/verify/simulate.py#L63):
   the `_oops` pattern. Each bench call is wrapped in its own
   `try/except Exception`. An exception becomes a note
   (`"power bench failed: …"`), and the next bench still runs.
6. [`simulate.py:70-83`](../../../circuitgenome/sizer/verify/simulate.py#L70):
   the **AC bench runs before the others**, because it returns the input
   `polarity` that slew, swing, CMRR and PSRR reuse. If AC reports a `reason`
   (no gain, did not settle), `_bias_diagnostic` (6.5) adds a one-line hint
   such as "in triode: m2_tail_current".
7. [`simulate.py:84-93`](../../../circuitgenome/sizer/verify/simulate.py#L84):
   slew and swing, each given the polarity hint. Slew also gets the spec target
   so it can size its time window.
8. [`simulate.py:94-126`](../../../circuitgenome/sizer/verify/simulate.py#L94):
   CMRR/PSRR run only if `ac_clean`, meaning AC returned a positive gain with
   no `reason`. SE uses `_measure_cmrr`/`_measure_psrr`. FD needs device
   **mismatch**, because a perfectly matched FD circuit rejects common mode
   exactly. It re-emits the DUT eight times with seeded mismatch draws and keeps
   the worst. A tech without a `mismatch_switch` leaves FD CMRR/PSRR `None`
   and adds a note explaining why.
9. [`simulate.py:127-128`](../../../circuitgenome/sizer/verify/simulate.py#L127):
   notes ride along under a `"notes"` key. They are advice for humans, not a
   metric.

**Running example.** `circuit_0010` on gf180 (from the
[README table](README.md#running-example)): all nine keys come back measured,
in about 0.5 s for the whole set. Gain 81.2 dB, GBW 6.93 MHz, PM 63.5°, slew
8.9 V/µs, power 0.378 mW, swing 0.04–3.29 V, CMRR 78.2 dB, PSRR 86.8 dB. No
notes. With `corner="ss"` the same sized circuit reads GBW 6.52 MHz, PM 63.8°,
CMRR 76.4 dB, PSRR 83.2 dB: slow devices give a little less GBW, which is what
the CLI's corner table shows
([`cli.py:360-381`](../../../circuitgenome/cli.py#L360)).

**Test to read.**
- [`test_spice_sim.py::test_generic_level1_tracks_analytical`](../../../tests/test_spice_sim.py#L118):
  on the `generic` tech (Level-1 SPICE model, the same equations as the
  analytical sizer), SPICE gain lies within 20 dB and GBW within 3× of the
  prediction. It is a loose sanity envelope.
- [`test_spice_sim.py::test_cmrr_psrr_none_without_clean_gain`](../../../tests/test_spice_sim.py#L1547):
  a circuit that measures ≤ 0 dB gain must report CMRR/PSRR as `None`. Before
  this rule, a non-amplifying circuit once measured "CMRR 242 dB" from
  numerical noise.
- [`test_spice_sim.py::test_misbiased_circuit_reports_measured_gain_and_reason`](../../../tests/test_spice_sim.py#L166):
  a circuit that biases but does not amplify reports its measured (≤ 0 dB)
  gain, `None` GBW/PM, and notes containing "amplify" and "triode"/"starved".

**Check yourself.**
1. Why does the AC bench have to run before the slew and swing benches?
2. If the power bench raises a `ValueError`, what happens to the gain
   measurement?
3. How does `simulate_metrics` know a circuit is fully differential?

<details><summary>Answers</summary>

1. It detects the input polarity and returns it. Slew, swing, CMRR and PSRR
   take it as their first guess
   ([`simulate.py:71`](../../../circuitgenome/sizer/verify/simulate.py#L71),
   [`:85-91`](../../../circuitgenome/sizer/verify/simulate.py#L85)).
2. Nothing. The exception is caught, turned into the note
   "power bench failed: ValueError: …", `power_w` stays `None`, and the AC
   bench runs next.
3. From its port names: both `outp` and `outn` present
   ([`rig.py:25`](../../../circuitgenome/sizer/verify/rig.py#L25)).
</details>

---

## 6.2 `deck.py` (297 lines)

**Job in one sentence.** Everything that is not about any one metric: emit the
model block, parse the DUT `.subckt`, inject the sized values, rewrite devices
for a PDK, and run ngspice safely.

**Design knowledge it encodes.**
- [Technology / PDK](00_background.md#technology): three ways to supply device
  models (foundry `.lib` with corners, a flat PTM `.model` file, or a
  synthesised Level-1 card).
- [Fingers](00_background.md#technology): a device wider than the PDK allows
  becomes several parallel devices.
- Mismatch: a PDK switch plus a fixed random seed gives repeatable "random"
  device variation.

**Walkthrough.**

1. [`deck.py:40` `_emit_model`](../../../circuitgenome/sizer/verify/deck.py#L40):
   three branches.
   - `tech.spice_lib` (gf180, sky130) → `.include` the design file, then
     `.lib "<file>" <corner>` ([`:58-65`](../../../circuitgenome/sizer/verify/deck.py#L58)).
     With `mismatch_seed` it adds `.param sw_stat_mismatch=1` and
     `.option seed=N`, so each draw is random but repeatable.
   - `tech.spice_model` (ptm45) → a flat `.include` of the `.model` cards.
   - neither (`generic`) → a synthesised Shichman–Hodges Level-1 card from
     `mu_cox`, `vth`, `lam` ([`:68-75`](../../../circuitgenome/sizer/verify/deck.py#L68)).
     This is the [square law](00_background.md#square-law) the analytical
     sizer uses, so SPICE and the estimate share one device model.
2. [`deck.py:126` `_parse_subckt`](../../../circuitgenome/sizer/verify/deck.py#L126):
   find the single `.subckt … .ends` block and return `(name, ports, body_lines)`.
   This is a deliberately tiny parser; it is not the recognizer's.
3. [`deck.py:207` `_inject_sizes`](../../../circuitgenome/sizer/verify/deck.py#L207):
   the core rewrite. For each body line:
   - a MOSFET in `result.transistors` → append `W=…u L=…u`. With `fingers > 1`
     it writes that many lines (`ref`, `ref__f2`, …), each `w_um / fingers`
     wide. The first finger keeps the original name so op-point probes still
     find it ([`:218-224`](../../../circuitgenome/sizer/verify/deck.py#L218));
   - a sized resistor → its placeholder value replaced with ohms;
   - a sized capacitor → its placeholder replaced with pF;
   - anything else passes through unchanged.
4. [`deck.py:99` `_emit_body`](../../../circuitgenome/sizer/verify/deck.py#L99):
   for a PDK with `device_map`, foundry transistors are *subcircuits*, not
   primitive MOSFETs. So `m1_input_pair … pmos W=` becomes
   `x1_input_pair … pmos_3p3 w=` (the `m` prefix is swapped for `x` by
   [`_xref`](../../../circuitgenome/sizer/verify/deck.py#L78)). For sky130
   (`wl_units="um"`) the `u` suffix is dropped, because its library sets
   `.option scale=1.0u`.
5. [`deck.py:236` `_dut`](../../../circuitgenome/sizer/verify/deck.py#L236):
   title comment + model block + `.subckt name __PORTS__` + rewritten body +
   `.ends`. The first line must be a comment because SPICE always treats line 1
   as the title and ignores it. `__PORTS__` is a placeholder that
   `rig._deck` fills in later.
6. [`deck.py:83` `_dev_prefix`](../../../circuitgenome/sizer/verify/deck.py#L83):
   the ngspice "handle" used to read one device's operating point. It is
   `@m.xdut.m1_input_pair` for flat models, and one level deeper,
   `@m.xdut.x1_input_pair.m0`, inside a gf180 subcircuit (sky130 names the
   inner device per polarity via `device_handle`).
7. The runners:
   - [`deck.py:258` `_ngspice`](../../../circuitgenome/sizer/verify/deck.py#L258)
     writes `.spiceinit` with `set num_threads=1`. Otherwise the system spinit
     may start one OpenMP thread per core, and parallel benches oversubscribe
     the machine. It runs `ngspice -b` with a **CPU-time** limit of 60 s
     ([`_CPU_LIMIT_S`](../../../circuitgenome/sizer/verify/deck.py#L26)) and a
     600 s wall-clock backstop. It returns `None` on any exception or when the
     process was killed.
   - [`deck.py:272` `_run`](../../../circuitgenome/sizer/verify/deck.py#L272):
     replaces `__OUT__` with a temp file, runs, and loads the `wrdata` table
     with numpy. It returns **`None`** if ngspice failed, wrote no file, or wrote
     an unparsable one. This is the bottom of the `None` chain.
   - [`deck.py:291` `_run_capture`](../../../circuitgenome/sizer/verify/deck.py#L291):
     the same, but returns stdout for decks that `print` values.

**Public functions** (re-exported from `circuitgenome.sizer`):

| Function | Line | Used by | What it does |
|---|---|---|---|
| `ngspice_available()` | [35](../../../circuitgenome/sizer/verify/deck.py#L35) | CLI, designer, tests | `shutil.which("ngspice")` |
| `sized_netlist(text, result)` | [137](../../../circuitgenome/sizer/verify/deck.py#L137) | designer export ([`designer.py:177`](../../../circuitgenome/designer/designer.py#L177)) | the generic netlist with sizes injected; **raises** `ValueError` if any MOSFET is unsized |
| `pdk_netlist(sized, tech, base_dir)` | [163](../../../circuitgenome/sizer/verify/deck.py#L163) | designer export ([`designer.py:343`](../../../circuitgenome/designer/designer.py#L343)) | `.lib` header + `_emit_body` rewrite, ready to run against the PDK |
| `unsized_mos_refs(body, result)` | [200](../../../circuitgenome/sizer/verify/deck.py#L200) | `simulate`, `sized_netlist` | MOSFETs the result has no size for |

Note the asymmetry. `sized_netlist` (an export) **raises** on an unsized device.
`simulate_metrics` (a measurement) only adds a note and lets the simulator
default apply ([`simulate.py:58-60`](../../../circuitgenome/sizer/verify/simulate.py#L58)).

**Running example.** The `sized_netlist` output for `circuit_0010` (generic
form, what the designer exports as `*_sized.ckt`):

```
.subckt circuit_0010 ibias in1 in2 out vdd! gnd!
m1_input_pair net_diff1 in1 net_tail net_tail pmos W=3.07000u L=0.56000u
m2_input_pair net_mid in2 net_tail net_tail pmos W=3.07000u L=0.56000u
m1_load net_diff1 net_diff1 gnd! gnd! nmos W=2.49500u L=0.56000u
...
c1_compensation net_mid out 1.2500p
mn1_second_stage out net_mid gnd! gnd! nmos W=17.85500u L=0.56000u
mp1_second_stage out net_bias5 vdd! vdd! pmos W=52.60500u L=1.12000u
mp1_second_stage__f2 out net_bias5 vdd! vdd! pmos W=52.60500u L=1.12000u
.ends
```

The 105.21 µm PMOS load became two 52.605 µm fingers. And here is what ngspice
actually gets from `_dut` (gf180, `typical`):

```
* circuitgenome spice verification
.include ".../pdk/gf180/ngspice/design.ngspice"
.lib ".../pdk/gf180/ngspice/sm141064.ngspice" typical
.subckt circuit_0010 __PORTS__
x1_input_pair net_diff1 in1 net_tail net_tail pmos_3p3 w=3.07000u l=0.56000u
...
xp1_second_stage out net_bias5 vdd! vdd! pmos_3p3 w=52.60500u l=1.12000u
xp1_second_stage__f2 out net_bias5 vdd! vdd! pmos_3p3 w=52.60500u l=1.12000u
.ends
```

**Test to read.**
- [`test_spice_sim.py::test_emit_level1_for_generic`](../../../tests/test_spice_sim.py#L49)
  and [`::test_emit_include_for_ptm`](../../../tests/test_spice_sim.py#L54):
  the `generic` and PTM branches of `_emit_model`.
- [`test_gf180.py::test_gf180_device_translation_m_to_x`](../../../tests/test_gf180.py#L60),
  [`::test_gf180_op_handle_is_nested`](../../../tests/test_gf180.py#L71),
  [`::test_device_handle_and_um_units`](../../../tests/test_gf180.py#L77):
  the `m`→`x` rewrite, the nested `.m0` handle, and sky130's unitless microns.
- [`test_gf180.py::test_wide_device_is_emitted_as_parallel_fingers`](../../../tests/test_gf180.py#L153):
  a 200 µm, 2-finger device becomes two 100 µm lines, `ref` and `ref__f2`.
- [`test_gf180.py::test_gf180_pdk_netlist_keeps_si_units`](../../../tests/test_gf180.py#L100)
  and [`test_sky130.py::test_sky130_pdk_netlist_export`](../../../tests/test_sky130.py#L86):
  the PDK export header, units, `base_dir`-relative paths, and the
  `ValueError` for a non-PDK tech.
- [`test_spice_sim.py::test_ngspice_runs_single_threaded`](../../../tests/test_spice_sim.py#L1627)
  and [`::test_ngspice_cpu_limit_stops_a_runaway_simulation`](../../../tests/test_spice_sim.py#L1638):
  `num_threads=1` is in effect, and a 1 s CPU limit turns a runaway transient
  into `None` well within 30 s.

**Check yourself.**
1. A gf180 device was sized at 105.21 µm with `fingers=2`. How many lines does
   it become, and what is the op-point handle of the first one?
2. Why is the CPU limit on CPU time rather than wall-clock time?
3. What does `simulate_metrics` do with a MOSFET that the sizing result does not
   cover, and what does `sized_netlist` do?

<details><summary>Answers</summary>

1. Two lines, `xp1_second_stage` and `xp1_second_stage__f2`, each 52.605 µm.
   The handle is `@m.xdut.xp1_second_stage.m0`, which reads the first finger
   only. That is why `read_op_operating_point` multiplies `id` by `fingers`
   ([`op.py:41-43`](../../../circuitgenome/sizer/verify/op.py#L41)).
2. On a loaded machine (parallel designer workers), a healthy run slows down in
   wall-clock time but uses the same CPU. A wall-clock limit would turn that
   healthy run into a missing measurement
   ([`deck.py:22-26`](../../../circuitgenome/sizer/verify/deck.py#L22)).
3. `simulate_metrics` simulates it at the simulator's default W/L and adds a
   note. `sized_netlist` raises `ValueError`, because an exported design must
   be fully sized.
</details>

---

## 6.3 `rig.py` (108 lines)

**Job in one sentence.** Build the part every testbench shares: supply, bias
current, load caps and the `Xdut` line, wired for one chosen input polarity.

**Design knowledge it encodes.**
- [Bias generator](00_background.md#bias-generator): the op-amp's reference
  current enters through the `ibias` pin into a diode-connected transistor. The
  direction the external source must push depends on whether that diode is NMOS
  or PMOS.
- [Load capacitance](00_background.md#phase-margin): the output pole sits on
  `CL`. A bench without it measures a different amplifier (issue #222).

**Walkthrough.**

1. [`rig.py:22` `_Topo`](../../../circuitgenome/sizer/verify/rig.py#L22):
   SE vs FD from port names, the list of outputs, and whether a `vcm_ref` pin
   exists.
2. [`rig.py:79` `_fb_netmap`](../../../circuitgenome/sizer/verify/rig.py#L79):
   the **port map**, a dict from DUT port name to testbench node. `vdd!`→`vdd`,
   `gnd!`→`0` (SPICE ground), and the chosen polarity: `{inp: "inp", inn: "inn"}`.
   So "polarity `("in2","in1")`" means "wire DUT port `in2` to the bench's
   non-inverting node".
3. [`rig.py:36` `_iref_sink`](../../../circuitgenome/sizer/verify/rig.py#L36):
   looks for the reference diode (`drain == gate == ibias`) and checks its
   source.
   - Source at `gnd!` (NMOS reference): the diode **sinks** current, so the
     bench must **push** current into the pin.
   - Source at `vdd!` (PMOS reference): the diode **sources** current out of
     the pin, so the bench must **pull** it to ground. Pushing instead floats
     the pin above VDD and the whole bias generator dies.

   Analogy: a pump connected to a tank. Whether it should fill or drain depends
   on which way the tank's own outlet faces. No diode, or diodes on both rails,
   falls back to "push" (the historical behaviour).
4. [`rig.py:63` `_rig`](../../../circuitgenome/sizer/verify/rig.py#L63):
   two lines, `Vsup vdd 0 dc <vdd>` (with `ac 1` for the PSRR bench) and the
   `Iref` source in the chosen direction. In SPICE, `I n+ n-` drives current
   from `n+` through the source to `n-`. So `Iref 0 ibias` pushes current into
   `ibias`.
5. [`rig.py:91` `_deck`](../../../circuitgenome/sizer/verify/rig.py#L91): the
   assembler. It fills `__PORTS__`, adds `_rig`, adds a `Cload_<out> <out> 0 CL`
   on **every** output, appends the bench's own feedback/stimulus lines `fb`,
   the `Xdut` line, and the `.control` block.

**Running example.** `circuit_0010` has `mnref_bias_gen ibias ibias gnd! gnd! nmos`,
an NMOS reference, so `_iref_sink` is `False` and the rig injects:
`Iref 0 ibias dc 2e-05`. The whole rig portion of its AC deck is in 6.4.

**Test to read.**
- [`test_spice_sim.py::test_iref_direction_follows_reference_diode`](../../../tests/test_spice_sim.py#L61):
  NMOS reference → inject, PMOS reference (raw or PDK-rewritten line) → sink,
  no diode → inject.
- [`::test_deck_sinks_iref_for_pmos_referenced_dut`](../../../tests/test_spice_sim.py#L76):
  `_deck` picks the direction from the DUT block it was given.
- [`::test_deck_loads_every_output_with_cl`](../../../tests/test_spice_sim.py#L95):
  exactly one `CL` per output, both outputs for FD.

**Check yourself.**
1. In `_fb_netmap`, what does the bench node `0` stand for?
2. A DUT has `mp1_bias_gen ibias ibias vdd! vdd! pmos`. Which `Iref` line does
   the rig emit?

<details><summary>Answers</summary>

1. SPICE's global ground. The DUT's `gnd!` port is wired to it (and `vss` is
   assumed to be 0 V; see the simplification notes).
2. `Iref ibias 0 dc <ibias>`, which pulls the current out of the pin.
</details>

---

## 6.4 `measure.py` (785 lines)

**Job in one sentence.** One testbench per metric. Each builds its own deck
around the DUT, runs it, checks that the result is physically sensible, and
returns a number or `None`.

**Design knowledge it encodes.** Every metric in the
[spec sheet](00_background.md#spec-metrics), plus the measurement lore of how
each bench can lie: a wrong polarity, a second 0 dB crossing, a railed output,
a ringing edge.

The file is long but regular. Group it by metric:

| Metric(s) | Bench | Rig around the DUT | Analysis | What it reads |
|---|---|---|---|---|
| power | [`_measure_power`](../../../circuitgenome/sizer/verify/measure.py#L81) | SE: DC loop, both polarities | `op` | `vdd · abs(i(Vsup))` |
| gain, GBW, PM | [`_measure_ac`](../../../circuitgenome/sizer/verify/measure.py#L184) | DC loop ([`_loop_fb`](../../../circuitgenome/sizer/verify/measure.py#L157)) | `op` check, then `ac` | dB and unwrapped phase of `v(out)` |
| slew | [`_measure_sr`](../../../circuitgenome/sizer/verify/measure.py#L426) → [`_measure_sr_se`](../../../circuitgenome/sizer/verify/measure.py#L470) | unity buffer (`Rfb out inn 1`), pulse `Vcm ± 0.15·Vdd` | `tran`, 2000 steps | steepest edge ([`_edge_slew`](../../../circuitgenome/sizer/verify/measure.py#L331)) |
| swing | [`_measure_swing`](../../../circuitgenome/sizer/verify/measure.py#L505) | inverting gain −1 (two 10 MΩ) | `dc` sweep 0 → Vdd | ends of the region where the output still tracks |
| CMRR (SE) | [`_measure_cmrr`](../../../circuitgenome/sizer/verify/measure.py#L628) | DC loop, AC on `cm` | `ac` at 1 mHz | `Adm − Acm` (dB) |
| PSRR (SE) | [`_measure_psrr`](../../../circuitgenome/sizer/verify/measure.py#L649) | DC loop, AC on `Vsup` | `ac` at 1 mHz | `Adm − Avdd` (dB) |
| FD slew / swing | [`_measure_sr_fd`](../../../circuitgenome/sizer/verify/measure.py#L549), [`_measure_swing_fd`](../../../circuitgenome/sizer/verify/measure.py#L589) | gain −1 from B-sources ([`_fd_inverting_fb`](../../../circuitgenome/sizer/verify/measure.py#L306)) | `tran` / `dc` | per output |
| FD CMRR/PSRR | [`_measure_fd_rejection`](../../../circuitgenome/sizer/verify/measure.py#L749) | offset servo ([`_fd_servo_fb`](../../../circuitgenome/sizer/verify/measure.py#L697)), 8 mismatch seeds | `op` + three `ac` | worst draw |

### Gain, GBW and PM: `_measure_ac`

1. [`measure.py:216-231`](../../../circuitgenome/sizer/verify/measure.py#L216):
   for **each** polarity, run a DC deck first (`op`, print `v(out)`). Keep the
   polarity only if the output settled inside 10–90 % of the supply. A railed
   output means positive feedback.
2. [`measure.py:238-251`](../../../circuitgenome/sizer/verify/measure.py#L238):
   run `ac dec 30 1m 10G` and `wrdata` the real and imaginary parts. Magnitude
   and phase are computed in numpy. The sweep starts at **1 mHz**
   ([`AC_START_HZ`](../../../circuitgenome/sizer/verify/measure.py#L41)) so that
   even a sub-Hz dominant [pole](00_background.md#poles) is above the first
   point. The first point is then truly "DC gain".
3. [`measure.py:254-256`](../../../circuitgenome/sizer/verify/measure.py#L254):
   unwrap the phase and subtract its DC value. This removes the 0° vs 180°
   baseline that depends on which input was driven, so PM is
   `180° + (phase at crossing)`
   ([phase margin](00_background.md#phase-margin)).
4. [`measure.py:258-264`](../../../circuitgenome/sizer/verify/measure.py#L258):
   find **all** 0 dB crossings
   ([`_zero_db_crossings`](../../../circuitgenome/sizer/verify/measure.py#L140),
   log-interpolated). GBW is the **first** crossing. PM is the **worst** margin
   over all crossings. This is the issue #269 fix (PR #270): a gain that dips
   under 0 dB and comes back up crosses again, often with almost no phase left.
   Reading only the first crossing reported about 104° for designs that ring in
   closed loop.
5. [`measure.py:265-272`](../../../circuitgenome/sizer/verify/measure.py#L265):
   rank the two polarities by `(PM plausible at the first crossing, gain)`.
   [`_pm_plausible`](../../../circuitgenome/sizer/verify/measure.py#L122)
   accepts only `0° < PM ≤ 180°`. A PM above 180° means phase *lead* on a
   falling gain: a [right-half-plane](00_background.md#rhp-zero) response.
6. [`measure.py:273-303`](../../../circuitgenome/sizer/verify/measure.py#L273):
   turn the winner into `(gain, gbw, pm, reason, polarity)`.
   - Nothing settled → all `None`, reason "did not settle (output railed)".
   - Winner implausible with PM > 180° → all `None`, reason "right-half-plane
     response".
   - Winner implausible on FD → reported as measured. The FD bench has no loop
     that a wrong polarity could corrupt, so PM ≤ 0° there is a real
     instability (#236).
   - Winner implausible on SE → all `None`, reason "extraction artifact,
     discarded".
   - Gain ≤ 0 dB → the gain is reported, GBW/PM are `None`, reason "does not
     amplify".

### Power: `_measure_power` (issue #271, PR #275)

[`measure.py:104-119`](../../../circuitgenome/sizer/verify/measure.py#L104):
the same DC loop as the AC bench, at both polarities, keeping the one whose
output is **closest to Vcm**. The old bench left the loop open. The output then
railed and dragged internal nodes with it, turning a downstream stage into a
fully-on diode: the reading could be 40× the real quiescent power. Power must be read
at the operating point the amplifier actually runs at.

### Slew: `_measure_sr`

1. [`measure.py:426-467`](../../../circuitgenome/sizer/verify/measure.py#L426):
   a unity buffer with a step centred on Vcm. A step from Vcm upward would drag
   the input common mode a whole step away from where the circuit was sized.
   The bench runs **twice**. The first run uses a window sized from the spec
   target ([`_sr_window`](../../../circuitgenome/sizer/verify/measure.py#L419)).
   The second run re-fits the window to the first reading, so the edge gets
   about 20 samples instead of 2–3.
2. [`measure.py:491-501`](../../../circuitgenome/sizer/verify/measure.py#L491):
   reject a polarity if the output did not start near the low step, did not
   follow the pulse
   ([`_follows_pulse`](../../../circuitgenome/sizer/verify/measure.py#L386):
   a latched wrong-polarity buffer collapses to a rail, which would look like a
   fast edge), or is still oscillating at the end of a half
   ([`_oscillates`](../../../circuitgenome/sizer/verify/measure.py#L403)). The
   result is the **slower** of the rising and falling edges.
3. [`_edge_slew`](../../../circuitgenome/sizer/verify/measure.py#L331): the
   steepest secant across 20 % of the swing, inside 10–90 %. This reads the
   current-limited ramp `I/C` ([slew](00_background.md#slew)), not the
   feed-through jump at the start, nor the settling tail and overshoot at the
   end (issue #65 was a 20–80 % average that landed in the tail).

### Swing: `_measure_swing`

[`measure.py:524-546`](../../../circuitgenome/sizer/verify/measure.py#L524):
`Rfb out inn 10meg` and `Rin src inn 10meg` make an inverting amplifier with
gain −1, with `inp` held at Vcm. Sweeping `src` from 0 to Vdd ideally gives
`out = 2·Vcm − src`. The bench keeps the contiguous region around Vcm where the
slope is ≤ −0.7, and reports that region's two end values as
`(swing_max, swing_min)`. The input stays at Vcm, so this measures **output**
[swing](00_background.md#swing) only, not the input common-mode range (#126).
A unity buffer would mix the two.

### CMRR and PSRR (SE)

[`measure.py:628-666`](../../../circuitgenome/sizer/verify/measure.py#L628):
both reuse the DC loop and read one AC point at 1 mHz
([`_lf_mag`](../../../circuitgenome/sizer/verify/measure.py#L66)). For CMRR the
1 V AC rides on the `cm` node, so both inputs move together. For PSRR it rides
on `Vsup`. Each result is the gain from `_measure_ac` minus the measured
leak-through gain, in dB. A leak of `≤ 1e-12` is "numerical zero" → `None`.

### Fully differential, briefly

- **AC rig** ([`_loop_fb` FD branch, `:171-178`](../../../circuitgenome/sizer/verify/measure.py#L171)):
  the inputs are anchored at Vcm through 1 GΩ resistors, and the
  [CMFB](00_background.md#cmfb) owns the output common mode. There is no
  output-to-input tie (issue #165). The old cross ties formed a bistable
  network that ngspice resolved into an all-off state.
- **Slew/swing rig** ([`_fd_inverting_fb`](../../../circuitgenome/sizer/verify/measure.py#L306)):
  ideal behavioural sources (`B` lines) feed back only the *differential*
  output, so the input common mode stays pinned at Vcm (#208, #126).
- **CMRR/PSRR** ([`_measure_fd_rejection`](../../../circuitgenome/sizer/verify/measure.py#L749)):
  a perfectly matched FD circuit has infinite rejection, so the number only
  means something with mismatch. Eight seeded draws
  ([`_MISMATCH_SEEDS`](../../../circuitgenome/sizer/verify/measure.py#L671)).
  Each draw runs in a rig with a slow DC servo that nulls the mismatch offset,
  clamped to ±50 mV. One ngspice run per draw does `op` plus three `ac`
  points: differential, common-mode and supply stimulus
  ([`_FD_REJECTION_CONTROL`](../../../circuitgenome/sizer/verify/measure.py#L677)).
  A draw that does not settle is skipped and counted. The worst draw is
  reported.

**Running example.** The AC deck for `circuit_0010`, polarity `(in1, in2)`.
The DUT block is the `_dut` text from 6.2, followed by:

```
Vsup vdd 0 dc 3.3            ← rig: supply
Iref 0 ibias dc 2e-05        ← rig: push 20 µA into the NMOS reference
Cload_out out 0 5e-12        ← rig: spec CL on the output
Vcm cm 0 1.65                ┐
Lfb out inn 1e12             │ bench: DC-only feedback (_loop_fb)
Cfb inn cm 1e3               │
Vid inp cm ac 1              ┘ bench: 1 V AC stimulus
Xdut ibias inp inn out vdd 0 circuit_0010
.control
ac dec 30 0.001 1e10
let vod=v(out)
wrdata __OUT__ real(vod) imag(vod)
.endc
.end
```

What the two polarities give:

| Polarity (non-inv, inv) | DC `v(out)` | DC gain | DC phase | 0 dB crossing |
|---|---|---|---|---|
| (in1, in2) | 1.6506 V (settled) | 81.17 dB | −180° | 6.931 MHz, PM 63.48° |
| (in2, in1) | 1.6494 V (settled) | 81.17 dB | 0° | 6.931 MHz, PM 63.49° |

Both settle, even though one is positive feedback. ngspice's `.op` finds the
*unstable* balance point too, like a pencil balanced on its tip in a perfect
world. The magnitudes match because the loop is open at AC, and subtracting the
DC phase removes the difference. The rank tie is broken by gain differences in
the noise. `(in2, in1)` wins, and that is the right answer. Trace it through
the circuit: `in2` up → `m2_input_pair` (PMOS) conducts less → `net_mid` falls
→ `mn1_second_stage` (inverting CS) → `out` rises. So `in2` is non-inverting.
This is exactly why
[`_pols`](../../../circuitgenome/sizer/verify/measure.py#L52) calls the AC
polarity "only a hint". The slew and swing benches confirm it with their own
large-signal checks. Final result: gain 81.17 dB, GBW 6.93 MHz, PM 63.5°, with
`reason=None` in 0.22 s.

**Test to read.**
- [`test_spice_sim.py::test_ac_bench_reports_the_worst_zero_db_crossing`](../../../tests/test_spice_sim.py#L754):
  the frozen #418 RNMC design crosses 0 dB twice. GBW must be the first
  crossing (5–15 MHz) and PM the worst (< 30°).
- [`::test_se_power_is_measured_in_unity_feedback`](../../../tests/test_spice_sim.py#L774):
  the same design reads 0.7–1.5 mW, not the ~7.9 mW of the open-loop bench.
- [`::test_predicted_power_matches_the_supply_current`](../../../tests/test_spice_sim.py#L801):
  `_measure_power` agrees with the sizer's predicted power within 10 % (#285).
- [`::test_pm_plausible_range`](../../../tests/test_spice_sim.py#L1166),
  [`::test_implausible_pm_extraction_is_discarded`](../../../tests/test_spice_sim.py#L1233),
  [`::test_fd_instability_reported_not_discarded`](../../../tests/test_spice_sim.py#L1279):
  the plausibility rules. An SE PM above 180° is discarded with a
  "right-half-plane" note. An FD PM below 0° is kept with an "unstable" note.
- [`::test_slew_bench_rejects_an_output_that_does_not_follow_the_pulse`](../../../tests/test_spice_sim.py#L1649)
  and the `test_edge_slew_*` tests just below it: the slew readout on synthetic
  waveforms, no ngspice needed.
- [`::test_fd_cmrr_psrr_none_without_mismatch_model`](../../../tests/test_spice_sim.py#L968)
  and [`::test_fd_cmrr_psrr_from_mismatch_draws`](../../../tests/test_spice_sim.py#L1120):
  FD rejection is `None` on ptm45 (no mismatch model). On a PDK it is finite,
  10–150 dB, and identical run to run (the seeds are fixed).

**Check yourself.**
1. Why does the AC sweep start at 1 mHz rather than 1 Hz?
2. An open-loop response crosses 0 dB at 10 MHz with 104° and again at 27 MHz
   with 5°. What do GBW and PM read?
3. Why is the swing bench an inverting −1 amplifier and not a unity buffer?

<details><summary>Answers</summary>

1. High-gain designs have a dominant pole below 1 Hz. Starting above it, the
   first point is no longer DC: the gain reads low and the phase reference is
   off by tens of degrees, which skews PM
   ([`measure.py:234-237`](../../../circuitgenome/sizer/verify/measure.py#L234)).
2. GBW = 10 MHz (the first crossing). PM = 5° (the worst crossing, #269).
3. In a unity buffer the input follows the output, so the sweep would also hit
   the input stage's common-mode limit. That measures the overlap of output
   swing and input range, not output swing (#126). With gain −1 and `inp` at
   Vcm, the input stays put.
</details>

---

## 6.5 `op.py` (348 lines)

**Job in one sentence.** Read each transistor's real DC operating point from
ngspice and turn it into a yes/no "is the bias sound?" verdict.

**Design knowledge it encodes.**
- [Regions](00_background.md#regions): a device in triode (`|Vds| < |Vdsat|`)
  is not a current source any more, so the gain collapses.
- "Starved" (`|Id| < 0.1 µA`) means the device is effectively off.
- An FD circuit can be DC-correct but still oscillate. That is invisible to
  `.op`, so op.py kicks it and watches it settle.

**Walkthrough.**

1. [`op.py:47` `_read_op`](../../../circuitgenome/sizer/verify/op.py#L47)
   (SE): build the DUT, then for each polarity a deck with the same
   `Lfb`/`Cfb` loop written inline
   ([`:84-89`](../../../circuitgenome/sizer/verify/op.py#L84)). It prints
   `v(out)` plus `@m.xdut.<ref>[id]`, `[vds]`, `[vdsat]` for every device
   (handles from `_dev_prefix`). It keeps the polarity whose output is
   **closest to Vcm**, not just the first one inside the window. A latched
   output behind a source follower is shifted by one Vgs and can land inside
   the window (#242). It returns `(op, None)` or `(None, "railed" | "sim-failed")`.
2. [`op.py:126` `_read_op_fd`](../../../circuitgenome/sizer/verify/op.py#L126):
   FD has no loop. Inputs and `vcm_ref` are held at Vcm, and the CMFB owns the
   output. It is "railed" if an output leaves 10–90 % of the supply or the two
   outputs split by more than 20 % of the supply.
3. [`op.py:22` `read_op_operating_point`](../../../circuitgenome/sizer/verify/op.py#L22):
   the public wrapper. It drops the failure kind and multiplies `id` by
   `fingers`, because the probe sees only the first finger.
4. [`op.py:247` `_op_bias_problems`](../../../circuitgenome/sizer/verify/op.py#L247):
   two lists. Starved: `|id| < 1e-7`. Triode:
   `|vds| < |vdsat| − 1 mV`.
5. [`op.py:264` `check_bias_soundness`](../../../circuitgenome/sizer/verify/op.py#L264):
   the verdict `(sound, reason)`.
   - ngspice missing → `(True, None)`: it cannot check, so it does not condemn.
   - `.op` railed or failed → `(False, "SPICE bias check: …")`.
   - FD → no per-device check. Low-voltage CMFB tails run in marginal triode
     by design. Instead,
     [`_fd_ringing`](../../../circuitgenome/sizer/verify/op.py#L195) kicks
     `outp` with a current pulse and measures the residual peak-to-peak over
     the last quarter of the window. More than 1 % of Vdd in common mode or
     differential mode → "the outputs do not settle" (#208). A `None` from
     `_fd_ringing` is no evidence and passes.
   - SE → any starved or triode device → `(False, "SPICE bias check: in triode: …")`.
6. [`op.py:325` `_bias_diagnostic`](../../../circuitgenome/sizer/verify/op.py#L325):
   the same lists as a human hint. `simulate_metrics` adds it when the AC bench
   finds no usable gain.

**Who calls it.** Neither sizer does. The CLI calls `check_bias_soundness`
right after sizing and can only *downgrade* `result.bias_feasible`
([`cli.py:243-249`](../../../circuitgenome/cli.py#L243)). The designer
calls it before spending time on the metric benches
([`designer.py:139-143`](../../../circuitgenome/designer/designer.py#L139)).

**Running example.** `check_bias_soundness` on `circuit_0010` / gf180 returns
`(True, None)` in 0.08 s. The operating point behind it (from
`read_op_operating_point`; region = saturation when `|vds| > |vdsat|`):

| Device | Id (µA) | Vds (V) | Vdsat (V) | Vds − Vdsat (V) | Region |
|---|---|---|---|---|---|
| m1_input_pair | 10.29 | 1.893 | 0.285 | 1.608 | saturation |
| m2_input_pair | 10.27 | 1.930 | 0.285 | 1.645 | saturation |
| m1_load | 10.29 | 0.850 | 0.178 | 0.672 | saturation |
| m2_load | 10.27 | 0.813 | 0.178 | 0.635 | saturation |
| m1_tail_current | 20.77 | 0.950 | 0.159 | 0.791 | saturation |
| m2_tail_current | 20.57 | 0.557 | 0.159 | **0.397** | saturation (tightest) |
| mn1_second_stage | 52.52 | 1.649 | 0.149 | 1.501 | saturation |
| mp1_second_stage | 52.52 (2 fingers) | 1.651 | 0.159 | 1.491 | saturation |
| mnref_bias_gen | 20.00 | 0.835 | 0.174 | 0.662 | saturation |
| mn5_bias_gen | 20.76 | 2.350 | 0.174 | 2.176 | saturation |
| mp5_bias_gen | 20.77 | 0.950 | 0.159 | 0.791 | saturation |
| mn7_bias_gen | 20.76 | 2.350 | 0.174 | 2.176 | saturation |

Compare with the plan: the pair at 10 µA, the tail at 20 µA, the second stage
at 50 µA. SPICE agrees within about 5 %. The tail device has the least room
(0.40 V), as the [headroom](00_background.md#headroom) story predicts: it sits
between VDD and the input pair's sources.

**Test to read.**
- [`test_spice_sim.py::test_check_bias_soundness_distinguishes_biasing_from_railed`](../../../tests/test_spice_sim.py#L264):
  a healthy generic design → `(True, None)`. `circuit_0019` on ptm45 at 1.0 V
  → not sound, with "SPICE bias" in the reason.
- [`::test_bias_gate_rejects_latched_polarity_behind_follower`](../../../tests/test_spice_sim.py#L298):
  #242. With a follower output stage, the gate must keep the polarity that
  settles at Vcm and pass the design.
- [`::test_fd_cm_gate_condemns_high_gain_cmfb`](../../../tests/test_spice_sim.py#L427)
  and [`::test_fd_settling_gate_catches_local_loop_oscillation`](../../../tests/test_spice_sim.py#L461):
  the FD kick-and-settle gate catches CM and local-loop oscillation.
- Not what the name suggests: [`tests/test_dc_op.py`](../../../tests/test_dc_op.py)
  tests the **analytical** gm/Id headroom check (`bias_feasible` from the
  sizer, e.g. "cascode tail" warnings). It never runs ngspice.

**Check yourself.**
1. `check_bias_soundness` returns `(True, None)` on a machine without ngspice.
   Why is that safe for the CLI?
2. Why does the FD verdict skip the per-device triode check?

<details><summary>Answers</summary>

1. The CLI uses the verdict only to *downgrade* a feasible result
   ([`cli.py:247-249`](../../../circuitgenome/cli.py#L247)). A `True` leaves
   the analytical verdict standing. Also, for a foundry or PTM tech the CLI
   refuses to continue without ngspice
   ([`cli.py:317-321`](../../../circuitgenome/cli.py#L317)).
2. With inputs at Vcm, the CMFB amplifier's tail and the main tail run in
   marginal triode by design at low supplies, yet the circuit still amplifies.
   A device-level rule would reject the whole low-voltage CMFB family. A dead
   FD circuit rails or splits its outputs instead, and the `.op` verdict
   catches that.
</details>

---

## Exit check

**Why does a failed measurement return `None` instead of raising?**

<details><summary>Answer</summary>

Because the benches are **independent, best-effort measurements on an external
numerical solver**, and the callers need to tell "unmeasured" apart from
"failed".

1. **Failure is expected, and not a bug.** ngspice can fail to converge, hit
   the CPU limit, or settle into a railed state for numerical reasons. The
   bottom layer turns all of these into `None`:
   [`deck._ngspice`](../../../circuitgenome/sizer/verify/deck.py#L258) returns
   `None` on an exception or a killed process, and
   [`deck._run`](../../../circuitgenome/sizer/verify/deck.py#L272) returns
   `None` for a missing or unparsable table. Each bench then turns "no data" or
   "data that fails its sanity check" (railed, wrong polarity, implausible PM,
   oscillating edge) into `None` for its own metric. The package docstring says
   so: "best-effort verification, not sign-off"
   ([`__init__.py:11-13`](../../../circuitgenome/sizer/verify/__init__.py#L11)).
2. **One bench must not kill the others.** `simulate_metrics` pre-fills all
   nine keys with `None` and wraps each bench in its own `try/except`
   ([`simulate.py:51-126`](../../../circuitgenome/sizer/verify/simulate.py#L51)).
   If the slew transient stalls, you still get gain, GBW, PM, power and swing,
   plus a note saying which bench failed and why. Raising would throw away
   eight good numbers to report one bad one.
3. **Dependent metrics stay honest.** CMRR/PSRR are only computed from a clean
   gain (`ac_clean`, [`simulate.py:73`](../../../circuitgenome/sizer/verify/simulate.py#L73)),
   so a bad gain gives `None` there as well, never a fake ratio.
4. **Downstream, `None` means "no evidence", not "fails spec".**
   - CLI: prints `n/a  [ngspice could not extract this metric]` and does **not**
     count it toward MARGINAL
     ([`cli.py:333-336`](../../../circuitgenome/cli.py#L333)). The corner table
     prints `n/a` per cell ([`cli.py:378-380`](../../../circuitgenome/cli.py#L378)).
   - Designer: `_margins` skips `None` metrics
     ([`designer.py:85-98`](../../../circuitgenome/designer/designer.py#L85)).
     A real miss is `spec_failed`. A constrained spec that came back `None`
     puts the candidate in its own bucket, **`unverified`**
     ([`designer.py:164-174`](../../../circuitgenome/designer/designer.py#L164)),
     with the notes attached. It is neither accepted nor rejected.
     `_best_points` ignores `None` too
     ([`designer.py:230`](../../../circuitgenome/designer/designer.py#L230)).
     A truly unexpected exception, for example from the sizer, is still caught
     one level up and counted as `errors`
     ([`designer.py:178-180`](../../../circuitgenome/designer/designer.py#L178)).
   - Pinned by [`test_designer.py::test_margins_unmeasured_metric_is_skipped_not_failed`](../../../tests/test_designer.py#L37).

The one place that **does** raise is the export: `sized_netlist` raises on an
unsized MOSFET ([`deck.py:153-157`](../../../circuitgenome/sizer/verify/deck.py#L153)),
because a file you hand to someone must be complete.
</details>

---

## Simplification notes

| Location | Observation |
|---|---|
| `verify/simulate.py:5-17`, `verify/op.py:9-19`, `verify/measure.py:30-31` | README row #2 confirmed and wider than listed: `simulate` imports `deck._dut/_inject_sizes/_parse_subckt`, `measure._MISMATCH_SEEDS` and seven `_measure_*`, `op._bias_diagnostic`, `rig._Topo`. `op` imports `deck._MOS_MODELS/_dev_prefix/_dut/_inject_sizes/_parse_subckt/_run/_run_capture` and `rig._Topo/_iref_sink/_rig/_xline`. `measure` imports `deck._run/_run_capture` and `rig._POLARITIES/_deck/_fb_netmap`. Tests import many of these too. Inside the package, nothing is really private. |
| `verify/__init__.py:15-17` vs `physics/equations.py:18` | The docstring says "neither sizing pipeline imports it", but `physics/equations.py` imports `AC_LOOP_LIMIT_DB` and `AC_START_HZ` from `verify.measure`. That loads the whole `verify` package, and the dependency points from physics to verify. These two bench constants could live in `physics/` (or `models`) and be imported by `measure`. |
| `verify/__init__.py:6-9` | Says "for PTM nodes the BSIM4 `.pm` card is included (so the delta reflects the Level-1-vs-device gap)". gf180/sky130 use the `spice_lib` `.lib` path, and the LUT sizers are gm/Id, not Level-1. Stale since the PDK and gm/Id work. |
| `verify/measure.py:33-35` | Comment says L must be AC-open "even at the lowest sweep frequency (1 Hz): ωL ≈ 6e12 Ω". The sweep now starts at `AC_START_HZ` = 1 mHz, where ωL ≈ 6.3e9 Ω. The `AC_LOOP_LIMIT_DB` docstring just below has the current story. |
| `verify/op.py:84-89` vs `measure._loop_fb` / `rig._deck` | `_read_op` rebuilds the SE DC loop by hand (literal `1e12`/`1e3` instead of `_LH`/`_CH`) and assembles its deck without `rig._deck`, so it has no `CL` (harmless at DC, but a second copy). `_read_op_fd` and `_fd_ringing` also hand-assemble decks. |
| `verify/op.py:71-78` and `:146-153` | The model-map, `prefixes` and `probe` building is duplicated verbatim between `_read_op` and `_read_op_fd`. |
| `verify/op.py:312-321` and `:338-347` | The "starved … / in triode …" message formatting is duplicated between `check_bias_soundness` and `_bias_diagnostic`. |
| `verify/op.py:81-105` vs `measure.py:106-119` | "Try both polarities, keep the output closest to Vcm" is implemented twice: `op._read_op` and `measure._measure_power`. The AC bench has a third, different selection rule. |
| `verify/op.py:266-284` | The docstring says it "condemns the bias only on positive evidence" (railed or starved/triode), but a `.op` that fails or does not converge (`"sim-failed"`) also returns `False` (`:294-295`). Only "ngspice absent" is treated as no evidence. Either the docstring or the behaviour should change. |
| `verify/op.py:264` | `check_bias_soundness` has no `corner` parameter, while `simulate_metrics` does. The CLI corner sweep re-measures metrics per corner, but the DC verdict is always at the nominal corner. |
| `verify/rig.py:81`, `verify/op.py:82` | `gnd!` is hard-wired to SPICE node `0`, and `_rig` emits only `Vsup`. `spec.vss` is used only to compute Vcm, so a spec with `vss ≠ 0` would be simulated wrongly without any warning. |
| `verify/deck.py:248`, `verify/rig.py:105` | `_dut` writes a `__PORTS__` placeholder that every deck builder must `.replace()`. `_dut` already has `name` and the caller has `ports`, so it could write the real port list directly. |
| `verify/measure.py:216-272` | On `circuit_0010` both polarities settle at DC and give the same gain magnitude and PM. The rank is decided by noise in the gain's last digits. The DC phase (−180° vs 0°, which `_measure_ac` computes and then subtracts at `:255`) already says which input is non-inverting, and could be a direct polarity signal. Worth checking on the RHP and latched cases before changing anything. |
| `verify/measure.py:646`, `:666` | `_measure_cmrr`/`_measure_psrr` return `np.float64`, not `float` (visible as `np.float64(78.19…)` in the output dict). Other benches cast to `float`. |
| `tests/test_spice_sim.py:1` | The module docstring points at `circuitgenome/sizer/shared/spice`, a path from before the `verify/` rename. |
| `tests/test_dc_op.py` | Its name suggests the SPICE `.op` reader (`verify/op.py`), but it tests the analytical gm/Id headroom check. The SPICE DC tests live in `test_spice_sim.py`. |
| `cli.py:272-273` | Comment says "For PTM the numbers come from ngspice". It is any tech with `spice_model` or `spice_lib` (gf180, sky130 too), as the code at `:311` says. |
