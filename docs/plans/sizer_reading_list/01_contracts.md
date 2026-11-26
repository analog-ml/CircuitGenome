# Stop 1 — Contracts: what goes in, what comes out

**Files**:
[`sizer/models.py`](../../../circuitgenome/sizer/models.py) (303 lines) ·
[`sizer/loader.py`](../../../circuitgenome/sizer/loader.py) (155 lines) ·
[`config/tech_gf180mcu.yaml`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml) (63 lines) ·
[`examples/two_stage_se_specs/spec_gf180.yaml`](../../../examples/two_stage_se_specs/spec_gf180.yaml) (13 lines) ·
[`tests/test_loader.py`](../../../tests/test_loader.py) (95 lines) ·
walkthrough pages [`models_explained.html`](../../_extra/walkthrough/models_explained.html) (610 lines),
[`loader_explained.html`](../../_extra/walkthrough/loader_explained.html) (491 lines)
**Time**: 1 h
**After this stop you can**:
- name every field of the three records (`TechParams`, `SizingSpec`, `SizingResult`) and the circuit idea behind it
- say what `load_tech` checks and what it leaves alone
- say which sizer phase fills each `SizingResult` field

## Background for this stop

- The spec is the [eight-metric spec sheet](00_background.md#spec-metrics):
  [gain](00_background.md#opamp), [GBW](00_background.md#gbw),
  [phase margin](00_background.md#phase-margin), [slew](00_background.md#slew),
  [power](00_background.md#power), [swing](00_background.md#swing),
  [CMRR](00_background.md#cmrr), [PSRR](00_background.md#psrr). It also holds
  the operating conditions: [rails](00_background.md#rails), the reference
  current (`ibias`, see [KCL](00_background.md#kcl)), the load
  [capacitor](00_background.md#capacitor), and the per-[stage](00_background.md#stages)
  current ratios.
- The tech file is the [technology / PDK](00_background.md#technology)
  description: [square-law](00_background.md#square-law) numbers (`generic`
  only), a [gm/Id LUT](00_background.md#gmid) (real techs),
  [body effect](00_background.md#vth-body) and
  [parasitic-cap](00_background.md#parasitic-caps) constants, and the
  manufacturing grid.
- The result is what [sizing](00_background.md#sizing-problem) produces:
  W/L per transistor, R, C, predicted metrics, and a
  [DC verdict](00_background.md#small-signal).

**Your notes from #250, checked against `8571bf9`:**

| Your note | Status on main |
|---|---|
| `SizingSpec` consists of 8 metrics: gain, GBW, PM, slew, power, swing, CMRR, PSRR | Right. Swing uses two fields (`output_swing_min_v`/`_max_v`), so there are 9 target fields. There are also 6 operating-condition fields that are *not* targets: `vdd`, `vss`, `ibias`, `cl` and the two current ratios (table in 1.1) |
| Simplify `SizingResult`: group `c1_pf`/`c2_pf` into `capacitors`, remove `transistor_intents` | **Done and on main.** PR #251 (commit `e8feaed`, merged 2026-10-19 as `4637868`). The old fields were called `cc_pf`/`cc2_pf`. The same PR renamed `open_loop_measurable` → `open_loop_gain_measurable` and made `resistors`/`capacitors` required. The intents still exist, but only inside the gm/Id plan (`SizingPlan.tintents`, Stop 4) |
| TODO: simplify the docstrings of `MosfetParams` and `TechParams` | **Still open.** The specific stale parts are listed in the Simplification notes at the end |
| `load_tech` builds `TechParams` from a PDK path | Close. It reads a **tech YAML** (a path, or a short name such as `"gf180mcu"`). The YAML *points at* the PDK and LUT files; the loader only turns those pointers into absolute paths and never opens them (1.2) |
| Exit check answer: "`size_circuit` takes care of it" | True but coarse. The per-field answer is in the [Exit check](#exit-check) |

---

## 1.1 `circuitgenome/sizer/models.py` (303 lines)

**Job in one sentence.** Plain dataclasses with no logic (two tiny helpers
aside). They are the typed hand-off points between the loader, the sizers, the
verifier and the CLI.

**Design knowledge it encodes.**
- [Technology, PDK, grid, fingers](00_background.md#technology): `TechParams`,
  `MosfetParams`, `GridSpec`, `SpiceLib`.
- [The spec sheet](00_background.md#spec-metrics): `SizingSpec`.
- [What sizing produces](00_background.md#sizing-problem): `TransistorSizing`,
  `SizingResult`.
- [Why the square law is refused on real devices](00_background.md#square-law):
  `UnsupportedTechError`.

**Walkthrough.** Read smallest record first, as the walkthrough page does.

1. **`MosfetParams`** ([`models.py:21`](../../../circuitgenome/sizer/models.py#L21)),
   one per polarity ([NMOS/PMOS](00_background.md#nmos-pmos)):

   | Field | Line | Meaning | Background | Read by |
   |---|---|---|---|---|
   | `vth` | [`:52`](../../../circuitgenome/sizer/models.py#L52) | threshold voltage (sign = polarity) | [threshold](00_background.md#vth-body) | Level-1 only (`generic`) |
   | `mu_cox` | [`:53`](../../../circuitgenome/sizer/models.py#L53) | µ·Cox, square-law strength | [square law](00_background.md#square-law) | Level-1 only |
   | `lam` | [`:54`](../../../circuitgenome/sizer/models.py#L54) | λ, so `gds = λ·Id` | [output resistance](00_background.md#rout) | Level-1 only |
   | `gamma` | [`:55`](../../../circuitgenome/sizer/models.py#L55) | body-effect γ (default 0) | [body effect](00_background.md#vth-body) | both models' `body_vth_shift` |
   | `phi` | [`:56`](../../../circuitgenome/sizer/models.py#L56) | 2φF (default 0.7 V) | [body effect](00_background.md#vth-body) | same |
   | `cox` | [`:57`](../../../circuitgenome/sizer/models.py#L57) | oxide capacitance per µm², for `Cgs = ⅔·W·L·Cox` | [parasitic caps](00_background.md#parasitic-caps) | both models' `cgs` |
   | `cdb` | [`:58`](../../../circuitgenome/sizer/models.py#L58) | drain-bulk cap per µm of width | [parasitic caps](00_background.md#parasitic-caps) | the RNMC model only (#266) |

   The first three are optional (`None`) since issue #158. A LUT tech gets every
   voltage from the table, so it carries none of them. The docstring is long
   because it explains this per field; your TODO is to shorten it.

2. **`GridSpec`** ([`models.py:62`](../../../circuitgenome/sizer/models.py#L62)):
   `min`, `max`, `step`. It is the [manufacturing grid](00_background.md#technology).
   Two methods:
   - `snap` ([`:74`](../../../circuitgenome/sizer/models.py#L74)) rounds to the
     step and clamps to `[min, max]`.
   - `snap_total` ([`:79`](../../../circuitgenome/sizer/models.py#L79)) rounds
     and floors but does **not** cap. A width past `max` is legal as a *total*;
     the netlist builds it from parallel [fingers](00_background.md#technology).

   Analogy: a ruler that only has marks every 5 nm. `snap` is "nearest mark,
   but never off the ruler". `snap_total` is "nearest mark; if it is longer than
   one ruler, use several rulers end to end".

3. **`SpiceLib`** ([`models.py:88`](../../../circuitgenome/sizer/models.py#L88)):
   where a foundry PDK lives and which [corner](00_background.md#technology) to
   use. `file` and `design` are library paths; `corner` is the nominal corner
   (sizing and the main measurement); `corners` is the CLI's sweep list;
   `mismatch_switch` is the library parameter that turns on per-device
   [mismatch](00_background.md#technology), needed for fully differential
   CMRR/PSRR ([FD](00_background.md#fully-differential)). Only `verify/` reads
   it. The sizers never do.

4. **`TechParams`** ([`models.py:115`](../../../circuitgenome/sizer/models.py#L115)),
   the whole technology:

   | Field | Line | Meaning | Used by |
   |---|---|---|---|
   | `name` | [`:147`](../../../circuitgenome/sizer/models.py#L147) | the YAML `name:` (e.g. `gf180mcu_3v3`, not `gf180mcu`) | CLI print; `intent_for_tech` key (Stop 2) |
   | `nmos`, `pmos` | [`:148`](../../../circuitgenome/sizer/models.py#L148) | the two `MosfetParams` | device models |
   | `width`, `length` | [`:150`](../../../circuitgenome/sizer/models.py#L150) | W and L grids, µm | geometry (Stops 4–5) |
   | `cap` | [`:152`](../../../circuitgenome/sizer/models.py#L152) | compensation-cap grid, **pF** | `compute_requirements` bounds Cc ([`preprocess.py:411`](../../../circuitgenome/sizer/physics/preprocess.py#L411)) |
   | `spice_model` | [`:153`](../../../circuitgenome/sizer/models.py#L153) | flat `.model` card (PTM) | dispatcher refusal (Stop 2); `verify/deck.py` |
   | `gmid_lut` | [`:154`](../../../circuitgenome/sizer/models.py#L154) | the `.npz` [LUT](00_background.md#gmid) | **dispatcher**: present → gm/Id path |
   | `spice_lib` | [`:155`](../../../circuitgenome/sizer/models.py#L155) | a `SpiceLib` (foundry PDK) | `verify/`, CLI corner sweep |
   | `device_map` | [`:156`](../../../circuitgenome/sizer/models.py#L156) | `"nmos"` → PDK subcircuit name | `verify/deck.py` (emit `X` instead of `M`) |
   | `device_handle` | [`:157`](../../../circuitgenome/sizer/models.py#L157) | name of the BSIM4 instance inside that subcircuit | `verify/` operating-point reads (sky130 only) |
   | `wl_units` | [`:158`](../../../circuitgenome/sizer/models.py#L158) | `"m"` (GF180) or `"um"` (sky130) | `verify/deck.py` |

   Six required fields describe every tech. The rest are optional, and three of
   them (`gmid_lut`, `spice_model`, `spice_lib`) decide *how* the tech is sized
   and verified. The last three are workarounds for foundry naming quirks.

5. **`SizingSpec`** ([`models.py:162`](../../../circuitgenome/sizer/models.py#L162)),
   the request. Every target is optional; `None` means "don't care".

   | Field | Line | Kind | Metric key it constrains | Background | `spec_gf180.yaml` |
   |---|---|---|---|---|---|
   | `vdd`, `vss` | [`:183`](../../../circuitgenome/sizer/models.py#L183) | condition | — | [rails](00_background.md#rails), [headroom](00_background.md#headroom) | 3.3, 0.0 |
   | `ibias` | [`:185`](../../../circuitgenome/sizer/models.py#L185) | condition | — | [KCL](00_background.md#kcl), [bias generator](00_background.md#bias-generator) | 20 µA |
   | `cl` | [`:186`](../../../circuitgenome/sizer/models.py#L186) | condition | — | [capacitor](00_background.md#capacitor), [slew](00_background.md#slew), [PM](00_background.md#phase-margin) | 5 pF |
   | `second_stage_current_ratio` | [`:187`](../../../circuitgenome/sizer/models.py#L187) | condition | — | [stages](00_background.md#stages) | 2.5 |
   | `third_stage_current_ratio` | [`:188`](../../../circuitgenome/sizer/models.py#L188) | condition | — | [stages](00_background.md#stages), [RNMC](00_background.md#nested-miller) | 5.0 (default) |
   | `gain_min_db` | [`:189`](../../../circuitgenome/sizer/models.py#L189) | min | `gain_db` | [op-amp](00_background.md#opamp), [common source](00_background.md#common-source) | 60 |
   | `gbw_min_hz` | [`:190`](../../../circuitgenome/sizer/models.py#L190) | min | `gbw_hz` | [GBW](00_background.md#gbw) | 2 MHz |
   | `phase_margin_min_deg` | [`:191`](../../../circuitgenome/sizer/models.py#L191) | min | `phase_margin_deg` | [phase margin](00_background.md#phase-margin) | 60° |
   | `slew_rate_min_vps` | [`:192`](../../../circuitgenome/sizer/models.py#L192) | min | `slew_rate_vps` | [slew](00_background.md#slew) | 0.3 V/µs |
   | `power_max_w` | [`:193`](../../../circuitgenome/sizer/models.py#L193) | max | `power_w` | [power](00_background.md#power) | 2 mW |
   | `output_swing_max_v` | [`:194`](../../../circuitgenome/sizer/models.py#L194) | min (must reach at least this high) | `output_swing_max_v` | [swing](00_background.md#swing) | 3.0 V |
   | `output_swing_min_v` | [`:195`](../../../circuitgenome/sizer/models.py#L195) | max (must reach at least this low) | `output_swing_min_v` | [swing](00_background.md#swing) | 0.3 V |
   | `cmrr_min_db` | [`:196`](../../../circuitgenome/sizer/models.py#L196) | min | `cmrr_db` | [CMRR](00_background.md#cmrr) | — |
   | `psrr_min_db` | [`:197`](../../../circuitgenome/sizer/models.py#L197) | min | `psrr_db` | [PSRR](00_background.md#psrr) | — |

   Two helpers sit on the spec:
   - `vcm` ([`:200`](../../../circuitgenome/sizer/models.py#L200)): the input
     common mode, assumed to be mid-supply ([diff pair](00_background.md#diff-pair)).
     1.65 V on gf180.
   - `rail_v(net)` ([`:204`](../../../circuitgenome/sizer/models.py#L204)):
     `"vdd!"` → `vdd`, anything else → `vss`. Used by the circuit walkers in
     Stop 3 to turn a rail name into a voltage.

   The "min"/"max" kind decides the sign of the margin
   ([`metrics.py:216`](../../../circuitgenome/sizer/physics/metrics.py#L216)):
   `predicted − limit` for a minimum, `limit − predicted` for a maximum. So a
   margin ≥ 0 always means "met".

6. **`TransistorSizing`** ([`models.py:215`](../../../circuitgenome/sizer/models.py#L215)):
   one device's answer. `w_um` (total width), `l_um`, `ids_a` (bias current),
   `vgs_v`, `vds_sat_v` ([Vdsat](00_background.md#vdsat)), and `fingers`.
   `w_um` is always the *total*. `fingers > 1` only when the total is wider than
   `tech.width.max`.

7. **`SizingResult`** ([`models.py:238`](../../../circuitgenome/sizer/models.py#L238)),
   in four commented groups:
   - sized parts ([`:292`](../../../circuitgenome/sizer/models.py#L292)):
     `transistors`, `resistors` (Ω), `capacitors` (F). All keyed by netlist ref.
   - predicted performance ([`:296`](../../../circuitgenome/sizer/models.py#L296)):
     `metrics`, `margins`, `solver_status`. "Predicted" means "from the device
     model, never from SPICE". A metric the model cannot support is left out,
     not set to 0.
   - advisory flags ([`:300`](../../../circuitgenome/sizer/models.py#L300)):
     `bias_feasible` (the [DC verdict](00_background.md#small-signal)) and
     `open_loop_gain_measurable` (is the predicted gain so large that the
     open-loop SPICE bench cannot read it?). Both are hints. Metrics are still
     reported when they are `False`.
   - `warnings` ([`:303`](../../../circuitgenome/sizer/models.py#L303)): plain
     text.

8. **`UnsupportedTechError`** ([`models.py:11`](../../../circuitgenome/sizer/models.py#L11)):
   a `ValueError` subclass, raised by the dispatcher (Stop 2).

**Running example.** `load_tech("gf180mcu")` gives (abbreviated):

```
TechParams(name='gf180mcu_3v3',
  nmos=MosfetParams(vth=None, mu_cox=None, lam=None, gamma=0.3101, phi=0.365, cox=4.316e-15, cdb=4.13e-16),
  pmos=MosfetParams(vth=None, mu_cox=None, lam=None, gamma=0.522,  phi=0.49,  cox=4.371e-15, cdb=3.64e-16),
  width=GridSpec(min=0.22, max=100.0, step=0.005), length=GridSpec(min=0.28, max=4.0, step=0.02),
  cap=GridSpec(min=0.01, max=20.0, step=0.01),
  spice_model=None, gmid_lut='/…/config/models/gf180mcu_gmid.npz',
  spice_lib=SpiceLib(file='/…/config/../pdk/gf180/ngspice/sm141064.ngspice', corner='typical',
                     design='/…/design.ngspice', corners=['typical','ss','ff','sf','fs'],
                     mismatch_switch='sw_stat_mismatch'),
  device_map={'nmos': 'nmos_3p3', 'pmos': 'pmos_3p3'}, device_handle=None, wl_units='m')
```

The grid shows up directly in `circuit_0010`'s sizes. Every L is a multiple of
`length.min = 0.28 µm`: signal devices get 2 × 0.28 = 0.56 µm, and current
sources get 4 × 0.28 = 1.12 µm (the L policy in
[`device_model.py:266`](../../../circuitgenome/sizer/physics/device_model.py#L266),
with the multiples from `gmid/intent.py`, Stop 4). `mp1_second_stage` comes out
at 105.21 µm. That is past `width.max = 100`, so it was snapped with
`snap_total` and gets `fingers=2`.

The `SizingResult` for `circuit_0010` on gf180 (2026-10-31, `8571bf9`):

| Field | Value |
|---|---|
| `transistors` | 12 entries, e.g. `m1_input_pair`: W=3.07, L=0.56, Id=10 µA, Vgs=−1.083 V, Vdsat=0.274 V |
| `resistors` | `{}` (mirror load, no resistors) |
| `capacitors` | `{'c1_compensation': 1.25e-12}` |
| `metrics` | 9 keys: gain 81.5 dB, GBW 7.06 MHz, PM 63.2°, slew 8.0 V/µs, power 0.363 mW, swing 0.14–3.15 V, CMRR 80.1 dB, PSRR 90.5 dB |
| `margins` | 7 keys: gain +21.5 dB, GBW +5.06 MHz, PM +3.2°, slew +7.7 V/µs, power +1.64 mW, swing max +0.146 V, swing min +0.155 V. No CMRR/PSRR entry because the spec leaves them `None` |
| `solver_status` | `"GMID"` |
| `bias_feasible` | `True` |
| `open_loop_gain_measurable` | `True` |
| `warnings` | `[]` |

For contrast, `circuit_0001` (resistor load) gets
`resistors={'r1_load': 76039, 'r2_load': 76039}` and two warnings: the
second-stage gm ceiling, and "rail-referenced resistor load is corner-fragile".

**Test to read.**
- [`test_gf180.py::test_width_grid_total_snap_has_no_upper_bound`](../../../tests/test_gf180.py#L144):
  `snap_total(3·max)` stays `3·max`, while `snap` clamps to `max`.
- [`test_gf180.py::test_gf180_sizing_marks_devices_wider_than_one_pdk_device`](../../../tests/test_gf180.py#L168):
  every sized device uses the fewest fingers that each fit under `width.max`.
- [`test_sizer.py::test_compensation_caps_keyed_by_device_ref_in_farads`](../../../tests/test_sizer.py#L209):
  `capacitors` is keyed by device ref, in farads; `comp2*` slots get Cc2.
- [`test_sizer.py::test_impossible_gain_flagged_not_infeasible`](../../../tests/test_sizer.py#L615):
  an impossible gain gives a negative `margins["gain_db"]` and a warning, not
  an empty result.

**Check yourself.**
1. A spec sets `output_swing_min_v: 0.3` and the sizer predicts 0.14 V. Is the
   margin +0.16 or −0.16?
2. gf180's YAML has no `vth`. Which `SizingResult` field still contains a
   gate-source voltage, and where did it come from?
3. Why does `SizingResult.metrics` sometimes lack a key instead of holding 0?

<details><summary>Answers</summary>

1. +0.16 (+0.155 exactly). `output_swing_min_v` is a maximum-style limit ("go
   at least this low"), so the margin is `limit − predicted`. Positive means
   met.
2. `TransistorSizing.vgs_v`. On the gm/Id path it is read from the LUT at the
   device's gm/Id and L (Stop 3, `GmIdModel`). `vth` is not needed.
3. So a caller can tell "fails the spec" (a bad value) from "not computed"
   (missing). Example: a single-stage resistor-load amplifier has no
   `phase_margin_deg` ([`sizer.rst:120`](../../modules/sizer.rst?plain=1#L120)).
</details>

---

## 1.2 `circuitgenome/sizer/loader.py` (155 lines)

**Job in one sentence.** Turn two YAML files into a `TechParams` and a
`SizingSpec`, strictly for the tech and loosely for the spec.

**Design knowledge it encodes.**
- The #158 rule: "a LUT tech needs no square-law numbers, a square-law tech
  needs all of them" ([square law](00_background.md#square-law) vs
  [gm/Id](00_background.md#gmid)).
- [NMOS/PMOS sign convention](00_background.md#nmos-pmos): NMOS `vth > 0`,
  PMOS `vth < 0`.

**Walkthrough.**

1. **`load_spec(path)`** ([`loader.py:12`](../../../circuitgenome/sizer/loader.py#L12)).
   Reads the YAML and keeps only keys that are `SizingSpec` fields
   ([`:20`](../../../circuitgenome/sizer/loader.py#L20)). Extra keys such as
   comments or provenance are dropped. A missing `vdd` raises `TypeError` from
   the dataclass. The catch: a *typo* such as `gain_mindb` is also silently
   dropped, so that target becomes "don't care".
2. **`load_tech(path)`** ([`loader.py:60`](../../../circuitgenome/sizer/loader.py#L60)),
   step by step:
   - **Find the file** ([`:72`](../../../circuitgenome/sizer/loader.py#L72)).
     `None` → `tech_generic.yaml`. A path that exists → that file. Anything else
     → try `config/tech_<name>.yaml`. That is why `--tech gf180mcu` works.
   - **Three converters**: `_mosfet` ([`:82`](../../../circuitgenome/sizer/loader.py#L82))
     builds `MosfetParams`, with `None` for any absent optional key. `_grid`
     ([`:93`](../../../circuitgenome/sizer/loader.py#L93)) builds `GridSpec`.
     `_resolve` ([`:102`](../../../circuitgenome/sizer/loader.py#L102)) makes a
     relative path absolute *relative to the YAML file*, not to your shell.
     The comment at [`:110`](../../../circuitgenome/sizer/loader.py#L110) says
     why: ngspice runs in its own temporary directory.
   - **Cap grid rename** ([`:144`](../../../circuitgenome/sizer/loader.py#L144)):
     the YAML says `min_pf`/`max_pf`/`step_pf`; `GridSpec` says
     `min`/`max`/`step`. The unit (pF) is in the YAML key name only.
   - **Optional blocks** ([`:115`](../../../circuitgenome/sizer/loader.py#L115)
     to `:130`): `spice_lib`, `device_map`, `device_handle` are filled only when
     present.
   - **Validate** ([`:134`](../../../circuitgenome/sizer/loader.py#L134) to
     `:136`): `nmos:`/`pmos:` may be missing entirely. Then
     `_validate_mosfets(..., is_level1=gmid_lut is None)`.
3. **`_validate_mosfets`** ([`loader.py:24`](../../../circuitgenome/sizer/loader.py#L24)).
   This is the one real rule in the file:
   - **LUT tech**: only checks the sign of `vth` *if* one is given
     ([`:34`](../../../circuitgenome/sizer/loader.py#L34)).
   - **No LUT** ("Level-1 tech"): `vth`, `mu_cox` and `lam` must all be present,
     with the right signs ([`:41`](../../../circuitgenome/sizer/loader.py#L41)).

   Analogy: a passport desk. Travellers with a LUT "visa" just get their name
   spelling checked. Everyone else must show all three documents.

   Note what "Level-1 tech" means here: *no LUT*. It does not look at
   `spice_model`. So a ptm45 copy without a LUT passes the loader (its YAML has
   all three numbers) and is only refused later by the dispatcher (Stop 2).

**What `load_tech` does *not* do.** It never opens the LUT or the PDK
library. It only stores their absolute paths. The LUT is opened by the gm/Id
plan ([`gmid/plan.py:105`](../../../circuitgenome/sizer/gmid/plan.py#L105),
Stop 4). The PDK files are read only by ngspice (Stop 6). A wrong path is
discovered there, not at load time.

**Running example.** `load_tech("gf180mcu")`:

1. `Path("gf180mcu")` does not exist, so it resolves to
   `config/tech_gf180mcu.yaml`.
2. `gmid_lut: models/gf180mcu_gmid.npz`
   ([`tech_gf180mcu.yaml:23`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L23))
   becomes an absolute path. `spice_lib.file` and `.design` (`../pdk/gf180/…`,
   [`:12`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L12)) too.
3. `nmos:` has only `gamma`, `phi`, `cox`, `cdb`
   ([`:39`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L39)), so
   `vth`, `mu_cox`, `lam` are `None`. The long YAML comment at
   [`:25`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L25) to `:38`
   says where `cox` and `cdb` were measured.
4. `_validate_mosfets(is_level1=False)`: no `vth` given, so nothing to check.

`load_spec("examples/two_stage_se_specs/spec_gf180.yaml")` turns all 12 keys of
the file into fields. `third_stage_current_ratio` keeps its default 5.0 (unused,
this is a two-stage circuit); `cmrr_min_db`/`psrr_min_db` stay `None`.

**Test to read.** [`tests/test_loader.py`](../../../tests/test_loader.py), all
nine tests (they run in under 2 s):
- [`test_builtin_techs_load`](../../../tests/test_loader.py#L24): all four
  bundled techs load.
- [`test_lut_tech_omits_level1_params`](../../../tests/test_loader.py#L30):
  gf180mcu and sky130 have a LUT and *no* `vth`/`mu_cox`/`lam`.
- [`test_level1_tech_keeps_mu_cox_and_lam`](../../../tests/test_loader.py#L44):
  `generic` has no LUT and does have them.
- [`test_gmid_tech_may_omit_all_level1_params`](../../../tests/test_loader.py#L52)
  and [`…_mosfet_blocks_entirely`](../../../tests/test_loader.py#L60): a LUT
  tech may have near-empty or missing `nmos:`/`pmos:` blocks.
- [`test_level1_tech_requires_vth`](../../../tests/test_loader.py#L70),
  [`…_mu_cox`](../../../tests/test_loader.py#L77),
  [`…_positive_lam`](../../../tests/test_loader.py#L84): without a LUT each
  missing or bad number is a `ValueError`.
- [`test_vth_sign_validated_for_any_tech`](../../../tests/test_loader.py#L91):
  a negative NMOS `vth` is rejected.
- Also [`test_sizer.py::test_load_tech_resolves_relative_tech_path_to_absolute_models`](../../../tests/test_sizer.py#L88):
  a tech file given by a relative path still yields absolute model paths.

**Check yourself.**
1. You copy `tech_gf180mcu.yaml` and delete the `gmid_lut:` line. What
   happens, and where?
2. You write `--tech ./my_tech.yaml` from some other directory, and the YAML
   says `gmid_lut: models/x.npz`. Which `models/` folder is used?

<details><summary>Answers</summary>

1. `load_tech` raises `ValueError: nmos.vth must be a positive number for a
   Level-1 tech (no gmid_lut), got None`
   ([`loader.py:42`](../../../circuitgenome/sizer/loader.py#L42)). Without a
   LUT the tech counts as Level-1, and gf180's YAML has no square-law numbers.
2. The one next to `my_tech.yaml`. `_resolve` joins relative paths to the YAML
   file's own folder ([`loader.py:107`](../../../circuitgenome/sizer/loader.py#L107)).
</details>

---

## 1.3 The two YAML files

**[`config/tech_gf180mcu.yaml`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml)**:
read the comments. They are the best explanation of the fields:

| YAML key | Line | → `TechParams` | Note |
|---|---|---|---|
| `name` | [`:9`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L9) | `name` | `gf180mcu_3v3` |
| `spice_lib` | [`:12`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L12) | `spice_lib` | 5 corners, mismatch switch |
| `device_map` | [`:19`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L19) | `device_map` | `nmos_3p3`, `pmos_3p3` |
| `gmid_lut` | [`:23`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L23) | `gmid_lut` | **this line picks the gm/Id path** |
| `nmos`/`pmos` | [`:39`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L39) | `nmos`/`pmos` | body effect + caps only |
| `width`/`length`/`cap` | [`:50`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L50) | grids | W 0.22–100 µm, L 0.28–4 µm, C 0.01–20 pF |

No `device_handle` (GF180 uses the default `m0`) and no `wl_units` (default
`"m"`). Compare
[`tech_sky130.yaml:24`](../../../circuitgenome/sizer/config/tech_sky130.yaml#L24),
which needs both.

**[`spec_gf180.yaml`](../../../examples/two_stage_se_specs/spec_gf180.yaml)**:
13 lines, mapped field by field in the `SizingSpec` table in 1.1.

---

## 1.4 The walkthrough pages

[`models_explained.html`](../../_extra/walkthrough/models_explained.html) is
mostly current (updated for #287 on 2026-10-31).
[`loader_explained.html`](../../_extra/walkthrough/loader_explained.html) is
from 2026-08-27 and is further behind. Where they differ from the code:

| Page | Line | Page says | Code says |
|---|---|---|---|
| models | [`:199`](../../_extra/walkthrough/models_explained.html#L199) | the `SizingResult` box lists "intents" | `transistor_intents` was removed in PR #251 |
| models | [`:245`](../../_extra/walkthrough/models_explained.html#L245) | `MosfetParams` has five fields | seven: `cox` and `cdb` are missing |
| models | [`:290`](../../_extra/walkthrough/models_explained.html#L290) | `SpiceLib` has four fields | five: `mismatch_switch` is missing |
| models | [`:405`](../../_extra/walkthrough/models_explained.html#L405) | "Same crate, four destinies": `spice_lib`/`spice_model` pick the engine | only `gmid_lut` (and, for the refusal, `spice_model`) picks the *sizer*. `spice_lib` picks how `verify/` builds its deck |
| loader | [`:287`](../../_extra/walkthrough/loader_explained.html#L287) | `_mosfet` reads five keys | also `cox`, `cdb` |
| loader | [`:308`](../../_extra/walkthrough/loader_explained.html#L308) | `Path(path).parent / p` | `Path(path).resolve().parent / p` |
| loader | [`:321`](../../_extra/walkthrough/loader_explained.html#L321) | `nmos=_mosfet(data["nmos"])`, crashes if absent | `data.get("nmos") or {}`, and `nmos`/`pmos` are built before the constructor |
| loader | whole page | no `_validate_mosfets`, no `ValueError` | the #158 validation is the loader's main rule |
| loader | [`:385`](../../_extra/walkthrough/loader_explained.html#L385) | `SpiceLib(...)` without `mismatch_switch` | it is passed |
| loader | [`:411`](../../_extra/walkthrough/loader_explained.html#L411) | generic → `MosfetParams(vth=0.5, mu_cox=0.00027, lam=0.04)` | also `cox=6e-15` |

---

## Exit check

**Name every `SizingResult` field and which phase fills it.**

<details><summary>Answer</summary>

**gm/Id path** (gf180mcu, sky130, ptm45). One `SizingResult(...)` is built at
[`gmid_sizer.py:176`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L176),
inside `_size_planned`. The phase numbers are those in the module docstring
([`gmid_sizer.py:6`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L6)):
1 Analyze, 2 Bias currents, 3 Plan, 4 Size, 5 Evaluate.

| Field | Filled by | Phase | Line |
|---|---|---|---|
| `transistors` | `assign_geometry_gmid` → `check_dc_operating_point` → `check_stage_interface` (inside `_size`, re-run by the RNMC and two-stage refine loops), then `tune_bias_levels`, then `_with_fingers` splits wide devices | 4 (fingers at build time) | [`:119`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L119)–[`:129`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L129), [`:163`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L163), [`:177`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L177) |
| `resistors` | rail-referenced load resistors from `assign_currents`, merged with the non-load network from `size_resistors` and the bias-level resistors from `tune_bias_levels` | 2 + 4 | [`:92`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L92), [`:159`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L159), [`:165`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L165), [`:178`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L178) |
| `capacitors` | `plan.cc_pf`/`cc2_pf` from `compute_requirements` (Phase 3, possibly raised by the Phase-4 refine loops), turned into `{ref: farads}` by `size_compensation_caps` using the Phase-1 `view.slot_capacitors` | 3 (+ 4) | [`:115`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L115), [`:179`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L179); [`preprocess.py:105`](../../../circuitgenome/sizer/physics/preprocess.py#L105) |
| `metrics` | `evaluate_circuit` (which calls the shared `evaluate_metrics`) | 5 | [`:172`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L172) |
| `margins` | same call, same sign rule | 5 | [`:172`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L172) |
| `solver_status` | the constant `"GMID"` | — | [`:183`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L183) |
| `bias_feasible` | geometry feasible AND DC check AND stage-interface check (Phase 4 `_size`), AND follower-swing AND CMFB-sense-swing checks | 4 | [`:129`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L129), [`:169`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L169) |
| `open_loop_gain_measurable` | `eq.open_loop_gain_measurable(gain_db, gbw_hz)` on the Phase-5 metrics | after 5 | [`:185`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L185) |
| `warnings` | follower + CMFB swing (4), `view.warnings` (1), `plan.warnings` (3, gm-ceiling), `size_warnings` (4), `eval_notes` (5), `adoption_warnings(view.adopted)` (1) | 1, 3, 4, 5 | [`:189`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L189) |

When the spec sets `cmrr_min_db` on a circuit with the nodal CMRR model,
`lift_nodal_cmrr` calls `_size_planned` several times with a rising gm1 floor
and keeps one result ([`:96`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L96)).
Every field still comes from the phases above.

**Level-1 path** (`generic`), in
[`analytical/level1.py`](../../../circuitgenome/sizer/analytical/level1.py):

| Field | Filled by | Line |
|---|---|---|
| `transistors` | CP-SAT solve (`_solve`), possibly re-solved by the two-stage re-plan loop; `{}` if CP-SAT finds nothing | [`:123`](../../../circuitgenome/sizer/analytical/level1.py#L123), [`:179`](../../../circuitgenome/sizer/analytical/level1.py#L179) |
| `resistors` | `size_load_resistors` | [`:62`](../../../circuitgenome/sizer/analytical/level1.py#L62) |
| `capacitors` | `compute_requirements` → `size_compensation_caps` | [`:91`](../../../circuitgenome/sizer/analytical/level1.py#L91), [`:97`](../../../circuitgenome/sizer/analytical/level1.py#L97) |
| `metrics`, `margins` | `evaluate_metrics(chain, spec)`; `{}` when infeasible | [`:182`](../../../circuitgenome/sizer/analytical/level1.py#L182) |
| `solver_status` | CP-SAT status name (`OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, …) | [`:189`](../../../circuitgenome/sizer/analytical/level1.py#L189) |
| `bias_feasible` | **never set**: default `True` (no DC check on this path) | — |
| `open_loop_gain_measurable` | same function as gm/Id; default `True` when infeasible | [`:190`](../../../circuitgenome/sizer/analytical/level1.py#L190) |
| `warnings` | `view.warnings` + gm-ceiling warnings + adoption warnings | [`:95`](../../../circuitgenome/sizer/analytical/level1.py#L95) |

**After the sizer** the CLI may still change two fields: `bias_feasible` →
`False` and an extra `warnings` entry when the SPICE `.op` check fails
([`cli.py:246`](../../../circuitgenome/cli.py#L246)). That applies to both
paths, including `generic` (whose sizer always says `True`).

For `circuit_0010` on gf180: Phase 2 sets every current (10 µA per pair
device, 20 µA tail, 50 µA second stage) and no load resistors. Phase 3 sets
`cc_pf = 1.25` (0.25 × CL) and gm1 ≥ 15.7 µS. Phase 4 gives the 12
`TransistorSizing` entries. Phase 5 gives the 9 metrics and 7 margins.
</details>

## Simplification notes

| Location | Observation |
|---|---|
| `models.py:21-51` `MosfetParams` docstring | 30 lines for 7 fields; much of it is the #158/#221/#266 history. Your open TODO |
| `models.py:119-120` `TechParams` docstring | "`nmos`: NMOS Shichman-Hodges parameters". On gf180/sky130 these blocks hold only body-effect and cap constants. Your open TODO |
| `models.py:124-127` `TechParams.spice_model` docstring | "When `None`, the SPICE-verification path synthesises a Level-1 model". Not when `spice_lib` is set (gf180, sky130) |
| `models.py:167-182` `SizingSpec` docstring | No entry for `third_stage_current_ratio`; `ibias` is "External input bias current" while `sizer.rst:56` calls it the tail current |
| `models.py:274-277` `SizingResult.bias_feasible` docstring | "Always `True` for the Level-1 path": true of the sizer, but the CLI may set it `False` afterwards on any path |
| `loader.py:20` `load_spec` | A misspelt target key is silently dropped, so the target becomes unconstrained. A warning for unknown keys would catch typos |
| `loader.py:67-70` `load_tech` docstring | Lists `FileNotFoundError` and `KeyError` but not the `ValueError` from `_validate_mosfets` |
| `loader.py:136` | `is_level1=gmid_lut is None`: "Level-1" means "no LUT" here, while the dispatcher refuses "SPICE model and no LUT". Two definitions of the same idea in two files |
| `models_explained.html`, `loader_explained.html` | Stale fields and excerpts (table in 1.4); the loader page does not mention `_validate_mosfets` at all |
