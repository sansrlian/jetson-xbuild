"""Load jxb profiles. A profile may `inherit` another one; tables are merged recursively, other values replaced."""
from __future__ import annotations

import tomllib
from pathlib import Path


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_profile(path: Path) -> dict:
    data = tomllib.loads(Path(path).read_text())
    parent = data.pop("inherit", None)
    if parent:
        data = _merge(load_profile(Path(path).with_name(f"{parent}.toml")), data)
    return data
