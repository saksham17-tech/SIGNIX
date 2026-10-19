import math
from unittest.mock import Mock, patch

import pytest

from app import solar_logic

EARTH_RADIUS_M = 6371008.8


@pytest.fixture(autouse=True)
def isolate_irradiance_cache(monkeypatch):
    monkeypatch.setattr(solar_logic, "get_cached_irradiance", lambda lat, lng: None)
    monkeypatch.setattr(
        solar_logic,
        "set_cached_irradiance",
        lambda lat, lng, data, **kwargs: None,
    )


def meters_to_degrees(meters):
    return math.degrees(meters / EARTH_RADIUS_M)


def test_mock_irradiance_varies_by_latitude_and_longitude():
    delhi = solar_logic._mock_irradiance_profile(28.6139, 77.2090)
    shillong = solar_logic._mock_irradiance_profile(25.5788, 91.8933)
    same_latitude_different_longitude = solar_logic._mock_irradiance_profile(
        28.6139, 91.8933
    )
    same_longitude_different_latitude = solar_logic._mock_irradiance_profile(
        25.5788, 77.2090
    )

    assert delhi != shillong
    assert delhi["Jan"] != shillong["Jan"]
    assert delhi != same_latitude_different_longitude
    assert delhi != same_longitude_different_latitude


def rating_estimate(**overrides):
    estimate = {
        "payback_years": 4,
        "orientation_factor": 1.0,
        "usable_area_sqm": 12,
        "system_size": 1,
        "net_investment": 20000,
        "cashflow_25yr": [100000],
        "co2_reduction_tons": 3,
        "irradiance_source": "pvgis",
        "inverter_type": "off-grid",
        "battery_kwh": 10,
    }
    estimate.update(overrides)
    return estimate


def test_suitability_rating_excellent_everything():
    rating = solar_logic.calculate_suitability_rating(rating_estimate())

    assert rating["overall_viability"]["tier"] == "excellent"
    assert rating["overall_viability"]["score"] == 100
    assert rating["data_confidence"] == {"tier": "high", "score": 100}


def test_suitability_rating_poor_everything():
    rating = solar_logic.calculate_suitability_rating(
        rating_estimate(
            payback_years=15,
            orientation_factor=0.65,
            usable_area_sqm=3,
            system_size=1,
            net_investment=100000,
            cashflow_25yr=[-10000],
            co2_reduction_tons=0.1,
            irradiance_source="mock_fallback",
            inverter_type="on-grid",
            battery_kwh=0,
        )
    )

    assert rating["overall_viability"]["tier"] == "poor"
    assert rating["data_confidence"] == {"tier": "low", "score": 60}


def test_suitability_rating_marks_not_viable_financial_case():
    rating = solar_logic.calculate_suitability_rating(
        rating_estimate(net_investment=100000, cashflow_25yr=[0])
    )

    financial = rating["factors"]["financial_viability"]
    assert financial["ratio"] == 1.0
    assert financial["tier"] == "not_viable"
    assert financial["score"] == 0


def test_suitability_rating_distinguishes_oversized_and_undersized_roofs():
    oversized = solar_logic.calculate_suitability_rating(
        rating_estimate(usable_area_sqm=80, system_size=1)
    )
    undersized = solar_logic.calculate_suitability_rating(
        rating_estimate(usable_area_sqm=5, system_size=1)
    )

    assert oversized["factors"]["roof_fit"]["tier"] == "fair"
    assert oversized["factors"]["roof_fit"]["score"] == 50
    assert undersized["factors"]["roof_fit"]["tier"] == "poor"
    assert undersized["factors"]["roof_fit"]["score"] == 25


def test_suitability_rating_priority_changes_weighting():
    estimate = rating_estimate(
        payback_years=4,
        co2_reduction_tons=0.3,
        inverter_type="on-grid",
        battery_kwh=0,
    )

    payback_rating = solar_logic.calculate_suitability_rating(
        estimate, user_priority="fastest_payback"
    )
    environmental_rating = solar_logic.calculate_suitability_rating(
        estimate, user_priority="environmental_impact"
    )

    assert (
        payback_rating["overall_viability"]["score"]
        > environmental_rating["overall_viability"]["score"]
    )
    assert payback_rating["weights"]["payback"] == 40
    assert environmental_rating["weights"]["co2_reduction"] == 50


