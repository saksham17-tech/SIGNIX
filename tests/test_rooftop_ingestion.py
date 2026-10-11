import uuid
from datetime import datetime, timedelta

import pytest
import requests

from app.models import RooftopDesign, User, UserLocation, db
from app.services import rooftop_ingestion_service as ingestion


ROOF = {
    "type": "Polygon",
    "coordinates": [[
        [77.2090, 28.6139],
        [77.2092, 28.6139],
        [77.2092, 28.6141],
        [77.2090, 28.6141],
        [77.2090, 28.6139],
    ]],
}


def _location(app):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Delhi ingestion roof",
            latitude=28.6140,
            longitude=77.2091,
            polygon_geojson=ROOF,
            system_size=4.25,
            annual_generation=6800.0,
            monthly_savings=4100.0,
        )
        db.session.add(location)
        db.session.commit()
        return location.id


def _building_payload():
    return {
        "name": "buildings/test",
        "center": {"latitude": 28.6140, "longitude": 77.2091},
        "boundingBox": {
            "sw": {"latitude": 28.6139, "longitude": 77.2090},
            "ne": {"latitude": 28.6141, "longitude": 77.2092},
        },
        "imageryDate": {"year": 2025, "month": 3, "day": 10},
        "imageryQuality": "HIGH",
        "solarPotential": {
            "wholeRoofStats": {"areaMeters2": 92.5},
            "maxArrayPanelsCount": 18,
            "panelWidthMeters": 1.134,
            "panelHeightMeters": 1.722,
            "maxSunshineHoursPerYear": 1720,
            "roofSegmentStats": [{
                "pitchDegrees": 14,
                "azimuthDegrees": 182,
                "planeHeightAtCenterMeters": 8.2,
                "center": {"latitude": 28.6140, "longitude": 77.2091},
                "boundingBox": {
                    "sw": {"latitude": 28.61392, "longitude": 77.20902},
                    "ne": {"latitude": 28.61408, "longitude": 77.20918},
                },
                "stats": {"areaMeters2": 46.2, "groundAreaMeters2": 44.9},
            }],
        },
    }


def _post(client, location_id, **payload):
    body = {"providers": ["google_solar"]}
    body.update(payload)
    return client.post(
        f"/api/locations/{location_id}/rooftop-design/ingest", json=body
    )


def test_building_insights_success_is_normalized_and_persisted(
    client, app, monkeypatch
):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: _building_payload())

    response = _post(client, location_id)

    assert response.status_code == 200
    data = response.get_json()
    source = data["sources"][0]
    assert source["status"] == "available"
    assert source["analysis"]["roof"]["segments"][0]["pitch_degrees"] == 14
    assert source["analysis"]["authoritative_footprint_geojson"] == ROOF
    assert data["design"]["authoritative"]["roof_boundary"] == ROOF


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (RuntimeError("no supported building at this location"), "not_found"),
        (RuntimeError("outside geographic coverage"), "outside_coverage"),
        (RuntimeError("Google Solar API access denied (HTTP 403)"), "authentication_error"),
        (RuntimeError("quota exceeded (HTTP 429)"), "quota_error"),
        (requests.Timeout("provider timed out"), "timeout"),
    ],
)
def test_google_provider_errors_have_machine_readable_status(
    client, app, monkeypatch, exception, expected
):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model

    def fail(lat, lng):
        raise exception

    monkeypatch.setattr(roof_model, "_building_payload", fail)
    response = _post(client, location_id)
    assert response.status_code == 200
    assert response.get_json()["sources"][0]["status"] == expected


def test_missing_solar_key_is_persisted_without_provider_call(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = None
    app.config["GOOGLE_MAPS_API_KEY"] = None
    from app import roof_model
    called = []
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: called.append(1))

    response = _post(client, location_id)

    assert response.get_json()["sources"][0]["status"] == "missing_api_key"
    assert called == []


def test_malformed_building_response(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: {"center": {}})

    response = _post(client, location_id)

    assert response.get_json()["sources"][0]["status"] == "malformed_response"


