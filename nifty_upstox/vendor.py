"""Load the vendored agiprolabs skill scripts by file path (they are scripts, not a package)."""
from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parent.parent / "third_party" / "agiprolabs-claude-trading-skills" / "skills"


@lru_cache(maxsize=None)
def load(skill: str, script: str) -> ModuleType:
    path = _ROOT / skill / "scripts" / f"{script}.py"
    if not path.exists():
        raise FileNotFoundError(f"Vendored skill script missing: {path}")
    name = f"_vendor_{skill.replace('-', '_')}_{script}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses in the scripts need the module registered
    spec.loader.exec_module(mod)
    return mod


def regime_detection() -> ModuleType:
    return load("regime-detection", "detect_regime")


def overfit_detector() -> ModuleType:
    return load("walk-forward-validation", "overfit_detector")


def walk_forward() -> ModuleType:
    return load("walk-forward-validation", "walk_forward")


def drawdown_analyzer() -> ModuleType:
    return load("risk-management", "drawdown_analyzer")
