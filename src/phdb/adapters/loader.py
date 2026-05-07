"""Adapter discovery from configured paths.

Walks settings.adapter_paths looking for Python modules containing Adapter
subclasses. Returns a registry mapping adapter name -> adapter class.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from phdb.adapters.base import Adapter
from phdb.log import get_logger

log = get_logger("phdb.adapters.loader")


def _load_module_from_path(path: Path) -> object | None:
    """Import a .py file as a module."""
    module_name = f"phdb._ext_adapter_{path.stem}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        log.exception("Failed to load adapter module: %s", path)
        del sys.modules[module_name]
        return None

    return module


def discover_adapters(adapter_paths: list[Path]) -> dict[str, type[Adapter]]:
    """Scan directories for Adapter subclasses.

    Returns a dict mapping adapter.name -> adapter class. If two adapters
    share a name, the later one wins (instance overrides project).
    """
    registry: dict[str, type[Adapter]] = {}

    for search_path in adapter_paths:
        if search_path.is_file() and search_path.suffix == ".py":
            files = [search_path]
        elif search_path.is_dir():
            files = sorted(search_path.glob("*.py"))
        else:
            log.warning("Adapter path not found: %s", search_path)
            continue

        for py_file in files:
            if py_file.name.startswith("_"):
                continue

            module = _load_module_from_path(py_file)
            if module is None:
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, Adapter)
                    and obj is not Adapter
                    and hasattr(obj, "name")
                    and not inspect.isabstract(obj)
                ):
                    registry[obj.name] = obj
                    log.debug("Discovered adapter: %s from %s", obj.name, py_file)

    log.info("Loaded %d adapter(s): %s", len(registry), ", ".join(sorted(registry)))
    return registry
