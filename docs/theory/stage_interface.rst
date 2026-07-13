Stage-Interface DC Feasibility
==============================

*Do two amplifier stages agree on the voltage of the wire they share?*

This page explains
:func:`~circuitgenome.sizer.gmid.stage_interface.check_stage_interface`, the
gm/Id sizer's DC-bias feasibility check for the node where the first stage hands
off to the second.  It is a *post-geometry* check — distinct from the
synthesizer's structural *stage-interface compatibility* filter, which runs
before enumeration and never looks at voltages.

Overview
--------

When two amplifier stages connect, they meet at a single node — the output of
the first stage *is* the input of the second.  That one wire has to satisfy
**both** stages at once:

- The **second stage** forces a specific DC voltage onto the node: its input
  transistor needs a particular ``V_GS`` to carry its design current, and that
  fixes the gate voltage.
- The **first stage's load** only stays healthy if the node sits inside a
  voltage *window* — high enough to keep its NMOS output-leg stack in
  saturation, low enough to keep its PMOS stack in saturation.

Ordinary gm/Id sizing sizes every transistor **on its own**.  Each device passes
its gm, current, and ``V_DS,sat`` checks, so the candidate looks feasible — then
dies at the SPICE ``.op`` with the load's output cascode in triode (issue #124).

This module answers the one question ordinary sizing never asks:

  *Does the voltage the second stage forces actually land inside the window the
  first stage allows — and if not, can we nudge the sizing until it does?*

It answers it **structurally** — from gm/Id lookup values and simple topology
walks, with no SPICE and no nonlinear solve — acting as a cheap filter (and
local repair engine) in front of the expensive simulation.

.. note::

   Think of two Lego blocks.  They only snap together if their connectors match.
   Here the "connector" is a DC voltage: the second stage presents one voltage,
   the first-stage load accepts a range, and this module checks that they fit.

The whole check is one condition on the shared node.  Writing :math:`V_{\text{pin}}`
for the voltage the second stage forces and :math:`[V_{\text{lo}}, V_{\text{hi}}]`
for the window the load allows, the design is feasible when

.. math::

   V_{\text{lo}} + \Delta \;\le\; V_{\text{pin}} \;\le\; V_{\text{hi}} - \Delta,
   \qquad \Delta = 0.05~\text{V},

or equivalently :math:`\operatorname{slack} \ge \Delta` with

.. math::

   \operatorname{slack} = \min\!\bigl(V_{\text{pin}} - V_{\text{lo}},\;
                                       V_{\text{hi}} - V_{\text{pin}}\bigr).

All three voltages come from topology, not simulation:

.. math::

   V_{\text{pin}} &= V_{\text{rail}} \pm V_{GS}
       \quad\text{(second-stage device pins the node)} \\
   V_{\text{lo}}  &= V_{\text{anchor}} + \sum_k V_{DS,\text{sat}}^{(k)}
       \quad\text{(NMOS output-leg stack)} \\
   V_{\text{hi}}  &= V_{\text{anchor}} - \sum_k V_{DS,\text{sat}}^{(k)}
       \quad\text{(PMOS output-leg stack)}

The margin :math:`\Delta` keeps every device comfortably inside saturation
rather than on the mathematical edge; the goal is to place :math:`V_{\text{pin}}`
inside :math:`[V_{\text{lo}}+\Delta,\, V_{\text{hi}}-\Delta]` *while preserving
each device's transconductance*.

How the check works
-------------------

The whole module is one decision, split across the shared interface node.  The
left branch asks *what the previous stage can provide*; the right branch asks
*what the next stage requires*; then the two are compared and, if they clash,
repaired:

.. code-block:: text

                          Interface node
                                │
              ┌─────────────────┴─────────────────┐
              ▼                                    ▼
        Previous stage                        Next stage
       Compute window                     Compute pin voltage
       (_stack_bound)                       (_pin_voltage)
              │                                    │
              ▼                                    ▼
         [ lo , hi ]                        required voltage
              └────────────── compare ──────────────┘
                                │
                            feasible?
                          ┌─────┴─────┐
                        yes           no
                          │            │
                     return OK    try repair
                                       │
                              gm/Id search loop
                              (_mirror_group_devs,
                               _resize_at_gmid)
                                       │
                            first successful fit
                                       │
                              return updated sizing

