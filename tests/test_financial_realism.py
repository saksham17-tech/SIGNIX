"""Independent numerical references for the Phase 12 financial model."""

import pytest

from app import assumptions
from app.services.report_chart_data import build_report_chart_data
from app.solar_logic import (
    build_lifecycle_projection,
    calculate_financials,
    current_assumption_fingerprint,
    value_generation_kwh,
)


def test_self_consumption_export_and_demand_cap_reference_case():
    # 10,000 kWh generation, 40% planning share, but only 3,000 kWh demand.
    # Direct value = 3,000*₹8; export value = 7,000*₹3; total = ₹45,000.
    value = value_generation_kwh(
        10_000, 0.40, 8.0, "export_at_feed_in_rate", 3.0,
        annual_consumption_kwh=3_000,
    )
    assert value["self_consumed_kwh"] == 3_000
    assert value["exported_kwh"] == 7_000
    assert value["avoided_retail_value"] == 24_000
    assert value["export_value"] == 21_000
    assert value["annual_savings"] == 45_000
    assert value["self_consumption_basis"].endswith("estimated_demand")


def test_missing_demand_is_explicitly_an_assumption_not_measured_use():
    value = value_generation_kwh(10_000, 0.55, 8.0)
    assert value["annual_consumption_kwh"] is None
    assert value["self_consumption_basis"] == (
        "load_profile_assumption_demand_unavailable"
    )


def test_lifecycle_reference_includes_om_npv_discounted_payback_and_lcoe():
    # Independent annuity reference: ₹28,000 net/year for 5 years at 10%.
    # PV factor = 3.7907868; NPV = -100,000 + 28,000*factor = ₹6,142.03.
    projection = build_lifecycle_projection(
        100_000,
        30_000,
        0,
        2_000,
        10_000,
        years=5,
        degradation=0,
        tariff_escalation=0,
        discount_rate=0.10,
    )
    assert projection["years"][0]["net_cash_flow"] == 28_000
    assert projection["nominal_payback_years"] == 3.6
    assert projection["discounted_payback_years"] == 4.6
    assert projection["npv"] == pytest.approx(6_142.03, abs=0.02)
    # PV costs ₹107,581.57 / PV energy 37,907.87 kWh.
    assert projection["discounted_lcoe"] == pytest.approx(2.84, abs=0.01)
    assert projection["lifetime_om_cost"] == 10_000


def test_subsidy_and_om_are_each_applied_once_in_financials():
    result = calculate_financials(2.0, 3_000, 10.0)
    assert result["subsidy_amount"] == 60_000
    assert result["net_investment"] == pytest.approx(
        result["system_cost"] - 60_000
    )
    assert result["year_one_net_savings"] == pytest.approx(
        result["annual_savings"] - result["annual_om"]
    )
    assert result["lifecycle_projection"]["years"][0]["net_cash_flow"] == (
        result["year_one_net_savings"]
    )


def test_energy_based_co2_has_units_source_and_report_consistency():
    result = calculate_financials(3.0, 5_000, 8.0)
    assert result["co2_reduction_tons"] == 3.55
    factor = result["financial_provenance"]["environmental_factor"]
    assert factor["value"] == 0.710
    assert factor["units"] == "kg_CO2_per_kWh"
    assert "Central Electricity Authority" in factor["source"]

    charts = build_report_chart_data(result)
    assert charts["environment"]["annual_co2_tons"] == 3.55
    assert charts["environment"]["provenance"]["value"] == 0.710
    assert charts["financial"]["cumulative_cashflow"] == result["cashflow_25yr"]


def test_financial_assumption_change_invalidates_fingerprint(monkeypatch):
    before = current_assumption_fingerprint()
    monkeypatch.setitem(
        assumptions.ASSUMPTIONS_V1,
        "discount_rate",
        assumptions.Assumption(
            key="discount_rate",
            value=0.09,
            effective_from="2026-10-19",
            source="independent regression test",
        ),
    )
    assert current_assumption_fingerprint() != before


def test_saved_financial_status_detects_changed_generation_and_legacy(app):
    from app.models import UserLocation, db

    result = calculate_financials(2.0, 3_000, 8.0)
    with app.app_context():
        location = UserLocation(
            user_id=1,
            address="Financial provenance",
            latitude=28.6,
            longitude=77.2,
            system_size=2.0,
            annual_generation=3_000,
            system_cost=result["system_cost"],
            subsidy_amount=result["subsidy_amount"],
            battery_cost=result["battery_cost"],
            extras={
                "assumption_fingerprint": current_assumption_fingerprint(),
                "tariff_per_kwh": 8.0,
                "feed_in_tariff": result["feed_in_tariff"],
                "net_metering_mode": result["net_metering_mode"],
                "financial_provenance": result["financial_provenance"],
            },
        )
        db.session.add(location)
        db.session.commit()
        assert location.to_dict()["financial_status"] == "current"

        location.annual_generation = 2_900
        assert location.to_dict()["financial_status"] == "stale_inputs"

        location.extras = {
            "assumption_fingerprint": current_assumption_fingerprint()
        }
        assert location.to_dict()["financial_status"] == "legacy_unversioned"
