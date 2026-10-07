"""Reference data seeders for SIGNIX."""

from __future__ import annotations

from datetime import date

from app.models import SubsidyScheme, TariffTable, db

SCHEME_CODE = "pm_surya_ghar"
CURRENT_EFFECTIVE_FROM = date(2024, 2, 29)


def current_pm_surya_ghar_slabs(effective_from: date = CURRENT_EFFECTIVE_FROM):
    """Central PM Surya Ghar slab rows for one revision (not yet committed)."""
    return [
        SubsidyScheme(
            scheme_code=SCHEME_CODE,
            min_kw=0.0,
            max_kw=2.0,
            amount_per_kw=30000.0,
            fixed_amount=None,
            subsidy_amount=60000.0,
            effective_from=effective_from,
            effective_to=None,
            notes="₹30,000/kW for first 2 kW",
        ),
        SubsidyScheme(
            scheme_code=SCHEME_CODE,
            min_kw=2.0,
            max_kw=3.0,
            amount_per_kw=18000.0,
            fixed_amount=None,
            subsidy_amount=78000.0,
            effective_from=effective_from,
            effective_to=None,
            notes="₹18,000/kW for 2–3 kW step",
        ),
        SubsidyScheme(
            scheme_code=SCHEME_CODE,
            min_kw=3.0,
            max_kw=1000.0,
            amount_per_kw=0.0,
            fixed_amount=78000.0,
            subsidy_amount=78000.0,
            effective_from=effective_from,
            effective_to=None,
            notes="Capped at ₹78,000 above 3 kW (open-ended band)",
        ),
    ]


def seed_subsidy_scheme(force: bool = False) -> int:
    """
    Insert current open-ended PM Surya Ghar slabs if missing.

    Returns number of rows inserted.

    How to add a new scheme revision
    --------------------------------
    Do **not** edit existing rows. Insert a new set of slabs with a later
    ``effective_from`` (and optionally set ``effective_to`` on the previous
    revision). ``calculate_subsidy`` picks the most recent effective_from on
    or before today.
    """
    existing = SubsidyScheme.query.filter_by(
        scheme_code=SCHEME_CODE, effective_from=CURRENT_EFFECTIVE_FROM
    ).count()

    if existing and not force:
        return 0

    if existing and force:
        SubsidyScheme.query.filter_by(
            scheme_code=SCHEME_CODE, effective_from=CURRENT_EFFECTIVE_FROM
        ).delete()
        db.session.commit()

    rows = current_pm_surya_ghar_slabs()
    db.session.add_all(rows)
    db.session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Residential tariff defaults — top SIGNIX target states
# Rates are indicative slab midpoints / common averages (not official quotes).
# ---------------------------------------------------------------------------

# (state, utility, rate_per_kwh, net_metering_mode, export_credit_rate, notes)
RESIDENTIAL_TARIFF_SEED = [
    (
        "Delhi",
        "BRPL / BYPL / TPDDL (mid-slab avg)",
        8.0,
        "full_offset",
        8.0,
        "NCR residential mid-slab midpoint; net metering typically banks units at retail.",
    ),
    (
        "Maharashtra",
        "MSEDCL domestic (mid-slab avg)",
        10.5,
        "full_offset",
        10.5,
        "MSEDCL domestic mid range; net metering common for rooftop solar.",
    ),
    (
        "Karnataka",
        "BESCOM / ESCOMs (mid-slab avg)",
        8.5,
        "full_offset",
        8.5,
        "BESCOM-area residential midpoint; energy banking / net metering.",
    ),
    (
        "Tamil Nadu",
        "TANGEDCO domestic (mid-slab avg)",
        7.5,
        "export_at_feed_in_rate",
        3.0,
        "TANGEDCO domestic midpoint; export often credited below full retail (FIT-style).",
    ),
    (
        "Uttar Pradesh",
        "UPPCL / discoms (mid-slab avg)",
        7.5,
        "full_offset",
        7.5,
        "UP residential mid-slab midpoint; net metering for eligible rooftop systems.",
    ),
]


def seed_tariff_table(force: bool = False) -> int:
    """Insert residential tariff rows for the top 5 target states.

    Returns number of rows inserted. If force=True, replaces existing rows
    for those states.
    """
    from app.models import TariffTable

    inserted = 0
    for state, utility, rate, mode, export_rate, notes in RESIDENTIAL_TARIFF_SEED:
        existing = TariffTable.query.filter_by(state=state).first()
        if existing and not force:
            # Backfill net-metering columns if an older seed left them null
            changed = False
            if not existing.net_metering_mode:
                existing.net_metering_mode = mode
                changed = True
            if existing.export_credit_rate is None:
                existing.export_credit_rate = export_rate
                changed = True
            if not existing.notes:
                existing.notes = notes
                changed = True
            if changed:
                inserted += 1
            continue
        if existing and force:
            db.session.delete(existing)
            db.session.flush()
        db.session.add(
            TariffTable(
                state=state,
                utility_name=utility,
                rate_per_kwh=rate,
                net_metering_mode=mode,
                export_credit_rate=export_rate,
                notes=notes,
            )
        )
        inserted += 1
    if inserted:
        db.session.commit()
    return inserted
