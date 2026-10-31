# Stop 2 — Dispatcher: one door, three routes

**Files**:
[`sizer/sizer.py`](../../../circuitgenome/sizer/sizer.py) (71 lines) ·
[`sizer/__init__.py`](../../../circuitgenome/sizer/__init__.py) (59 lines) ·
[`gmid/intent.py`](../../../circuitgenome/sizer/gmid/intent.py) lines 225–327 only (`GmIdIntent`, `INTENT_BY_TECH`, `intent_for_tech`)
**Time**: 20 min
**After this stop you can**:
- trace any tech through `size_circuit` to the sizer it reaches
- list what `circuitgenome.sizer` exports and what it keeps private
- explain why "SPICE model, no LUT" is refused on purpose

## Background for this stop

- [Square law](00_background.md#square-law): simple and closed-form, but wrong
  by 2× or worse on real short-channel devices.
- [gm/Id method and the LUT](00_background.md#gmid): the accurate alternative,
  but it needs a table measured from the real device model.
- [Search vs forward pass](00_background.md#two-strategies): `analytical/` vs
  `gmid/`.
- [Inversion levels](00_background.md#inversion): the gm/Id "intent" decides
  how hard each kind of device is driven.

One idea is new here.

**Policy vs gap.** A *gap* is something the code cannot do yet but would do if
someone wrote it ("the GBW model ignores overlap capacitance"). A *policy* is
something the code *could* do but refuses to, because the answer would be
misleading. Analogy: a pharmacist who has the pills on the shelf but will not
hand them over without a prescription. The shelf is not empty (no gap). The
refusal protects you (policy).

---

## 2.1 `circuitgenome/sizer/sizer.py` (71 lines)

**Job in one sentence.** Look at the tech, pick the sizer that is valid for it,
call it, and return its `SizingResult` unchanged.

**Design knowledge it encodes.**
- A real device needs the [LUT](00_background.md#gmid); only the made-up
  `generic` tech may use the [square law](00_background.md#square-law).
- Both sizers return the same `SizingResult` contract (Stop 1), so callers
  never need to know which one ran
  ([two strategies](00_background.md#two-strategies)).

**Walkthrough.**

1. **Signature** ([`sizer.py:28`](../../../circuitgenome/sizer/sizer.py#L28)).
   Six inputs: the Layer 0, 1 and 2 results (`parsed`, `sr_result`,
   `fbr_result`), the `topology` template, the `tech`, the `spec`. Plus
   `time_limit_s` for the CP-SAT solver. `fbr_result` must come from
   *topology mode* (every device in a named slot,
   [pipeline layers](00_background.md#pipeline-layers)).
2. **Route 1: LUT present** ([`sizer.py:54`](../../../circuitgenome/sizer/sizer.py#L54)):

   ```python
   if getattr(tech, "gmid_lut", None):
       from .gmid import intent_for_tech, size_gmid
       return size_gmid(parsed, sr_result, fbr_result, topology, tech, spec,
                        intent=intent_for_tech(tech.name))
   ```

   This is the [gm/Id forward pass](00_background.md#gmid). The extra argument
   is the *design intent*: which [inversion level](00_background.md#inversion)
   and L multiple each kind of device gets (2.3). `time_limit_s` is not passed;
   the gm/Id path has no solver.
3. **Route 2: refusal** ([`sizer.py:62`](../../../circuitgenome/sizer/sizer.py#L62)).
   No LUT, but a flat SPICE model card → `UnsupportedTechError`. The message
   says to characterise a LUT first. The comment at
   [`:59`](../../../circuitgenome/sizer/sizer.py#L59) is the reason in one line:
   "those are the inaccurate numbers gm/Id exists to avoid".
4. **Route 3: default** ([`sizer.py:69`](../../../circuitgenome/sizer/sizer.py#L69)).
   Neither → `size_level1`, the [square law](00_background.md#square-law) plus
   CP-SAT search, with `time_limit_s`. Only `generic` reaches this line among
   the bundled techs.

There is no `elif`/`else`: each branch returns or raises, so order alone sets
priority. That matters for ptm45, which has *both* a LUT and a SPICE model.
Route 1 catches it first.

The imports inside the branches (`from .gmid import …`,
`from .analytical.level1 import …`) are lazy: a gm/Id run never imports the
OR-Tools sizer module, and the reverse.

**Running example.** `circuit_0010` + gf180, the way the CLI and
`run_example.py` do it:

```python
tech = load_tech("gf180mcu")          # Stop 1: gmid_lut = /…/gf180mcu_gmid.npz,
                                      #         spice_model = None, spice_lib = SpiceLib(...)
spec = load_spec("examples/two_stage_se_specs/spec_gf180.yaml")
result = size_circuit(parsed, sr, fbr, topology, tech, spec)
```

1. `tech.gmid_lut` is a non-empty path → Route 1.
2. `intent_for_tech("gf180mcu_3v3")`: the registry is empty →
   `DEFAULT_INTENT`.
3. `size_gmid(...)` runs the five phases (Stop 4) and returns
   `solver_status="GMID"`, `capacitors={'c1_compensation': 1.25e-12}`,
   `bias_feasible=True`, in about 0.06 s.
4. Route 2 is never checked. gf180 has no `spice_model` anyway: it has a
   `spice_lib`, which the dispatcher does not look at.

The same circuit on `generic` with `spec_generic.yaml` takes Route 3 and
returns `solver_status="OPTIMAL"` with `c1_compensation` = 2.86 pF.

**Test to read.**
- [`test_dc_op.py::test_spice_model_without_lut_raises_unsupported`](../../../tests/test_dc_op.py#L60):
  ptm45 with its LUT removed (`replace(load_tech("ptm45"), gmid_lut=None)`)
  must raise `UnsupportedTechError`, not fall through. This pins Route 2.
- [`test_dc_op.py::test_level1_path_bias_feasible_default_true`](../../../tests/test_dc_op.py#L75):
  Route 3 never runs a DC check, so `bias_feasible` stays `True`.
- [`test_dc_op.py::test_simple_mirror_tail_no_cascode_warning`](../../../tests/test_dc_op.py#L42):
  ptm45 lands on Route 1 (`solver_status == "GMID"`).

**Check yourself.**
1. Swap Route 1 and Route 2. Which bundled tech breaks?
2. You pass `time_limit_s=1` with gf180. What changes?

<details><summary>Answers</summary>

1. ptm45. It has a `spice_model`, so it would be refused even though its LUT is
   fine.
2. Nothing. `time_limit_s` reaches only `size_level1`
   ([`sizer.py:71`](../../../circuitgenome/sizer/sizer.py#L71)).
</details>

---

## 2.2 `circuitgenome/sizer/__init__.py` (59 lines): the public API

**Job in one sentence.** Decide what `from circuitgenome.sizer import …`
offers. That is the whole interface the CLI and the designer use.

**Design knowledge it encodes.** The four jobs from
[Stop 0](00_orientation.md#04-the-four-jobs-and-where-each-lives): contracts
in, one sizing door, the referee out. Everything else (`physics/`, `gmid/`,
`analytical/` internals) is private by convention.

**Walkthrough.** The exports
([`__init__.py:45`](../../../circuitgenome/sizer/__init__.py#L45)), by job:

| Job | Name | From | What it is |
|---|---|---|---|
| contracts | `load_spec`, `load_tech` | `loader.py` | Stop 1 |
| contracts | `SizingSpec`, `TechParams`, `SizingResult`, `TransistorSizing`, `UnsupportedTechError` | `models.py` | Stop 1 |
| dispatcher | `size_circuit` | `sizer.py` | 2.1 |
| referee | `simulate_metrics` | `verify/simulate.py` | measure all nine metrics in ngspice (Stop 6) |
| referee | `check_bias_soundness` | `verify/op.py` | SPICE DC verdict, `(ok, reason)` |
| referee | `ngspice_available` | `verify/deck.py` | is `ngspice` on `PATH`? |
| referee | `sized_netlist` | `verify/deck.py` | the input netlist with W/L/R/C written in |
| referee | `pdk_netlist` | `verify/deck.py` | that netlist rewritten for the foundry PDK (subcircuits, units) |

Not exported: `MosfetParams`, `GridSpec`, `SpiceLib` (you get them inside a
`TechParams`), and `size_gmid` / `size_level1` (reach them only through
`size_circuit`). The package root
([`circuitgenome/__init__.py:38`](../../../circuitgenome/__init__.py#L38))
re-exports only the contracts and `size_circuit`, not the `verify` functions.

The module docstring
([`__init__.py:4`](../../../circuitgenome/sizer/__init__.py#L4)) says the
sizer works "using OR-Tools CP-SAT". That is only true of Route 3 (README
simplification #1).

**Running example.** `run_example.py` imports exactly
`load_spec, load_tech, size_circuit, simulate_metrics` from here: two
contracts, the door, one referee.

**Test to read.**
[`test_loader.py:7`](../../../tests/test_loader.py#L7) imports `load_tech` from
the package, not from `loader.py`. Most tests do the same, so the export list
is exercised everywhere.

**Check yourself.**
1. Where would you import `size_gmid` from if you wanted to skip the
   dispatcher, and why might that be a bad idea?

<details><summary>Answers</summary>

1. `circuitgenome.sizer.gmid`. You would then pick the intent yourself
   (default `DEFAULT_INTENT`, [`gmid_sizer.py:80`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L80))
   and skip the refusal check. Only `size_circuit` guarantees the
   "valid sizer for this tech" rule.
</details>

---

## 2.3 `gmid/intent.py`: only `intent_for_tech` and `INTENT_BY_TECH`

**Job in one sentence (of this part).** Let a technology replace the default
gm/Id design choices, keyed by its name.

**Design knowledge it encodes.**
- [Inversion levels](00_background.md#inversion): current sources sit at about
  10 /V, cascodes at about 8 /V, followers at 15 /V; signal devices have
  theirs solved from the gm they need (the table in the module docstring,
  [`intent.py:28`](../../../circuitgenome/sizer/gmid/intent.py#L28)).
- [Output resistance](00_background.md#rout): current sources get a longer L
  (4 × `length.min`) than signal devices (2 ×)
  ([`intent.py:82`](../../../circuitgenome/sizer/gmid/intent.py#L82)).

**Walkthrough.**

1. `GmIdIntent` ([`intent.py:228`](../../../circuitgenome/sizer/gmid/intent.py#L228)):
   a frozen bundle of all these knobs, with the per-block table in
   `block_intents`. `DEFAULT_INTENT = GmIdIntent()`
   ([`:262`](../../../circuitgenome/sizer/gmid/intent.py#L262)).
2. `INTENT_BY_TECH` ([`intent.py:321`](../../../circuitgenome/sizer/gmid/intent.py#L321)):
   a dict from tech name to `GmIdIntent`. **It is empty.** The comment above
   it ([`:316`](../../../circuitgenome/sizer/gmid/intent.py#L316)) gives the
   reason from issue #77: on gf180 the defaults already meet the two-stage
   spec, and sky130's failures are not caused by the L policy.
3. `intent_for_tech(name)` ([`intent.py:324`](../../../circuitgenome/sizer/gmid/intent.py#L324)):
   `INTENT_BY_TECH.get(name, DEFAULT_INTENT)`. One line.

The key is `tech.name`, the YAML `name:` field: `gf180mcu_3v3`, `sky130_1v8`,
`ptm45_hp`. It is *not* the short name you type after `--tech`. An entry
registered as `"gf180mcu"` would silently never match.

Everything else in `intent.py` (`BlockIntent`, `resolve_transistor_intents`,
`make_intent`) belongs to Stop 4.

**Running example.** `intent_for_tech("gf180mcu_3v3")` returns
`DEFAULT_INTENT`. That is where `circuit_0010`'s L values come from: signal
devices 0.56 µm, current sources 1.12 µm (Stop 1, 1.1).

**Test to read.**
[`test_block_intent.py::test_intent_for_tech_registry_fallback`](../../../tests/test_block_intent.py#L104):
an unknown name gets `DEFAULT_INTENT`; a temporarily registered name gets its
own intent back.

**Check yourself.**
1. You want gf180 to use L = 3 × `length.min` for signal devices. What exactly
   do you add, and where?

<details><summary>Answers</summary>

1. `INTENT_BY_TECH["gf180mcu_3v3"] = make_intent(signal_l_mult=3.0)` in
   `intent.py`. Use `make_intent` so the per-block table and the flat fallback
   knobs move together
   ([`intent.py:265`](../../../circuitgenome/sizer/gmid/intent.py#L265)).
</details>

---

## Pointers to Stop 3 (your open TODOs)

Your #250 notes for this stop list two TODOs. Neither file is read here. Both
are in [Stop 3](03_physics.md):

- **Review RNMC three-stage compensation (PR #249).** The three-stage
  [nested Miller / RNMC](00_background.md#nested-miller) design is in
  `physics/rnmc.py` (`design_rnmc`) and the RNMC functions in
  `physics/equations.py` (`rnmc_pole_damping`, `phase_margin_rnmc_deg`,
  `rnmc_min_gm2`). The gm/Id re-plan on the sized circuit is
  `gmid/rnmc_refine.py` (Stop 4).
- **Rewrite the docstring of each function in `equations.py`.** That is the
  974-line formula file at the start of Stop 3. The dispatcher never calls it
  directly.

---

## Exit check

**Why is "SPICE model, no LUT → error" policy, not a gap?**

<details><summary>Answer</summary>

Because nothing is missing that would let the code size such a tech. It
**chooses** not to.

1. **The capability exists.** The loader accepts a SPICE-model tech without a
   LUT, as long as it carries `vth`/`mu_cox`/`lam`
   ([`loader.py:136`](../../../circuitgenome/sizer/loader.py#L136)). ptm45's
   YAML still carries such fits
   ([`tech_ptm45.yaml:21`](../../../circuitgenome/sizer/config/tech_ptm45.yaml#L21)).
   `size_level1` would run on it. Removing the three lines at
   [`sizer.py:62`](../../../circuitgenome/sizer/sizer.py#L62) would "fix" the
   error. Nobody does, on purpose.
2. **The answer would be confidently wrong.** The [square law](00_background.md#square-law)
   is off by 2× or more on short-channel devices. The ptm45 fit itself says it
   is "bias/length dependent", extracted at a single point
   ([`tech_ptm45.yaml:7`](../../../circuitgenome/sizer/config/tech_ptm45.yaml#L7)).
   Measured on `8571bf9`, with `circuit_0010` and `spec_ptm45.yaml`:

   | | gm/Id (Route 1) | Level-1 forced on ptm45 (`size_level1` called directly, LUT removed) |
   |---|---|---|
   | status | `GMID` | `OPTIMAL` |
   | input pair W/L | 4.5 / 0.09 µm | 0.1 / 0.045 µm |
   | predicted gain | 66.7 dB (passes 60 dB) | 63.1 dB (passes 60 dB) |
   | **ngspice gain** | **66.4 dB** | **21.2 dB** |
   | ngspice DC check | sound | `m2_tail_current` in triode |

   The Level-1 result *says* it meets the spec. ngspice says it misses gain by
   40 dB because the tail is not even biased. A silent wrong answer is worse
   than an honest error. That is the comment at
   [`sizer.py:59`](../../../circuitgenome/sizer/sizer.py#L59).
3. **The fix lives in data, not code.** The way forward is to measure a LUT
   (`tools/extract_tech.py --gm-id`) and add one `gmid_lut:` line. The tech
   then takes Route 1 with no code change. The error message says exactly
   that. Issue #73 was the plan to do this for PTM 32/22/16 nm. It was closed
   as *not planned* when those nodes were dropped (`a9be8fa`). So no bundled
   tech hits the error today; it guards future ones.
4. **It is a documented, tested contract.** The module docstring
   ([`sizer.py:13`](../../../circuitgenome/sizer/sizer.py#L13)), the error's own
   docstring ([`models.py:11`](../../../circuitgenome/sizer/models.py#L11)),
   `sizer.rst` "Path selection"
   ([`:205`](../../modules/sizer.rst?plain=1#L205)), the test
   [`test_dc_op.py:60`](../../../tests/test_dc_op.py#L60), and the CLI's clean
   exit ([`cli.py:235`](../../../circuitgenome/cli.py#L235)) all state it. It
   was introduced deliberately, in PR #85 (commit `890cf50`).

**The one real gap in the policy.** It is keyed on `spice_model` only. A
foundry PDK uses `spice_lib` instead. A `spice_lib` tech with no LUT but with
`vth`/`mu_cox`/`lam` in its YAML passes the loader and reaches Route 3.
Verified: `replace(gf180, gmid_lut=None, nmos=generic.nmos, pmos=generic.pmos)`
returns `solver_status="OPTIMAL"` instead of raising. Today this cannot happen
by accident with the bundled YAMLs, because gf180/sky130 carry no square-law
numbers and the loader rejects them without a LUT. But the rule "real device
model, no LUT → refuse" is enforced for one kind of real device model, not
both.
</details>

## Simplification notes

| Location | Observation |
|---|---|
| `sizer.py:62` | The refusal checks only `spice_model`. A `spice_lib` PDK without a LUT but with square-law numbers falls through to Level-1 (verified). `if tech.spice_model or tech.spice_lib` would match the stated policy |
| `sizer.py:54`, `:62` | `getattr(tech, "gmid_lut", None)`: `TechParams` always has these fields, so `tech.gmid_lut` would do |
| `sizer.py:64-67` | The error message says "PTM nodes" and "see issue #73"; #73 is closed (not planned) and the rule applies to any SPICE-model tech |
| `sizer.py:36`, `:49` | `time_limit_s` is silently ignored on the gm/Id route; the docstring says "Level-1 path only", but a caller cannot tell which route will run |
| `sizer.py:56-57` vs `gmid_sizer.py:80` | The per-tech intent lookup happens in the dispatcher, while `size_gmid` has its own default `DEFAULT_INTENT`. A direct `size_gmid` caller silently skips the registry |
| `gmid/intent.py:321` | `INTENT_BY_TECH` is keyed by the YAML `name:` (`gf180mcu_3v3`), not the `--tech` short name; easy to register under the wrong key |
| `sizer/__init__.py:4-5` | "using OR-Tools CP-SAT": only Route 3 (already README #1) |
| `circuitgenome/__init__.py:38` | The package root re-exports the contracts and `size_circuit` but none of the `verify` functions the sizer package exports |
