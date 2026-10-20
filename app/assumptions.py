"""
Versioned design assumptions for SIGNIX estimation.

Each assumption is a small record with:
  - value: numeric value used by the engine
  - effective_from: ISO date string when this value became active
  - source / note: short provenance for audits and the PRD data inventory

This module is the single source of truth for planning constants that used to
live as bare numbers in Config or inline in solar_logic. Behaviour must stay
identical to the pre-refactor defaults unless a new version is deliberately
introduced later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Assumption:
    """One versioned design constant."""

    key: str
    value: float
    effective_from: str  # YYYY-MM-DD
    source: str
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "effective_from": self.effective_from,
            "source": self.source,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Current active set. Financial assumptions added in v2 deliberately make
# estimates saved under v1 stale rather than silently reinterpreting them.
# effective_from is the prototype baseline date used across the codebase.
# ---------------------------------------------------------------------------

_EFFECTIVE = "2025-01-01"

AREA_PER_KWP = Assumption(
    key="area_per_kwp",
    value=10.0,
    effective_from=_EFFECTIVE,
    source="India PV planning practice / prototype default",
    note="~1 kWp per 10 m² usable shadow-free roof. Same as Config.SQM_PER_KWP.",
)

PERFORMANCE_RATIO = Assumption(
    key="performance_ratio",
    value=0.80,
    effective_from=_EFFECTIVE,
    source="Industry planning range 0.75–0.85; midpoint used as default",
    note=(
        "Base PR includes inverter, mismatch, wiring, availability and an "
        "implicit soiling allowance. Explicit soiling_loss_pct is tracked "
        "separately as a placeholder and is not yet subtracted again."
    ),
)

DEGRADATION_PCT = Assumption(
    key="degradation_pct",
    value=0.005,
    effective_from=_EFFECTIVE,
    source="Module warranty norms (~0.5%/year linear)",
    note="Applied year-on-year in 25-year cashflow and lifetime kWh.",
)

OM_PERCENT = Assumption(
    key="om_percent",
    value=0.012,
    effective_from=_EFFECTIVE,
    source="Industry planning figures (~1–1.5% of system cost / year)",
    note="Annual O&M as fraction of installed system cost. Same as Config.OM_PERCENT_PER_YEAR.",
)

SOILING_LOSS_PCT = Assumption(
    key="soiling_loss_pct",
    value=0.0,
    effective_from=_EFFECTIVE,
    source="Placeholder — soiling currently embedded in PERFORMANCE_RATIO",
    note=(
        "Explicit soiling factor reserved for a future split of PR. Value 0.0 "
        "preserves current generation outputs (no double-counting)."
    ),
)

CO2_TONS_PER_KW_YEAR = Assumption(
    key="co2_tons_per_kw_year",
    value=1.4,
    effective_from=_EFFECTIVE,
    source="Deprecated Phase 11 prototype factor",
    note="Historical compatibility constant only; v2 uses modeled energy and the CEA grid factor.",
)

GRID_EMISSIONS_KG_CO2_PER_KWH = Assumption(
    key="grid_emissions_kg_co2_per_kwh",
    value=0.710,
    effective_from="2026-10-19",
    source="Central Electricity Authority CO2 Baseline Database v22.0, FY 2024-25",
    note=(
        "India-grid weighted-average emission rate including renewable generation "
        "and imports. Applied to modeled annual energy; this is a planning estimate, "
        "not a measured or location-specific reduction."
    ),
)

DISCOUNT_RATE = Assumption(
    key="discount_rate",
    value=0.08,
    effective_from="2026-10-19",
    source="SIGNIX homeowner planning assumption",
    note=(
        "Nominal annual discount rate used for NPV, discounted payback and discounted "
        "LCOE. It is a scenario assumption, not a quoted financing rate."
    ),
)

ANNUAL_TARIFF_ESCALATION = Assumption(
    key="annual_tariff_escalation",
    value=0.04,
    effective_from=_EFFECTIVE,
    source="India retail tariff trend planning figure (~3–6%/yr); midpoint 4%",
    note=(
        "Compounded year-on-year inside the 25-year cashflow loop. "
        "Used for nominal payback; simple payback still uses a flat year-1 tariff."
    ),
)

PRACTICAL_MAX_KWP = Assumption(
    key="practical_max_kwp",
    value=50.0,
    effective_from=_EFFECTIVE,
    source="Domestic / MSME planning ceiling used to bound sizing UI",
    note=(
        "Caps sizing max_kw so a mis-scaled polygon cannot show multi-MW systems. "
        "Roof capacity is still reported separately when larger."
    ),
)

MODULE_WATTAGE = Assumption(
    key="module_wattage",
    value=545.0,
    effective_from=_EFFECTIVE,
    source="Common mono-PERC / TOPCon module datasheet range 540–550 W; midpoint 545 W",
    note=(
        "Used only for approximate panel_count = ceil(system_kW * 1000 / module_W). "
        "Not a layout or packing calculation."
    ),
)


# Registry of all current assumptions (for reporting / admin later)
ASSUMPTIONS_V1: Dict[str, Assumption] = {
    AREA_PER_KWP.key: AREA_PER_KWP,
    PERFORMANCE_RATIO.key: PERFORMANCE_RATIO,
    DEGRADATION_PCT.key: DEGRADATION_PCT,
    OM_PERCENT.key: OM_PERCENT,
    SOILING_LOSS_PCT.key: SOILING_LOSS_PCT,
    GRID_EMISSIONS_KG_CO2_PER_KWH.key: GRID_EMISSIONS_KG_CO2_PER_KWH,
    DISCOUNT_RATE.key: DISCOUNT_RATE,
    ANNUAL_TARIFF_ESCALATION.key: ANNUAL_TARIFF_ESCALATION,
    MODULE_WATTAGE.key: MODULE_WATTAGE,
    PRACTICAL_MAX_KWP.key: PRACTICAL_MAX_KWP,
}

ASSUMPTIONS_VERSION = "v2"
ASSUMPTIONS_VERSION_DATE = "2026-10-19"

# Shared copy for all monetary surfaces (API, web UI, PDF). Do not duplicate.
MONETARY_DISCLAIMER = "Indicative only — verify with DISCOM / vendor"

# Subsidy-specific copy — keep identical across API, web UI, and PDF.
SUBSIDY_CAVEAT = (
    "Central subsidy estimate — final amount subject to MNRE / DISCOM approval"
)
SUBSIDY_ELIGIBILITY_ASSUMPTION = (
    "Assumes residential, grid-connected system"
)


def get_assumption(key: str, on_date: Optional[date] = None) -> Assumption:
    """
    Return the active Assumption for key.

    on_date is reserved for future multi-version lookup; currently only v1
    exists, so the single record is returned regardless of date.
    """
    try:
        return ASSUMPTIONS_V1[key]
    except KeyError as exc:
        raise KeyError(f"Unknown assumption key: {key}") from exc


def get_value(key: str, default: Optional[float] = None) -> float:
    """Convenience: numeric value for key, or default if key missing."""
    try:
        return float(ASSUMPTIONS_V1[key].value)
    except KeyError:
        if default is not None:
            return float(default)
        raise


def assumptions_snapshot() -> Dict[str, Any]:
    """Serializable snapshot of the active assumption set (for extras/PDF)."""
    return {
        "version": ASSUMPTIONS_VERSION,
        "effective_from": ASSUMPTIONS_VERSION_DATE,
        "items": {k: a.as_dict() for k, a in ASSUMPTIONS_V1.items()},
    }