def test_data_layers_and_dsm_are_normalized_after_building_success(
    client, app, monkeypatch
):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: _building_payload())
    monkeypatch.setattr(
        roof_model,
        "_data_layers",
        lambda lat, lng: {
            "dsmUrl": "https://solar.example/dsm?t=1&key=secret",
            "rgbUrl": "https://solar.example/rgb",
            "maskUrl": "https://solar.example/mask",
            "annualFluxUrl": "https://solar.example/flux",
            "imageryQuality": "HIGH",
            "imageryDate": {"year": 2025},
        },
    )
    monkeypatch.setattr(
        roof_model,
        "_dsm_grid",
        lambda location, ring, layers: {
            "rows": 1, "cols": 2, "x": [-1.0, 1.0], "z": [0.0],
            "heights": [0.0, 1.2],
        },
    )
    monkeypatch.setattr(
        roof_model,
        "_flux_grid",
        lambda location, ring, layers: {
            "rows": 1, "cols": 2, "x": [-1.0, 1.0], "z": [0.0],
            "relativeFlux": [0.4, 1.0],
        },
    )

    response = _post(client, location_id, include_data_layers=True)

    sources = response.get_json()["sources"]
    layers = next(item for item in sources if item["data_type"] == "data_layers")
    assert layers["status"] == "available"
    assert layers["analysis"]["dsm_grid"]["heights"] == [0.0, 1.2]
    assert "key=" not in layers["analysis"]["layers"]["dsm"]


