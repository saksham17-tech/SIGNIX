#!/usr/bin/env python3
"""
Seed (or re-seed) central PM Surya Ghar slabs into SubsidyScheme.

Current policy (open-ended revision, effective_from=2024-02-29):
  • ₹30,000 / kW for the first 2 kW
  • ₹18,000 / kW for capacity between 2–3 kW
  • Capped at ₹78,000 for systems above 3 kW

How to add a new scheme revision
--------------------------------
Do **not** edit existing rows. Insert a new set of slabs with a later
``effective_from`` (and optionally set ``effective_to`` on the previous
revision so the history is explicit).

``calculate_subsidy`` always picks the revision whose effective_from is the
most recent date on or before *today*.

Usage:
    python scripts/seed_subsidy_scheme.py
    python scripts/seed_subsidy_scheme.py --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.models import SubsidyScheme
from app.seeds import SCHEME_CODE, seed_subsidy_scheme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace slabs for the current effective_from instead of skipping",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        from app.database import initialize_database

        initialize_database()
        n = seed_subsidy_scheme(force=args.force)
        total = SubsidyScheme.query.filter_by(scheme_code=SCHEME_CODE).count()
        print(
            f"Inserted {n} SubsidyScheme row(s). "
            f"Total pm_surya_ghar rows now: {total}."
        )
        for row in (
            SubsidyScheme.query.filter_by(scheme_code=SCHEME_CODE)
            .order_by(SubsidyScheme.effective_from, SubsidyScheme.min_kw)
            .all()
        ):
            print(
                f"  {row.effective_from} → {row.effective_to or 'open'} | "
                f"{row.min_kw}-{row.max_kw} kW | "
                f"per_kw={row.amount_per_kw} fixed={row.fixed_amount} "
                f"cum={row.subsidy_amount}"
            )


if __name__ == "__main__":
    main()
