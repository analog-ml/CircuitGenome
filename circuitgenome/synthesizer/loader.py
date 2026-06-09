from __future__ import annotations
import importlib.resources
from pathlib import Path

import yaml

from .models import Connection, Device, ModuleVariant, PortDef, Slot, TopologyTemplate

_CONFIG_DIR = Path(__file__).parent / "config"


def _config_path(filename: str) -> Path:
    return _CONFIG_DIR / filename


def load_modules(path: str | Path | None = None) -> dict[str, list[ModuleVariant]]:
    path = Path(path) if path else _config_path("opamp_modules.yaml")
    with open(path) as f:
        data = yaml.safe_load(f)

    by_category: dict[str, list[ModuleVariant]] = {}
    for entry in data["modules"]:
        ports = [PortDef(name=p["name"], role=p["role"]) for p in entry["ports"]]
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
        )
        by_category.setdefault(variant.category, []).append(variant)
    return by_category


def load_topologies(path: str | Path | None = None) -> list[TopologyTemplate]:
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
