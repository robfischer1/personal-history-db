"""Sparsity factor computation — isolated module for decay-policy integration.

Import this directly from the decay policy without pulling in the rest of
coverage_map. The only dependency is the coverage_map.json file on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_sparsity(coverage_json: Path | str) -> dict[tuple[int, str], float]:
    """Load sparsity factors from a coverage_map.json file.

    Returns a dict mapping (year, domain) → sparsity_factor.
    Thin cells have high factors (close to 1.0), dense cells have low (close to 0.0).
    """
    data = json.loads(Path(coverage_json).read_text(encoding="utf-8"))
    result: dict[tuple[int, str], float] = {}
    for cell in data.get("cells", []):
        key = (cell["year"], cell["domain"])
        result[key] = cell["sparsity_factor"]
    return result


def compute_sparsity(
    cells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute sparsity_factor for each cell in the coverage matrix.

    Input: list of dicts with keys: year, domain, count, scale_group.
    Output: same list with sparsity_factor added to each cell.

    Formula: 1 - (count / max_count_in_domain). Thin = high, dense = low.
    Domains with zero max get factor 1.0 (maximally sparse).
    """
    domain_max: dict[str, int] = {}
    for cell in cells:
        d = cell["domain"]
        domain_max[d] = max(domain_max.get(d, 0), cell["count"])

    for cell in cells:
        d = cell["domain"]
        mx = domain_max.get(d, 0)
        if mx == 0:
            cell["sparsity_factor"] = 1.0
        else:
            cell["sparsity_factor"] = round(1.0 - (cell["count"] / mx), 4)

    return cells