def test_calculate_polygon_area_sqm_rectangle():
    width_m = 20.0
    height_m = 10.0
    width_degrees = meters_to_degrees(width_m)
    height_degrees = meters_to_degrees(height_m)
    coordinates = [
        [0.0, 0.0],
        [0.0, width_degrees],
        [height_degrees, width_degrees],
        [height_degrees, 0.0],
    ]

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert area == pytest.approx(width_m * height_m, abs=0.05)


def test_calculate_polygon_area_sqm_irregular_polygon():
    coordinates_in_meters = [
        (0.0, 0.0),
        (30.0, 0.0),
        (45.0, 15.0),
        (20.0, 30.0),
        (0.0, 20.0),
    ]
    coordinates = [
        [meters_to_degrees(north), meters_to_degrees(east)]
        for north, east in coordinates_in_meters
    ]

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert area == pytest.approx(950.0, abs=0.1)


@pytest.mark.parametrize(
    "coordinates",
    [
        [[0.0, 0.0], [0.0, 0.001]],
        [[0.0, 0.0], [0.0, 0.001], [0.0, 0.002]],
        [[0.0, 0.0], [0.0, 0.001], [0.0, 0.001], [0.0, 0.0]],
    ],
)
def test_calculate_polygon_area_sqm_degenerate_polygon_returns_zero(coordinates):
    assert solar_logic.calculate_polygon_area_sqm(coordinates) == 0.0


def test_calculate_polygon_area_sqm_cross_checks_both_methods(monkeypatch):
    coordinates = [[0.0, 0.0], [0.0, 0.001], [0.001, 0.001]]
    calls = []

    def fake_local_area(points):
        calls.append("local")
        return 100.0

    def fake_spherical_area(points):
        calls.append("spherical")
        return 120.0

    monkeypatch.setattr(solar_logic, "_area_local_tangent_sqm", fake_local_area)
    monkeypatch.setattr(solar_logic, "_area_spherical_excess_sqm", fake_spherical_area)

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert calls == ["local", "spherical"]
    assert area == 120.0


def test_zero_or_tiny_roof_is_not_given_fictitious_minimum_capacity():
    empty = solar_logic.compute_system_sizing(usable_area_sqm=0)
    tiny = solar_logic.compute_system_sizing(usable_area_sqm=2)

    assert empty["roof_capacity_kw"] == 0
    assert empty["recommended_kw"] == 0
    assert empty["max_kw"] == 0
    assert tiny["roof_capacity_kw"] == pytest.approx(0.2)
    assert tiny["recommended_kw"] == pytest.approx(0.2)
    assert tiny["recommended_kw"] <= tiny["roof_capacity_kw"]


def test_emi_matches_independent_reducing_balance_calculation():
    principal = 100_000
    annual_rate_pct = 12
    tenure_years = 1
    monthly_rate = annual_rate_pct / 100 / 12
    months = tenure_years * 12
    expected = (
        principal
        * monthly_rate
        * (1 + monthly_rate) ** months
        / ((1 + monthly_rate) ** months - 1)
    )

    result = solar_logic.calculate_emi(principal, annual_rate_pct, tenure_years)

    assert result["monthly_emi"] == pytest.approx(expected, abs=0.01)
    assert result["total_payment"] == pytest.approx(
        round(result["monthly_emi"] * months, 2)
    )
    assert result["total_interest"] == pytest.approx(
        result["total_payment"] - principal
    )


def test_calculate_polygon_area_sqm_uses_local_area_when_methods_agree():
    coordinates = [[0.0, 0.0], [0.0, 0.001], [0.001, 0.001]]
    points = solar_logic._normalize_ring(coordinates)
    local_area = solar_logic._area_local_tangent_sqm(points)
    spherical_area = solar_logic._area_spherical_excess_sqm(points)

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert abs(local_area - spherical_area) / max(local_area, spherical_area) < 0.02
    assert area == pytest.approx(local_area, abs=0.01)


def test_fetch_solar_data_uses_nasa_power_when_successful():
    irradiance = {"Jan": 5.0}
    temperature = {"Jan": 25.0}

    with patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(irradiance, temperature, "nasa_power"),
    ) as nasa_fetch, patch.object(solar_logic, "fetch_irradiance_pvgis") as pvgis_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == (irradiance, temperature, "nasa_power", "high")
    nasa_fetch.assert_called_once_with(28.6, 77.2)
    pvgis_fetch.assert_not_called()


