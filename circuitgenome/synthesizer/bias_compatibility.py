"""
Bias-rail *flavor* compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``bias_generation`` leg delivers its rail voltage through a
diode-connected MOSFET, and that diode pins the rail to one supply: a PMOS
diode hangs from vdd (rail settles near ``VDD - |V_GSP|``), an NMOS diode
sits on gnd (rail settles near ``V_GSN``).  We call the supply the rail
voltage tracks the rail's **flavor** -- ``"vdd"`` or ``"gnd"``.  A
``resistor_bias`` leg has no diode (``out_i = I_leg * R_i``), so its level is
tunable per-rail and it has *no* flavor.

Consumers impose the opposite requirement.  A consumer MOSFET whose gate
sits on a bias rail and whose source sits on a supply needs a gate voltage
one ``V_GS`` away from *that* supply -- i.e. a rail of matching flavor:

- a PMOS current source (source at vdd) gated by a gnd-flavored rail sees
  ``V_SG = VDD - V_GSN`` (~2.5 V at 3.3 V supply) and overpowers everything
  beneath it (the measured rail-5 "output pinned at VDD" class);
- an NMOS current sink (source at gnd) gated by a vdd-flavored rail
  (~2.35 V) is massively overdriven and pins its drain at GND (the
  measured rail-1 class);
- a current-mirror tail's own reference diode on rail 7 converts the leg
  current into its mirror-gate voltage; the leg's diode must be the *same*
  flavor (it is then redundant and dropped by
  :func:`~circuitgenome.synthesizer.bias_pruning.prune_redundant_tail_diode`,
  leaving a correctly-directed current source/sink).  A *cross*-flavor leg
  diode instead fights the tail's diode for the rail voltage and leaves the
  reference current uncontrolled (measured 22x the planned current).

No sizing can fix any of these -- in the post-rig-fix dataset for issue #99,
zero candidates carrying such a mismatch ever reached ``bias_ok``, and their
open-loop DC sweeps show no reachable operating point.  Rejecting them buys
honest rejection statistics and less wasted SPICE work, **not** new working
circuits.

Both sides are derived **structurally** from the netlist (no YAML tags, so
nothing can drift from the devices):

- provided flavor (:func:`provided_rail_flavors`): the diode-connected
  device on each ``out1``..``out7`` leg, keyed by channel type.
- required flavor (:func:`required_rail_flavors`): per consumer device on a
  ``net_bias1``..``net_bias7`` net -- a gate whose source sits on a supply
  requires that supply's flavor; a diode-connected device on the rail (a
  mirror tail's reference, including the cascode tails' stacked reference
  whose source is an internal node) requires its own channel type's flavor.
  Consumers whose source is an *internal* node (cascode gates: ``bias2`` of
  the folded-cascode loads, ``bias1`` of the telescopic loads) impose no
  requirement -- no current bias_generation variant produces a
  cascode-appropriate level, and that gap is out of scope here (issue #99).

Only rails a device actually references can acquire a requirement, so rails
that are unconsumed (and would be dropped by
:func:`~circuitgenome.synthesizer.bias_pruning.prune_bias_generation`
anyway) never cause a rejection.  For the same reason this filter must run
**after** :func:`~circuitgenome.synthesizer.cmfb_compatibility.prune_cmfb` /
:func:`~circuitgenome.synthesizer.tail_current_compatibility.prune_tail_current`,
so emptied placeholder slots impose nothing.

Because ``bias_generation`` is one slot of the enumeration product,
rejecting a mismatched pairing silently *routes* each consumer set to the
generators that fit: homogeneous consumer sets keep the matching
single-flavor generator (plus ``resistor_bias``), while mixed-flavor sets
(e.g. rail-5 vdd + rail-7 gnd) keep only ``resistor_bias``.
"""
from __future__ import annotations

from .models import Device, ModuleVariant, TopologyTemplate