Every function in the file implements one box of this diagram.  Keep it in view
— the rest of the page walks the boxes left to right, top to bottom.

The picture
-----------

The shared node — call it ``net_mid`` — is squeezed between two transistor
stacks, and simultaneously pinned by the second stage's gate:

.. code-block:: text

                    vdd!
                     │
             ┌───────┴───────┐   PMOS load stack — each device costs a Vdsat
             │               │   ───  hi   (pin must stay BELOW this)
      Stage-2 gate ──● net_mid
             │               │   ───  lo   (pin must stay ABOVE this)
             │               │   NMOS load stack — each device costs a Vdsat
             └───────┬───────┘
                     │
                    vss!

        feasible   ⇔   lo + 0.05 V   ≤   pin   ≤   hi − 0.05 V

Two levels, one node:

- ``pin`` — the level the second stage **forces**.  *This is the question*
  :func:`~circuitgenome.sizer.gmid.stage_interface._pin_voltage` *answers:*
  "what voltage does the next stage require?"
- ``[lo, hi]`` — the **window** the load allows.  *This is the question*
  :func:`~circuitgenome.sizer.gmid.stage_interface._stack_bound` *answers:*
  "what voltage range can the previous stage provide?"

The extra ``0.05 V`` on each side is a safety margin (``_MARGIN_V``): a
transistor sitting one millivolt inside saturation is not a design you want,
because process, temperature, and model error will push it into triode.  The
check aims for *comfortable* saturation, not the bare mathematical edge.

.. note::

   It's a plumbing problem.  Tank B only accepts a water level between 40 cm and
   60 cm.  If tank A delivers 20 cm it never fills; at 100 cm it overflows.  Only
   40–60 cm works.  The interface voltage behaves exactly the same way — ``lo``
   and ``hi`` are the 40 cm and 60 cm marks.

Examples
--------

**Example 1 — feasible.**  Supply ``vdd = 1.8 V``, ``vss = 0 V``.  The second
stage is a common-source NMOS whose source sits on ground; gm/Id sizing gives it
``V_GS = 0.72 V``.  So it forces the node to:

.. code-block:: text

   pin = V_S + V_GS = 0 + 0.72 = 0.72 V

The first-stage cascode load allows the window ``lo = 0.45 V`` … ``hi = 1.38 V``.
Since ``0.45 + 0.05 ≤ 0.72 ≤ 1.38 − 0.05``, the node lands comfortably inside
the window.  **Accepted** — the sizing is returned unchanged.

**Example 2 — infeasible, then repaired.**  Same supply.  This time the load's
NMOS stack is taller and demands ``lo = 0.80 V``, while the second stage still
forces only ``pin = 0.72 V``:

.. code-block:: text

   pin = 0.72 V   <   lo + margin = 0.85 V     ✗  violates the lower bound

Before rejecting, the check tries its two real degrees of freedom.  It re-sizes
the load's NMOS mirror group toward weak inversion, which lowers that stack's
``V_GS`` and ``V_DS,sat`` and drops the bound to ``lo = 0.65 V``:

.. code-block:: text

   pin = 0.72 V   ≥   lo + margin = 0.70 V     ✓  feasible

The **repaired** sizing (mirror group moved to the fitting gm/Id) is returned.

**Example 3 — genuinely infeasible.**  If no gm/Id assignment can pull ``lo``
below ``pin`` — the second stage is a common-source device sized exactly at its
gm floor, so it can't move either — the verdict is an honest
``bias_feasible = False`` with a warning, and the candidate is **rejected
before** the SPICE run it could never pass.

----

How each piece works
--------------------

The pin: what the next stage requires
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

*This function answers:* "what voltage does the next stage force onto the
interface node?"