def test_fetch_solar_data_tries_pvgis_when_nasa_power_fails():
    irradiance = {"Jan": 4.5}

    with patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(None, None, None),
    ) as nasa_fetch, patch.object(
        solar_logic,
        "fetch_irradiance_pvgis",
        return_value=(irradiance, "pvgis"),
    ) as pvgis_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == (irradiance, None, "pvgis", "high")
    nasa_fetch.assert_called_once_with(28.6, 77.2)
    pvgis_fetch.assert_called_once_with(28.6, 77.2, 2.0)


def test_fetch_solar_data_uses_mock_profile_when_external_fetches_fail():
    mock_profile = {"Jan": 5.2, "Feb": 5.8}

    with patch.object(
        solar_logic, "get_cached_irradiance", return_value=None
    ), patch.object(
        solar_logic.requests, "get", side_effect=RuntimeError("network unavailable")
    ) as http_get, patch.object(
        solar_logic, "_mock_irradiance_profile", return_value=mock_profile
    ) as mock_profile_fetch, patch.object(
        solar_logic, "set_cached_irradiance"
    ) as cache_writer:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == (mock_profile, None, "mock_fallback", "low")
    # NASA always hits HTTP; PVGIS may fail earlier (e.g. missing pvlib) without a second GET.
    assert http_get.call_count >= 1
    mock_profile_fetch.assert_called_once_with(28.6, 77.2)
    cache_writer.assert_called_once_with(
        28.6,
        77.2,
        {
            "irradiance": mock_profile,
            "temperature": None,
            "source": "mock_fallback",
            "profile_kind": "daily_horizontal_irradiance_kwh_per_m2",
            "schema_version": "2.0",
        },
        ttl_hours=1,
    )


def test_fetch_solar_data_returns_cached_result_without_external_fetches(monkeypatch):
    cached = {
        "irradiance": {"Jan": 5.0},
        "temperature": {"Jan": 25.0},
        "source": "cached",
    }
    monkeypatch.setattr(solar_logic, "get_cached_irradiance", lambda lat, lng: cached)
    with patch.object(
        solar_logic, "fetch_irradiance_nasa_power"
    ) as nasa_fetch, patch.object(solar_logic, "fetch_irradiance_pvgis") as pvgis_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == ({"Jan": 5.0}, {"Jan": 25.0}, "cached", "low")
    nasa_fetch.assert_not_called()
    pvgis_fetch.assert_not_called()


def test_fetch_solar_data_caches_successful_external_result(monkeypatch):
    irradiance = {"Jan": 5.0}
    temperature = {"Jan": 25.0}
    set_cache = patch.object(solar_logic, "set_cached_irradiance")

    monkeypatch.setattr(solar_logic, "get_cached_irradiance", lambda lat, lng: None)
    with patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(irradiance, temperature, "nasa_power"),
    ), set_cache as cache_writer:
        result = solar_logic.fetch_solar_data(28.6, 77.2)

    assert result == (irradiance, temperature, "nasa_power", "high")
    cache_writer.assert_called_once_with(
        28.6,
        77.2,
        {
            "irradiance": irradiance,
            "temperature": temperature,
            "source": "nasa_power",
            "profile_kind": "daily_horizontal_irradiance_kwh_per_m2",
            "schema_version": "2.0",
        },
        ttl_days=30,
    )


def test_pvgis_preserves_monthly_specific_yield_units():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "outputs": {
            "monthly": {
                "fixed": [
                    {"month": month, "E_m": 200.0 + month}
                    for month in range(1, 13)
                ]
            }
        }
    }
    with patch.object(solar_logic.requests, "get", return_value=response):
        profile, source = solar_logic.fetch_irradiance_pvgis(
            28.6139, 77.2090, peak_power_kw=2.0
        )

    assert source == "pvgis"
    assert profile["Jan"] == 100.5
    assert profile["Dec"] == 106.0
    assert response.json.call_count == 1


def test_legacy_pvgis_cache_is_not_reinterpreted_with_new_units():
    legacy = {
        "irradiance": {month: 4.5 for month in solar_logic.MONTH_NAMES},
        "temperature": None,
        "source": "pvgis",
    }
    nasa_profile = {month: 5.0 for month in solar_logic.MONTH_NAMES}
    with patch.object(
        solar_logic, "get_cached_irradiance", return_value=legacy
    ), patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(nasa_profile, None, "nasa_power"),
    ) as nasa_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2)

    assert result == (nasa_profile, None, "nasa_power", "high")
    nasa_fetch.assert_called_once_with(28.6, 77.2)


