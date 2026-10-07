"""
Golden-file tests for the full estimate pipeline.

Each case under tests/goldens/*.json captures:
  - representative inputs (geometry, bill, sizing mode, backup flags)
  - a fixed irradiance fixture (NASA POWER profile mocked)
  - the full run_full_estimation output

The test re-runs the pipeline with the same mocked irradiance and asserts
the live output matches the golden file (exact structure; floats within
a small absolute/relative tolerance).

---------------------------------------------------------------------------
How to regenerate golden files after a *deliberate* behavior change
---------------------------------------------------------------------------
If you intentionally change estimation math, subsidy policy, or sizing
rules — and the new numbers are correct — regenerate goldens instead of
weakening assertions:

  cd /path/to/SIGNIX-main
  PYTHONPATH=. python tests/test_estimate_goldens.py --regenerate

Review the git diff of tests/goldens/*.json carefully, then commit the
updated goldens with the behavior-change PR. Do **not** regenerate to
silence a failing test unless you have verified the new output is right.

Irradiance is always mocked in these tests so external APIs cannot drift
the goldens. Geometry and bill inputs are frozen inside each JSON file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "golden-tests")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import solar_logic

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"
FLOAT_ABS_TOL = 1e-6
FLOAT_REL_TOL = 1e-9


def _load_golden(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _run_pipeline(inputs: dict, irradiance_fixture: dict) -> dict:
    irr = irradiance_fixture["monthly_psh"]
    temp = irradiance_fixture.get("monthly_temp_c")
    source = irradiance_fixture.get("source", "nasa_power")
    confidence = irradiance_fixture.get("confidence", "high")
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irr, temp, source, confidence),
    ):
        return solar_logic.run_full_estimation(**inputs)


def _values_close(a, b, path: str, errors: list) -> None:
    """Recursively compare JSON-like structures with float tolerance."""
    if type(a) is not type(b) and not (
        isinstance(a, (int, float)) and isinstance(b, (int, float))
    ):
        # Allow int/float cross-match for whole numbers serialized as int
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            errors.append(f"{path}: type mismatch {type(a).__name__} vs {type(b).__name__}")
            return

    if isinstance(a, dict):
        a_keys = set(a)
        b_keys = set(b)
        if a_keys != b_keys:
            missing = sorted(a_keys - b_keys)
            extra = sorted(b_keys - a_keys)
            if missing:
                errors.append(f"{path}: missing keys in live output: {missing}")
            if extra:
                errors.append(f"{path}: unexpected keys in live output: {extra}")
            # Compare intersection only
        for key in sorted(a_keys & b_keys):
            _values_close(a[key], b[key], f"{path}.{key}", errors)
        return

    if isinstance(a, list):
        if len(a) != len(b):
            errors.append(f"{path}: list length {len(b)} != golden {len(a)}")
            return
        for i, (av, bv) in enumerate(zip(a, b)):
            _values_close(av, bv, f"{path}[{i}]", errors)
        return

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(float(a)) or math.isnan(float(b)):
            errors.append(f"{path}: NaN encountered ({a!r} vs {b!r})")
            return
        if not math.isclose(
            float(a), float(b), rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL
        ):
            errors.append(f"{path}: {b!r} != golden {a!r}")
        return

    if a != b:
        errors.append(f"{path}: {b!r} != golden {a!r}")


def _assert_matches_golden(golden: dict, live: dict) -> None:
    errors: list[str] = []
    _values_close(golden["output"], live, "output", errors)
    if errors:
        preview = "\n  ".join(errors[:25])
        more = f"\n  ... and {len(errors) - 25} more" if len(errors) > 25 else ""
        pytest.fail(
            f"Live estimate diverged from golden file ({len(errors)} mismatch(es)):\n"
            f"  {preview}{more}\n\n"
            "If this change is intentional, regenerate goldens:\n"
            "  PYTHONPATH=. python tests/test_estimate_goldens.py --regenerate"
        )


def _golden_paths():
    paths = sorted(GOLDEN_DIR.glob("*.json"))
    assert paths, f"No golden files found under {GOLDEN_DIR}"
    return paths


@pytest.mark.parametrize("golden_path", _golden_paths(), ids=lambda p: p.stem)
def test_estimate_pipeline_matches_golden(golden_path: Path):
    """Full pipeline: geometry → irradiance (mocked) → sizing → gen → $ → subsidy."""
    golden = _load_golden(golden_path)
    live = _run_pipeline(golden["inputs"], golden["irradiance_fixture"])
    _assert_matches_golden(golden, live)


# ---------------------------------------------------------------------------
# Optional CLI: regenerate goldens after deliberate behavior changes
# ---------------------------------------------------------------------------
def _regenerate_all() -> None:
    for path in _golden_paths():
        golden = _load_golden(path)
        live = _run_pipeline(golden["inputs"], golden["irradiance_fixture"])
        golden["output"] = live
        path.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n")
        print(f"regenerated {path.name} (system_size={live.get('system_size')})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help=(
            "Overwrite tests/goldens/*.json outputs from the current pipeline. "
            "Use only for intentional behavior changes; review the diff before commit."
        ),
    )
    args = parser.parse_args()
    if args.regenerate:
        _regenerate_all()
    else:
        parser.print_help()