:func:`~circuitgenome.sizer.gmid.stage_interface._pin_voltage` handles the two
topologies whose gate voltage follows from the rail directly:

- **Common-source** (source on a rail): the gate sits one ``V_GS`` away from the
  rail, ``V_G = V_rail ± V_GS`` — e.g. an NMOS on ground with ``V_GS = 0.72 V``
  pins the node at ``0.72 V``.
- **Follower** (drain on a rail): the gate sits one ``V_GS`` from the quiescent
  output, taken as mid-supply ``(vdd + vss) / 2``.

Anything else — a cascode with both source and drain floating — cannot be
inferred without solving the circuit, so the function returns ``None`` and the
check bows out (returns feasible, deferring to SPICE).

The device being pinned is the *signal* device of the next stage (the one whose
gate the previous stage drives), **not** the fixed-bias current source beneath
it; :func:`~circuitgenome.sizer.gmid.stage_interface._pin_device` finds it by
walking the second/third-stage slots.

The window: what the previous stage can provide
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

*This function answers:* "what range of voltages keeps every transistor in the
load's output leg saturated?"

The previous section answered what the next stage *requires*;
:func:`~circuitgenome.sizer.gmid.stage_interface._stack_bound` answers the
complementary question of what the previous stage *allows*.  It walks the stack
drain→source from ``net_mid`` toward the rail, accumulating each crossed
device's ``V_DS,sat`` (the "floor"), until it reaches a node whose voltage it
actually knows — one of three **anchors**:

1. a **supply rail** (voltage known outright);
2. a **mirror cascode** whose gate is set by diode drops (source pinned at
   gate − ``V_GS``);
3. the **input pair**, whose source is pinned at ``Vcm ∓ V_GS`` by the
   feedback-held inputs.

An NMOS stack yields a **lower** bound (the node must sit *above* it); a PMOS
stack yields an **upper** bound.

.. note::

   Numeric trace: an output node above two NMOS devices with
   ``V_DS,sat = 0.18 V`` and ``0.22 V`` must stay above ``0.18 + 0.22 = 0.40 V``,
   or one of them drops out of saturation.

Recovering diode-chain bias voltages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

*This function answers:* "if a node is set by a chain of diode-connected
transistors, what DC voltage does it produce?"

Anchor 2 above needs the gate voltage of a mirror cascode, which is itself
generated by a diode-connected bias chain.
:func:`~circuitgenome.sizer.gmid.stage_interface._diode_chain_voltage` recovers
it.  Each diode-connected device fixes its gate one ``V_GS`` from its source, so
starting at the rail and adding (NMOS) or subtracting (PMOS) one ``V_GS`` per
device gives every node in the chain — no solver needed.

.. note::

   It's a staircase.  To know a step's height you first need the step below it,
   and to know that one you need the step below *it*, down to the floor (the
   rail).  Then you add back up: rail ``0 V`` → ``+0.70`` → ``+0.65`` → the node
   sits at ``1.35 V``.  This is why the function is recursive.

The repair engine: two degrees of freedom
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

*This section answers:* "the pin missed the window — what can we change, without
breaking gain, to move it back in?"

The check does not reject on the first miss.  It has exactly two spec-safe knobs:

- **Mirror stack.**  Re-size the violated side's load mirror group toward weak
  inversion; smaller ``V_GS`` and ``V_DS,sat`` shrink the whole stack
  requirement.  Always safe because mirror devices carry no gm requirement, and
  re-sizing the gate group at one gm/Id preserves the mirror ratios.
  :func:`~circuitgenome.sizer.gmid.stage_interface._mirror_group_devs` selects
  the group.
- **Second-stage device.**  Its ``|V_GS|`` may move, but only while
  ``gm/Id · Id`` still meets the stage's gm floor.  A follower can be pushed
  toward weak inversion (lower pin); a common-source device sized exactly at the
  floor cannot move the other way.