def test_saved_calculation_is_marked_stale_when_model_assumptions_change(app, monkeypatch):
    from app.models import UserLocation, db

    with app.app_context():
        location = UserLocation(
            user_id=1,
            address="Versioned calculation",
            latitude=28.6139,
            longitude=77.2090,
            polygon_geojson={
                "type": "Polygon",
                "coordinates": [[
                    [77.2090, 28.6139], [77.2092, 28.6139],
                    [77.2092, 28.6141], [77.2090, 28.6139],
                ]],
            },
            system_size=1.0,
            annual_generation=1400.0,
            extras={
                "assumption_fingerprint": solar_logic.current_assumption_fingerprint(),
                "surface_generation": {"annual_generation_kwh": 1400.0},
                "surface_generation_status": "current",
            },
        )
        db.session.add(location)
        db.session.commit()
        assert location.to_dict()["calculation_status"] == "current"

        monkeypatch.setattr(solar_logic, "CALCULATION_MODEL_VERSION", "test-change")
        serialized = location.to_dict()
        assert serialized["calculation_status"] == "stale_assumptions"
        assert serialized["extras"]["surface_generation_status"] == "stale"
        assert location.extras["surface_generation_status"] == "current"


def test_degradation_and_financials_use_year_one_generation_once():
    annual_generation = 1460.0
    financials = solar_logic.calculate_financials(
        system_size_kw=1.0,
        annual_generation_kwh=annual_generation,
        tariff_per_kwh=8.0,
        property_type="residential",
        load_profile="flat",
        net_metering_mode="export_at_tariff",
    )

    assert financials["annual_savings"] == pytest.approx(annual_generation * 8.0)
    assert financials["generation_25yr"][0] == annual_generation
    assert financials["generation_25yr"][1] == pytest.approx(
        annual_generation * (1 - 0.005), abs=0.1
    )
    independently_compounded = sum(
        annual_generation * ((1 - 0.005) ** year) for year in range(25)
    )
    assert financials["lifetime_kwh"] == pytest.approx(
        independently_compounded, abs=0.5
    )


def test_calculate_subsidy_zero_kw_is_zero():
    """0 kW must yield ₹0 — empty system is not subsidy-eligible."""
    assert solar_logic.calculate_subsidy(0) == 0.0
    assert solar_logic.calculate_subsidy(0.0, "on-grid", "residential") == 0.0


def test_calculate_subsidy_exactly_2kw_slab_boundary():
    """Exactly 2 kW sits at the end of the 0–2 kW band: 2 × ₹30,000 = ₹60,000."""
    assert solar_logic.calculate_subsidy(2.0, "on-grid", "residential") == 60000.0
    assert solar_logic.calculate_subsidy(2, "hybrid", "residential") == 60000.0


def test_calculate_subsidy_exactly_3kw_slab_boundary():
    """Exactly 3 kW sits at the end of the 2–3 kW band: ₹60,000 + ₹18,000 = ₹78,000."""
    assert solar_logic.calculate_subsidy(3.0, "on-grid", "residential") == 78000.0
    assert solar_logic.calculate_subsidy(3, "hybrid", "residential") == 78000.0


def test_calculate_subsidy_above_cap_is_capped():
    """Sizes well above 3 kW stay at the ₹78,000 central CFA ceiling."""
    assert solar_logic.calculate_subsidy(5.0, "on-grid", "residential") == 78000.0
    assert solar_logic.calculate_subsidy(10.0, "on-grid", "residential") == 78000.0
    assert solar_logic.calculate_subsidy(50.0, "on-grid", "residential") == 78000.0


def test_calculate_subsidy_eligibility_zeroes_commercial_and_off_grid():
    """Commercial / off-grid systems are not eligible — subsidy must be ₹0."""
    assert solar_logic.calculate_subsidy(3.0, "on-grid", "commercial") == 0.0
    assert solar_logic.calculate_subsidy(3.0, "off-grid", "residential") == 0.0
    assert solar_logic.calculate_subsidy(2.0, "offgrid", "residential") == 0.0
    assert solar_logic.calculate_subsidy(5.0, "on-grid", "industrial") == 0.0


