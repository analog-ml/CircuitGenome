"""
YAML configuration loaders.

Translates human-editable YAML files into :mod:`~circuitgenome.synthesizer.models`
instances.  Default paths resolve to the ``config/`` directory bundled with the
package; pass explicit paths to use custom definitions.
"""
from __future__ import annotations
from pathlib import Path

import yaml

from .models import (
    BiasLegLibrary,
    Connection,
    Device,
    ModuleVariant,
    PortDef,
    Slot,
    TopologyTemplate,
)

_CONFIG_DIR = Path(__file__).parent / "config"


def _config_path(filename: str) -> Path:
    return _CONFIG_DIR / filename


def load_modules(path: str | Path | None = None) -> dict[str, list[ModuleVariant]]:
    """Load module variant definitions from a YAML file.

    :param path: Path to a modules YAML file.  Defaults to the built-in
                 ``opamp_modules.yaml``.
    :returns: Dictionary mapping category name → list of
              :class:`~circuitgenome.synthesizer.models.ModuleVariant`.

    Example::

        from circuitgenome.synthesizer.loader import load_modules

        modules = load_modules()
        for variant in modules["input_pair"]:
            print(variant.name, variant.display_name)
    """
    path = Path(path) if path else _config_path("opamp_modules.yaml")
    with open(path) as f:
        data = yaml.safe_load(f)

    by_category: dict[str, list[ModuleVariant]] = {}
    for entry in data["modules"]:
        ports = [
            PortDef(name=p["name"], role=p["role"], alias_of=p.get("alias_of"))
            for p in entry["ports"]
        ]
        devices = [
            Device(
                ref=d["ref"],
                type=d["type"],
                terminals={k: v for k, v in d.items() if k not in ("ref", "type")},
            )
            for d in entry["devices"]
        ]
        variant = ModuleVariant(
            name=entry["name"],
            category=entry["category"],
            display_name=entry["display_name"],
            ports=ports,
            devices=devices,
            polarity=entry.get("polarity"),
            output_cardinality=entry.get("output_cardinality"),
        )
        by_category.setdefault(variant.category, []).append(variant)
    return by_category


def _load_devices(entries: list[dict]) -> list[Device]:
    return [
        Device(
            ref=d["ref"],
            type=d["type"],
            terminals={k: v for k, v in d.items() if k not in ("ref", "type")},
        )
        for d in entries
    ]


def load_bias_legs(path: str | Path | None = None) -> BiasLegLibrary:
    """Load the typed bias-leg library from a YAML file.

    :param path: Path to a bias-legs YAML file.  Defaults to the built-in
                 ``bias_legs.yaml``.
    :returns: A :class:`~circuitgenome.synthesizer.models.BiasLegLibrary`
              with the master reference, the ``pref`` branch, and one leg
              template per rail kind (see the YAML header for the contract).
    """
    path = Path(path) if path else _config_path("bias_legs.yaml")
    with open(path) as f:
        data = yaml.safe_load(f)

    return BiasLegLibrary(
        reference=_load_devices(data["reference"]["devices"]),
        pref_branch=_load_devices(data["pref_branch"]["devices"]),
        legs={leg["kind"]: _load_devices(leg["devices"]) for leg in data["legs"]},
    )


def load_topologies(path: str | Path | None = None) -> list[TopologyTemplate]:
    """Load topology templates from a YAML file.

    :param path: Path to a topologies YAML file.  Defaults to the built-in
                 ``opamp_topologies.yaml``.
    :returns: List of :class:`~circuitgenome.synthesizer.models.TopologyTemplate`.

    Example::

        from circuitgenome.synthesizer.loader import load_topologies

        for topo in load_topologies():
            print(topo.name, topo.config)
    """
    path = Path(path) if path else _config_path("opamp_topologies.yaml")
    with open(path) as f:
        data = yaml.safe_load(f)

    topologies = []
    for entry in data["topologies"]:
        slots = [Slot(name=s["name"], category=s["category"]) for s in entry["slots"]]
        connections = [
            Connection(slot=c["slot"], port=c["port"], net=c["net"])
            for c in entry["connections"]
        ]
        topologies.append(
            TopologyTemplate(
                name=entry["name"],
                config=entry.get("config", {}),
                external_ports=entry["external_ports"],
                slots=slots,
                connections=connections,
            )
        )
    return topologies
