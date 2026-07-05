"""
Cross-slot compatibility filter for :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Some module variants only form a circuit with a real DC current path when
paired with variants of a matching electrical *polarity*. For example, an
NMOS ``input_pair`` (``differential_pair_nmos``) draws current out of
``out1``/``out2`` into the tail, so it needs a ``load`` that *sources*
current into ``out1``/``out2`` from vdd, and a ``tail_current`` that *sinks*
the tail node to gnd. Pairing it with ``active_load_nmos`` (which also sinks
to gnd) or ``current_mirror_tail_pmos`` (which also sources from vdd) leaves
the shared node with no current path -- a non-functional circuit.

Each variant declares its required polarity via the ``polarity`` field in
``opamp_modules.yaml``: ``"pmos_input"``, ``"nmos_input"``, or omitted/``None``
for variants that work with either polarity (e.g. ``inverter_based_input``,
and -- for now -- every ``bias_generation`` variant, since a single bias_gen
variant feeds all four bias rails regardless of the load/input-pair polarity
that consumes each one).

``input_pair`` is the reference: a combination is valid as long as every other
polarity-tagged variant in it matches ``input_pair.polarity`` (or has no tag
at all). ``input_pair`` variants with no tag of their own
(``inverter_based_input``) impose no constraint, so they're compatible with
every ``load``/``tail_current``/etc. regardless of those variants' tags.

To extend: tag new or edited variants with ``polarity: pmos_input`` /
``polarity: nmos_input`` in ``opamp_modules.yaml`` -- no code changes needed
here.
"""
from __future__ import annotations
from ..models import ModuleVariant


def is_combination_valid(variant_map: dict[str, ModuleVariant]) -> bool:
    """Return ``False`` if *variant_map* pairs ``input_pair`` with a
    polarity-tagged variant of the opposite polarity.

    ``input_pair`` is the reference: every other slot's ``polarity`` tag (if
    any) must match ``input_pair.polarity``. If ``input_pair`` itself has no
    polarity tag (``inverter_based_input``), it has no current-direction
    requirement, so every combination involving it is valid.
    """
    input_pair = variant_map.get("input_pair")
    if input_pair is None or input_pair.polarity is None:
        return True
    return all(
        variant.polarity is None or variant.polarity == input_pair.polarity
        for slot_name, variant in variant_map.items()
        if slot_name != "input_pair"
    )