:func:`~circuitgenome.sizer.gmid.stage_interface._resize_at_gmid` recomputes a
device's geometry at a candidate gm/Id (snapping the width to the manufacturing
grid and re-reading ``V_GS``/``V_DS,sat`` from the LUT).  The check scans every
mirror gm/Id against every gm-valid second-stage gm/Id and takes the **first**
assignment that clears the margin — the least deviation from the original
sizing.

Stated abstractly, the repair searches two discrete gm/Id choices — the load
mirror group and the second-stage input device — for an assignment that restores
the feasibility condition:

.. math::

   \text{find } \; & (g_m/I_D)_{\text{mir}},\;(g_m/I_D)_{\text{in}}
       \in \mathcal{A} \\
   \text{s.t. } \; & (g_m/I_D)_{\text{in}}\cdot I_{D,\text{in}}
       \;\ge\; g_{m,\text{req}}
       \quad\text{(preserve the stage's transconductance)} \\
                   & V_{\text{lo}} + \Delta \;\le\; V_{\text{pin}}
       \;\le\; V_{\text{hi}} - \Delta
       \quad\text{(the violated bound moves with } (g_m/I_D)_{\text{mir}}\text{)}

where :math:`\mathcal{A}` is the LUT's gm/Id axis.  The mirror knob lowers only
the violated stack's bound; the input knob shifts :math:`V_{\text{pin}}`, but the
gm floor is why a follower can move toward weak inversion while a common-source
device sized exactly at its floor cannot.

.. note::

   This is a *feasibility search*, not a formal optimization: the code evaluates
   no objective function.  It tries "unchanged" first, scans :math:`\mathcal{A}`,
   and returns the **first** assignment that clears the margin — so it *prefers*
   the least change from the original sizing without computing an ``argmin``.  If
   none clears the full margin, it keeps the maximum-slack candidate.

The verdict
~~~~~~~~~~~

The search produces one of three outcomes, mirroring the three examples above:

- **slack ≥ 50 mV** → feasible; return the (possibly repaired) sizing.
- **0 ≤ slack < 50 mV** → clears the raw bounds but not the margin; keep the
  closest-fitting sizing and let the SPICE gate ground the verdict.
- **slack < 0** → no assignment clears even the raw bounds; return
  ``bias_feasible = False`` with an explanatory warning, rejecting the candidate
  before the SPICE evaluation it cannot pass.

How the pieces fit
~~~~~~~~~~~~~~~~~~

.. code-block:: text

   check_stage_interface()
   │
   ├── _pin_device()          which device does the previous stage drive?
   ├── _pin_voltage()         what voltage does it require?          (the pin)
   ├── _stack_bound()         what voltage window does the load allow?
   │     └── _diode_chain_voltage()   recover mirror-cascode bias voltages
   ├── _mirror_group_devs()   pick the mirror group to re-size       (repair)
   └── _resize_at_gmid()      re-size a device at a candidate gm/Id  (repair)

Scope and limitations
---------------------

The check applies to **single-ended cascode loads only**
(:func:`~circuitgenome.sizer.gmid.stage_interface.check_stage_interface` returns
feasible early otherwise).  A fully-differential first stage's output levels are
set by CMFB, not by the second-stage gate, and non-cascode loads have enough
headroom that the interface never binds.  The pin and diode-chain inference
deliberately return ``None`` for topologies they cannot resolve locally
(floating cascodes, complex bias networks), leaving those verdicts to SPICE.

For where this check sits in the wider gm/Id sizing pipeline, see
:doc:`gmid_sizing_flow`.

Reference
---------

.. autofunction:: circuitgenome.sizer.gmid.stage_interface.check_stage_interface

Internal helpers
~~~~~~~~~~~~~~~~~

The real machinery lives in the private helpers; signatures below are pulled
live from the source.

.. autofunction:: circuitgenome.sizer.gmid.stage_interface._pin_voltage
.. autofunction:: circuitgenome.sizer.gmid.stage_interface._stack_bound
.. autofunction:: circuitgenome.sizer.gmid.stage_interface._diode_chain_voltage
.. autofunction:: circuitgenome.sizer.gmid.stage_interface._resize_at_gmid
