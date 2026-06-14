"""
Net-merge pass for ``load`` ports declared ``alias_of`` another ``load`` port,
run by :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits` after
``load``'s port-to-net map has been built.

``opamp_topologies.yaml`` assigns ``load.in1``/``in2`` (the folding nodes fed
by ``input_pair.out1``/``out2``) and ``load.out``/``out1``/``out2`` (the
load's actual output node(s), sensed by ``cmfb``/``second_stage*``/``comp*``
and -- in ``single_ended`` topologies -- the stage-output net) to *separate*
nets. This is correct for the 6 cascode ``load`` variants, where the folding
node and the cascode's output are distinct device terminals (e.g.
``mn1{d: out1, g: bias2, s: in1}``).

For the other 6 ``load`` variants (resistor/active/current-source loads),
``in1``/``in2`` and ``out1``/``out2`` *are* the same physical node -- declared
via ``out1: {alias_of: in1}`` / ``out2: {alias_of: in2}`` in
``opamp_modules.yaml``. :func:`compute_alias_net_rename` finds these
declarations and, for each one whose two assigned nets differ, computes a
rename merging them into a single net; :func:`apply_net_rename` then rewrites
every device's terminals (across all slots) accordingly -- restoring, for
these 6 variants, the single shared in/out node that their devices assume.

To extend: declare ``alias_of`` on any new ``load`` port whose net should be
merged with another -- no code changes needed here.
"""
from __future__ import annotations

from .models import Device, ModuleVariant


def compute_alias_net_rename(
    load_variant: ModuleVariant,
    load_port_net_map: dict[str, str],
    external_ports: list[str],
) -> dict[str, str]:
    """Return a ``{old_net: new_net}`` rename for *load_variant*'s ``alias_of`` ports.

    For each port ``p`` with ``p.alias_of = q``, if ``p`` and ``q`` were
    assigned different nets, merge them into one. The merge keeps whichever
    net is an external subcircuit port (so the ``.subckt`` port list stays
    intact); if neither (or both) are external, ``q``'s net is folded into
    ``p``'s.
    """
    rename: dict[str, str] = {}
    for port in load_variant.ports:
        if port.alias_of is None:
            continue
        net_p = load_port_net_map.get(port.name)
        net_q = load_port_net_map.get(port.alias_of)
        if net_p is None or net_q is None or net_p == net_q:
            continue
        if net_q in external_ports:
            rename[net_p] = net_q
        else:
            rename[net_q] = net_p
    return rename


def apply_net_rename(
    devices: list[tuple[str, Device]],
    rename: dict[str, str],
) -> list[tuple[str, Device]]:
    """Return *devices* with every terminal net rewritten according to *rename*.

    Nets not present in *rename* are left unchanged. Applies across all
    slots' devices, not just ``load``'s.
    """
    if not rename:
        return devices
    result = []
    for ref, dev in devices:
        terminals = {term: rename.get(net, net) for term, net in dev.terminals.items()}
        result.append((ref, Device(ref=dev.ref, type=dev.type, terminals=terminals)))
    return result