def test_tariff_escalation_changes_cashflow_and_dual_payback(monkeypatch):
    """Acceptance: escalating tariff changes 25yr cashflow vs flat; dual payback labelled.

    Uses a known net investment / year-1 savings pair so simple payback is exact
    and nominal payback (with 4% escalation) is strictly shorter.
    """
    from app import assumptions

    # Force known escalation / degradation for stability
    monkeypatch.setitem(
        assumptions.ASSUMPTIONS_V1,
        "annual_tariff_escalation",
        assumptions.Assumption(
            key="annual_tariff_escalation",
            value=0.04,
            effective_from="2025-01-01",
            source="test",
        ),
    )
    monkeypatch.setitem(
        assumptions.ASSUMPTIONS_V1,
        "degradation_pct",
        assumptions.Assumption(
            key="degradation_pct",
            value=0.0,
            effective_from="2025-01-01",
            source="test",
        ),
    )

    fin = solar_logic.calculate_financials(
        system_size_kw=2.0,
        annual_generation_kwh=3000.0,
        tariff_per_kwh=10.0,
        battery_kwh=0.0,
        inverter_type="on-grid",
        property_type="residential",
    )
    assert fin["tariff_escalation_pct"] == 4.0
    assert fin["simple_payback_years"] is not None
    assert fin["nominal_payback_years"] is not None
    # With positive escalation and zero degradation, nominal payback <= simple
    assert fin["nominal_payback_years"] <= fin["simple_payback_years"]
    # Simple payback = net / year-1 annual savings
    expected_simple = round(fin["net_investment"] / fin["annual_savings"], 1)
    assert fin["simple_payback_years"] == expected_simple

    # Flat escalation (0%) cashflow must differ from 4% on later years
    monkeypatch.setitem(
        assumptions.ASSUMPTIONS_V1,
        "annual_tariff_escalation",
        assumptions.Assumption(
            key="annual_tariff_escalation",
            value=0.0,
            effective_from="2025-01-01",
            source="test",
        ),
    )
    fin_flat = solar_logic.calculate_financials(
        system_size_kw=2.0,
        annual_generation_kwh=3000.0,
        tariff_per_kwh=10.0,
        battery_kwh=0.0,
        inverter_type="on-grid",
        property_type="residential",
    )
    assert fin_flat["cashflow_25yr"][-1] != fin["cashflow_25yr"][-1]
    assert fin_flat["cashflow_25yr"][-1] < fin["cashflow_25yr"][-1]


def test_net_metering_modes_produce_different_savings():
    """Same generation/tariff/profile → three modes must yield distinct annual savings."""
    gen = 5000.0
    tariff = 10.0
    fit = 3.0
    # evening-heavy → more export, amplifies mode differences
    kwargs = dict(
        system_size_kw=3.0,
        annual_generation_kwh=gen,
        tariff_per_kwh=tariff,
        battery_kwh=0.0,
        load_profile="evening_heavy",
        feed_in_tariff=fit,
    )
    full = solar_logic.calculate_financials(**kwargs, net_metering_mode="full_offset")
    retail = solar_logic.calculate_financials(**kwargs, net_metering_mode="export_at_tariff")
    feed = solar_logic.calculate_financials(**kwargs, net_metering_mode="export_at_feed_in_rate")

    assert full["annual_savings"] < retail["annual_savings"]
    assert full["annual_savings"] < feed["annual_savings"]
    assert feed["annual_savings"] < retail["annual_savings"]
    assert full["net_metering_mode"] == "full_offset"
    assert retail["net_metering_mode"] == "export_at_tariff"
    assert feed["net_metering_mode"] == "export_at_feed_in_rate"
    # export_at_tariff values all kWh at retail
    assert retail["annual_savings"] == round(gen * tariff, 2)


def test_load_profile_presets_change_base_self_consumption():
    day_sc, day_key, _ = solar_logic.self_consumption_fraction("day_heavy", 0)
    flat_sc, flat_key, _ = solar_logic.self_consumption_fraction("flat", 0)
    eve_sc, eve_key, _ = solar_logic.self_consumption_fraction("evening_heavy", 0)
    assert day_key == "day_heavy" and flat_key == "flat" and eve_key == "evening_heavy"
    assert day_sc > flat_sc > eve_sc
    # Battery boost still increases SC on each preset
    day_batt, _, _ = solar_logic.self_consumption_fraction("day_heavy", 5.0)
    assert day_batt > day_sc