def test_gemini_success_is_advisory_and_cached(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GEMINI_API_KEY"] = "test-key"
    from app import roof_ai
    calls = []

    def analyze(ring):
        calls.append(ring)
        return {
            "roof_type": "gable",
            "pitch_degrees": 18,
            "confidence": 0.82,
            "roof_planes": [],
            "obstructions": [],
            "imagery_provider": "Esri World Imagery",
        }

    monkeypatch.setattr(roof_ai, "analyze_polygon", analyze)
    first = client.post(
        f"/api/locations/{location_id}/rooftop-design/ingest",
        json={"providers": ["gemini"]},
    ).get_json()
    second = client.post(
        f"/api/locations/{location_id}/rooftop-design/ingest",
        json={"providers": ["gemini"]},
    ).get_json()

    assert first["sources"][0]["status"] == "available"
    assert second["sources"][0]["cached"] is True
    assert len(calls) == 1
    assert second["design"]["authoritative"]["roof_metadata"]["roof_type"] is None


def test_gemini_unavailable_is_normalized(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GEMINI_API_KEY"] = "test-key"
    from app import roof_ai
    monkeypatch.setattr(
        roof_ai,
        "analyze_polygon",
        lambda ring: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )

    response = client.post(
        f"/api/locations/{location_id}/rooftop-design/ingest",
        json={"providers": ["gemini"]},
    )

    assert response.get_json()["sources"][0]["status"] == "unavailable"


def test_cached_google_result_avoids_second_provider_call(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    calls = []

    def building(lat, lng):
        calls.append((lat, lng))
        return _building_payload()

    monkeypatch.setattr(roof_model, "_building_payload", building)
    assert _post(client, location_id).status_code == 200
    second = _post(client, location_id).get_json()

    assert len(calls) == 1
    assert second["used_cache"] is True
    assert second["sources"][0]["cached"] is True


def test_failed_refresh_preserves_previous_usable_source(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: _building_payload())
    assert _post(client, location_id).status_code == 200
    monkeypatch.setattr(
        roof_model,
        "_building_payload",
        lambda lat, lng: (_ for _ in ()).throw(requests.Timeout("late")),
    )

    refreshed = _post(client, location_id, refresh=True).get_json()

    statuses = [
        item["availability_status"]
        for item in refreshed["design"]["advisory_sources"]
        if item["data_type"] == "building_insights"
    ]
    assert "available" in statuses
    assert "timeout" in statuses
    with app.app_context():
        design = RooftopDesign.query.filter_by(
            user_location_id=location_id, is_current=True
        ).first()
        location = db.session.get(UserLocation, location_id)
        assert ingestion.legacy_roof_model_payload(
            design, location=location
        )["available"] is True
    reused = _post(client, location_id).get_json()
    assert reused["sources"][0]["cached"] is True
    assert reused["sources"][0]["status"] == "available"


def test_authoritative_design_edit_invalidates_provider_cache(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: _building_payload())
    assert _post(client, location_id).status_code == 200

    edited_roof = {
        "type": "Polygon",
        "coordinates": [[
            [77.2090, 28.6139], [77.2093, 28.6139],
            [77.2093, 28.6141], [77.2090, 28.6141],
            [77.2090, 28.6139],
        ]],
    }
    revised = client.post(
        f"/api/locations/{location_id}/rooftop-design",
        json={"authoritative_roof_geojson": edited_roof},
    )
    assert revised.status_code == 201

    with app.app_context():
        location = db.session.get(UserLocation, location_id)
        design = RooftopDesign.query.filter_by(
            user_location_id=location_id, is_current=True
        ).first()

        payload = ingestion.legacy_roof_model_payload(design, location=location)

        assert payload["available"] is False
        assert payload["status"] == "incompatible_geometry"


def test_ingestion_uses_current_authoritative_design_boundary(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    calls = []

    def building(lat, lng):
        calls.append((lat, lng))
        payload = _building_payload()
        payload["center"] = {"latitude": lat, "longitude": lng}
        return payload

    monkeypatch.setattr(roof_model, "_building_payload", building)
    assert _post(client, location_id).status_code == 200
    edited_roof = {
        "type": "Polygon",
        "coordinates": [[
            [77.2092, 28.6139], [77.2094, 28.6139],
            [77.2094, 28.6141], [77.2092, 28.6141],
            [77.2092, 28.6139],
        ]],
    }
    revised = client.post(
        f"/api/locations/{location_id}/rooftop-design",
        json={"authoritative_roof_geojson": edited_roof},
    )
    assert revised.status_code == 201

    result = _post(client, location_id).get_json()

    assert len(calls) == 2
    assert result["used_cache"] is False
    assert result["sources"][0]["analysis"]["authoritative_footprint_geojson"] == edited_roof


def test_legacy_payload_rejects_expired_provider_data(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: _building_payload())
    assert _post(client, location_id).status_code == 200

    with app.app_context():
        location = db.session.get(UserLocation, location_id)
        design = RooftopDesign.query.filter_by(
            user_location_id=location_id, is_current=True
        ).first()
        source = next(item for item in design.sources if item.data_type == "building_insights")
        source.reusable_until = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        payload = ingestion.legacy_roof_model_payload(design, location=location)

        assert payload["available"] is False
        assert payload["status"] == "stale"


def test_roof_model_explicit_refresh_bypasses_cache(client, app, monkeypatch):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    calls = []

    def building(lat, lng):
        calls.append((lat, lng))
        return _building_payload()

    monkeypatch.setattr(roof_model, "_building_payload", building)
    assert _post(client, location_id).status_code == 200

    response = client.get(
        f"/api/roof-model/{location_id}?high_fidelity=0&refresh_provider=1"
    )

    assert response.status_code == 200
    assert response.get_json()["mode"] == "provider"
    assert len(calls) == 2


def test_ingestion_preserves_user_geometry_financials_and_coordinate_order(
    client, app, monkeypatch
):
    location_id = _location(app)
    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    from app import roof_model
    monkeypatch.setattr(roof_model, "_building_payload", lambda lat, lng: _building_payload())

    response = _post(client, location_id)
    normalized = response.get_json()["sources"][0]["analysis"]

    first = normalized["provider_building_footprint_geojson"]["coordinates"][0][0]
    assert first == [77.209, 28.6139]
    assert normalized["building_center"] == {
        "latitude": 28.614,
        "longitude": 77.2091,
    }
    with app.app_context():
        location = db.session.get(UserLocation, location_id)
        assert location.polygon_geojson == ROOF
        assert location.system_size == 4.25
        assert location.annual_generation == 6800.0
        assert location.monthly_savings == 4100.0


def test_ingestion_ownership_is_enforced(client, app):
    location_id = _location(app)
    with app.app_context():
        user = User(
            email=f"other-{uuid.uuid4().hex}@example.com",
            password_hash="unused",
            display_name="Other",
        )
        db.session.add(user)
        db.session.commit()
        other_id = user.id
    with client.session_transaction() as session:
        session["_user_id"] = str(other_id)
        session["_fresh"] = True

    assert _post(client, location_id).status_code == 404