_BIAS_NET_INDEX = {f"net_bias{i}": i for i in range(1, 8)}
_SUPPLIES = ("vdd", "gnd")


def rail_flavor_from_diode(devices: list[Device], node: str) -> str | None:
    """Return the flavor a diode-connected MOSFET on *node* pins it to.

    A diode-connected device (``d == g == node``) ties the node one ``V_GS``
    away from whatever its source ultimately rests on, and its channel type
    names that supply: PMOS conducts from vdd (``"vdd"``), NMOS to gnd
    (``"gnd"``).  The channel type is used rather than the source terminal so
    stacked references (a cascode tail's ``d == g == bias`` device whose
    source is an internal node of the stack) resolve correctly.  Returns
    ``None`` when no diode-connected MOSFET sits on *node* (e.g. a
    ``resistor_bias`` leg).
    """
    for dev in devices:
        if (
            dev.type in ("nmos", "pmos")
            and dev.terminals.get("d") == node
            and dev.terminals.get("g") == node
        ):
            return "vdd" if dev.type == "pmos" else "gnd"
    return None


def provided_rail_flavors(bias_variant: ModuleVariant) -> dict[int, str]:
    """Return ``{rail_index: flavor}`` for every leg with a diode-connected
    delivery device.

    Rails absent from the result (all of ``resistor_bias``'s) are tunable and
    compatible with any consumer.
    """
    flavors: dict[int, str] = {}
    for i in range(1, 8):
        flavor = rail_flavor_from_diode(bias_variant.devices, f"out{i}")
        if flavor is not None:
            flavors[i] = flavor
    return flavors


def required_rail_flavors(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> dict[int, set[str]]:
    """Return ``{rail_index: {flavors}}`` demanded by the consuming slots.

    For every slot other than ``bias_generation``, every MOSFET whose gate
    lands on a port wired to ``net_bias1``..``net_bias7`` contributes:

    - its own channel type's flavor if it is diode-connected on the rail
      (a mirror tail's reference diode, including cascode stacks), else
    - its source's supply if the source sits on ``vdd``/``gnd``, else
    - nothing (cascode gates -- source on an internal node).

    A rail can collect *both* flavors (e.g. a vdd-flavored second stage with
    a gnd-flavored tail never shares a single-flavor generator) -- such
    combinations survive only with the flavorless ``resistor_bias``.
    """
    required: dict[int, set[str]] = {}
    for slot in topology.slots:
        if slot.category == "bias_generation":
            continue
        variant = variant_map[slot.name]
        for port_name, net in topology.slot_connections(slot.name).items():
            idx = _BIAS_NET_INDEX.get(net)
            if idx is None:
                continue
            for dev in variant.devices:
                if dev.type not in ("nmos", "pmos"):
                    continue
                if dev.terminals.get("g") != port_name:
                    continue
                if dev.terminals.get("d") == port_name:
                    flavor = "vdd" if dev.type == "pmos" else "gnd"
                elif dev.terminals.get("s") in _SUPPLIES:
                    flavor = dev.terminals["s"]
                else:
                    continue
                required.setdefault(idx, set()).add(flavor)
    return required


def is_bias_flavor_compatible(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> bool:
    """Return ``False`` if any consumed bias rail's required flavor
    contradicts the flavor the ``bias_generation`` variant delivers on it.

    Rails the generator delivers without a diode (``resistor_bias``: tunable)
    and rails whose consumers impose no requirement (cascode gates,
    unconsumed rails) never reject.  Must be called after
    ``prune_cmfb``/``prune_tail_current`` have replaced irrelevant slots with
    empty placeholders (see the module docstring).
    """
    bias_slot = next(s for s in topology.slots if s.category == "bias_generation")
    provided = provided_rail_flavors(variant_map[bias_slot.name])
    if not provided:
        return True
    required = required_rail_flavors(topology, variant_map)
    return all(
        idx not in provided or flavors == {provided[idx]}
        for idx, flavors in required.items()
    )
