"""
Demand-driven bias-generation construction for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

The bias generator is not an enumerated module variant: it is *constructed*
per combination from what the other slots actually consume on each bias rail.
This replaces the enumerate-then-filter-then-prune chain that the fixed
``bias_generation`` variants needed (issues #99/#101/#102): every consumed
rail gets exactly the leg its consumer requires, so flavor mismatches,
unused legs, and the redundant rail-7 diode cannot arise in the first place.

Each consumed rail is classified into a **kind** -- the type system this
module is built around:

- ``gate_vdd`` -- a consumer MOSFET gate whose source sits at vdd (a PMOS
  current source). The rail must sit one ``|V_GSP|`` below vdd, and the leg's
  diode-connected PMOS is the mirror *master* of its consumers: the sizer
  sets the consumer current by W/L ratio instead of hoping a voltage matches.
- ``gate_gnd`` -- mirror image: consumer gate with source at gnd (NMOS
  current sink); NMOS diode leg one ``V_GSN`` above gnd.
- ``current_source`` -- the consumer brings its own NMOS reference diode (a
  current-mirror tail's mirror diode). The rail
  is a *current* interface: the leg is a bare PMOS mirror sourcing the
  reference current into the consumer's diode. A voltage-style leg here
  would either duplicate the consumer's diode (parallel diodes splitting the
  reference current) or fight it (the measured 22x rail-7 contention of
  issue #99).
- ``current_sink`` -- mirror image for a PMOS-diode consumer: bare NMOS
  mirror sinking the reference current.
- ``cascode_gnd`` -- an NMOS cascode gate: consumer gate on the rail with
  its source on an *internal* node (a stacked device below). The rail must
  sit at the cascode's ``V_GS`` plus the saturation floor of the stack under
  it, so the leg is a diode-connected NMOS (the tracking ``V_GS`` part) on a
  small floor resistor (``out = V_GSN + I*R``), both sized per-rail by the
  sizer from the consumer stack (issue #99's parked cascode class).
- ``cascode_vdd`` -- mirror image for PMOS cascode gates: diode-connected
  PMOS on a resistor to vdd (``out = vdd - |V_GSP| - I*R``).
- ``tunable`` -- no structurally implied level: conflicting demands on a
  shared rail. The leg is a PMOS mirror into a resistor
  (``out = I_leg * R``), per-rail tunable by the sizer (issue #100).

The leg templates and the multi-reference core (NMOS master on the ``ibias``
pin, plus a ``pref`` branch deriving the PMOS-side mirror reference, emitted
only when a leg needs it) live in ``config/bias_legs.yaml`` -- see its header
for the electrical layout and the template net-name contract.

Both the demand analysis and the assembly are **structural** (actual device
terminal references, no YAML tags), so new consumer variants are classified
correctly without code changes. Construction must run **after**
:func:`~circuitgenome.synthesizer.cmfb_compatibility.prune_cmfb` /
:func:`~circuitgenome.synthesizer.tail_current_compatibility.prune_tail_current`,
so emptied placeholder slots demand nothing.
"""
from __future__ import annotations

from .models import BiasLegLibrary, Device, ModuleVariant, PortDef, TopologyTemplate

_BIAS_NET_INDEX = {f"net_bias{i}": i for i in range(1, 9)}
_SUPPLIES = ("vdd", "gnd")

#: Rail kinds whose legs mirror from the PMOS-side reference gate. Present
#: for documentation; the pref branch is actually emitted on a structural
#: check (an instantiated leg terminal referencing ``pref``).
PREF_KINDS = frozenset({"gate_gnd", "current_source", "tunable", "cascode_gnd"})

#: Template-local nets instantiated once per rail (rewritten ``{net}{i}``);
#: every other template net (ibias/pref/vdd/gnd) is shared across legs.
_PER_LEG_NETS = ("out", "mid")


def rail_flavor_from_diode(devices: list[Device], node: str) -> str | None:
    """Return the supply a diode-connected MOSFET on *node* pins it to.

    A diode-connected device (``d == g == node``) ties the node one ``V_GS``
    away from whatever its source ultimately rests on, and its channel type
    names that supply: PMOS conducts from vdd (``"vdd"``), NMOS to gnd
    (``"gnd"``).  The channel type is used rather than the source terminal so
    stacked references (a cascode tail's ``d == g == bias`` device whose
    source is an internal node of the stack) resolve correctly.  Returns
    ``None`` when no diode-connected MOSFET sits on *node*.
    """
    for dev in devices:
        if (
            dev.type in ("nmos", "pmos")
            and dev.terminals.get("d") == node
            and dev.terminals.get("g") == node
        ):
            return "vdd" if dev.type == "pmos" else "gnd"
    return None


