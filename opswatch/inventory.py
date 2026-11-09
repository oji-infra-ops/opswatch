"""Load and validate inventory.yaml (SPEC §1). Runs before any probe starts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class InventoryError(Exception):
    """The inventory can't be used; the run is refused and nothing is written."""


@dataclass(frozen=True)
class Host:
    name: str
    address: str
    role: str
    ssh_user: str
    ssh_key: str
    services: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    ca_cert: str | None = None


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys (PyYAML silently keeps the last)."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise InventoryError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _str_list(value, field: str, host: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InventoryError(f"host {host!r}: {field} must be a list of strings")
    return tuple(value)


def _required_str(value, field: str, host: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InventoryError(f"host {host!r}: missing or empty {field}")
    return value


def parse_inventory(text: str) -> list[Host]:
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except InventoryError:
        raise
    except yaml.YAMLError as exc:
        raise InventoryError(f"invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise InventoryError("inventory must be a mapping with a 'hosts' key")
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise InventoryError("'defaults' must be a mapping")
    hosts = data.get("hosts")
    if not isinstance(hosts, dict) or not hosts:
        raise InventoryError("'hosts' is missing or empty")

    result: list[Host] = []
    for name, spec in hosts.items():
        if not isinstance(name, str) or not name:
            raise InventoryError(f"host name {name!r} must be a non-empty string")
        if not isinstance(spec, dict):
            raise InventoryError(f"host {name!r}: definition must be a mapping")
        ca_cert = spec.get("ca_cert")
        if ca_cert is not None and not isinstance(ca_cert, str):
            raise InventoryError(f"host {name!r}: ca_cert must be a string path")
        result.append(
            Host(
                name=name,
                address=_required_str(spec.get("address"), "address", name),
                role=str(spec.get("role", "")),
                ssh_user=_required_str(spec.get("ssh_user", defaults.get("ssh_user")), "ssh_user", name),
                ssh_key=_required_str(spec.get("ssh_key", defaults.get("ssh_key")), "ssh_key", name),
                services=_str_list(spec.get("services"), "services", name),
                urls=_str_list(spec.get("urls"), "urls", name),
                ca_cert=ca_cert,
            )
        )
    return result


def load_inventory(path: str | Path) -> list[Host]:
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise InventoryError(f"can't read inventory {path}: {exc}") from exc
    return parse_inventory(text)