def _device_votes(
    variant: ModuleVariant, port_name: str
) -> tuple[set[str], set[str], set[str]]:
    """Return ``(diode_kinds, gate_kinds, cascode_kinds)`` demanded on *port_name*.

    Per consumer MOSFET whose gate lands on the port:

    - diode-connected on the port (``d == g == port``): the consumer is a
      mirror reference converting a *current* into its own gate voltage --
      an NMOS diode wants current sourced in (``current_source``), a PMOS
      diode wants it sunk out (``current_sink``);
    - else, source on a supply: the gate needs a voltage one ``V_GS`` from
      that supply (``gate_vdd`` / ``gate_gnd``);
    - else (source on an internal node -- a cascode gate): the gate needs
      its ``V_GS`` plus the stack's saturation floor, referenced to the
      supply its channel type conducts toward (``cascode_gnd`` for NMOS,
      ``cascode_vdd`` for PMOS).
    """
    diode_kinds: set[str] = set()
    gate_kinds: set[str] = set()
    cascode_kinds: set[str] = set()
    for dev in variant.devices:
        if dev.type not in ("nmos", "pmos"):
            continue
        if dev.terminals.get("g") != port_name:
            continue
        if dev.terminals.get("d") == port_name:
            diode_kinds.add(
                "current_source" if dev.type == "nmos" else "current_sink"
            )
        elif dev.terminals.get("s") in _SUPPLIES:
            gate_kinds.add(f"gate_{dev.terminals['s']}")
        else:
            cascode_kinds.add(
                "cascode_gnd" if dev.type == "nmos" else "cascode_vdd"
            )
    return diode_kinds, gate_kinds, cascode_kinds


def required_rail_kinds(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> dict[int, str]:
    """Return ``{rail_index: kind}`` for every bias rail actually consumed.

    A rail is consumed when any device terminal of the slot's variant
    references the port the topology wires to ``net_bias1``..``net_bias8``
    (declared-but-unwired ``optional`` ports are ignored, exactly like the
    retired ``needed_bias_outputs``). The kind combines the per-device votes
    of :func:`_device_votes` across all consuming slots:

    - a diode vote wins over gate votes: the consumer's own diode makes the
      rail a current interface, and any mirror gates riding on it (the tail's
      output device) are slaved to that diode, not to the leg;
    - a single gate kind is taken as-is;
    - a single cascode kind (and no diode/gate votes) is taken as-is -- a
      supply-referenced gate sharing the rail would need one ``V_GS`` where
      the cascode needs ``V_GS`` plus a floor, so that mix is a conflict;
    - conflicting votes fall back to ``tunable`` -- the honest "no single
      structural level implied" answer.

    Unconsumed rails are absent from the result and get no leg at all.
    """
    diode_votes: dict[int, set[str]] = {}
    gate_votes: dict[int, set[str]] = {}
    cascode_votes: dict[int, set[str]] = {}
    consumed: set[int] = set()
    for slot in topology.slots:
        if slot.category == "bias_generation":
            continue
        variant = variant_map[slot.name]
        used_local_nets = {
            local_net
            for dev in variant.devices
            for local_net in dev.terminals.values()
        }
        for port_name, net in topology.slot_connections(slot.name).items():
            idx = _BIAS_NET_INDEX.get(net)
            if idx is None or port_name not in used_local_nets:
                continue
            consumed.add(idx)
            diode_kinds, gate_kinds, cascode_kinds = _device_votes(variant, port_name)
            diode_votes.setdefault(idx, set()).update(diode_kinds)
            gate_votes.setdefault(idx, set()).update(gate_kinds)
            cascode_votes.setdefault(idx, set()).update(cascode_kinds)

    kinds: dict[int, str] = {}
    for idx in consumed:
        diodes = diode_votes.get(idx, set())
        gates = gate_votes.get(idx, set())
        cascodes = cascode_votes.get(idx, set())
        if len(diodes) == 1:
            kinds[idx] = next(iter(diodes))
        elif not diodes and len(gates) == 1 and not cascodes:
            kinds[idx] = next(iter(gates))
        elif not diodes and not gates and len(cascodes) == 1:
            kinds[idx] = next(iter(cascodes))
        else:
            kinds[idx] = "tunable"
    return kinds


def construct_bias_generation(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
    library: BiasLegLibrary,
) -> ModuleVariant:
    """Assemble the bias-generation variant this combination needs.

    Emits the master reference, one leg per consumed rail (template chosen by
    :func:`required_rail_kinds`, with the per-leg nets ``out``/``mid``
    rewritten to ``out{i}``/``mid{i}`` and each ref suffixed with the rail
    index), and the ``pref`` branch when any instantiated leg references the
    ``pref`` net. The result is a normal
    :class:`~circuitgenome.synthesizer.models.ModuleVariant` (name
    ``constructed_bias``) -- downstream wiring, serialization, and the
    recognizer treat it like any other variant. ``pref`` (and the pref
    branch's ``prefsrc``/``ncasc``, and the cascode legs' ``mid{i}``) are not
    ports, so they resolve to slot-internal nets.
    """
    kinds = required_rail_kinds(topology, variant_map)

    leg_devices: list[Device] = []
    uses_pref = False
    for idx in sorted(kinds):
        for tmpl in library.legs[kinds[idx]]:
            terminals = {
                term: (f"{net}{idx}" if net in _PER_LEG_NETS else net)
                for term, net in tmpl.terminals.items()
            }
            if "pref" in terminals.values():
                uses_pref = True
            leg_devices.append(
                Device(ref=f"{tmpl.ref}{idx}", type=tmpl.type, terminals=terminals)
            )

    devices = list(library.reference)
    if uses_pref:
        devices.extend(library.pref_branch)
    devices.extend(leg_devices)

    ports = [PortDef(name="ibias", role="input")]
    ports.extend(PortDef(name=f"out{idx}", role="output") for idx in sorted(kinds))
    ports.append(PortDef(name="vdd", role="supply"))
    ports.append(PortDef(name="gnd", role="supply"))

    return ModuleVariant(
        name="constructed_bias",
        category="bias_generation",
        display_name="Constructed Multi-Reference Bias",
        ports=ports,
        devices=devices,
    )
