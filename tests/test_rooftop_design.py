import uuid

import pytest
from sqlalchemy import inspect
from werkzeug.security import generate_password_hash

from app.models import RooftopDesign, User, UserLocation, db


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

MANUAL_OBSTRUCTIONS = {
    "type": "MultiPolygon",
    "coordinates": [[[
        [77.20905, 28.61395],
        [77.20908, 28.61395],
        [77.20908, 28.61398],
        [77.20905, 28.61398],
        [77.20905, 28.61395],
    ]]],
}


def _location(app, user_email="test@example.com"):
    with app.app_context():
        user = User.query.filter_by(email=user_email).first()
        location = UserLocation(
            user_id=user.id,
            address="Persistent roof",
            latitude=28.6140,
            longitude=77.2091,
            polygon_geojson=ROOF,
            obstructions_geojson=MANUAL_OBSTRUCTIONS,
            system_size=3.0,
            annual_generation=4500.0,
            monthly_savings=3000.0,
        )
        db.session.add(location)
        db.session.commit()
        return location.id


def _create_payload():
    return {
        "schema_version": "1.0",
        "review_status": "draft",
        "advisory_sources": [
            {
                "provider": "google_solar",
                "data_type": "building_insights",
                "retrieved_at": "2026-10-08T10:00:00+00:00",
                "source_version": "v1",
                "confidence": "high",
                "quality": "BASE",
                "review_status": "pending",
                "analysis_result": {
                    "suggested_roof_type": "gable",
                    "suggested_boundary": {
                        "type": "Polygon",
                        "coordinates": [[[80.0, 30.0], [80.1, 30.0], [80.0, 30.1], [80.0, 30.0]]],
                    },
                },
                "diagnostics": {"coverage": "available"},
            }
        ],
    }


def test_create_and_retrieve_rooftop_design(client, app):
    location_id = _location(app)

    created = client.post(
        f"/api/locations/{location_id}/rooftop-design", json=_create_payload()
    )

    assert created.status_code == 201
    data = created.get_json()
    assert data["revision_number"] == 1
    assert data["schema_version"] == "1.0"
    assert data["authoritative"]["roof_boundary"] == ROOF
    assert data["authoritative"]["manual_obstructions"] == MANUAL_OBSTRUCTIONS
    assert data["advisory_sources"][0]["review_status"] == "pending"

    retrieved = client.get(f"/api/locations/{location_id}/rooftop-design")
    assert retrieved.status_code == 200
    assert retrieved.get_json() == data


def test_advisory_data_does_not_replace_authoritative_data(client, app):
    location_id = _location(app)

    response = client.post(
        f"/api/locations/{location_id}/rooftop-design", json=_create_payload()
    )
    data = response.get_json()

    assert data["authoritative"]["roof_boundary"] == ROOF
    assert data["authoritative"]["roof_metadata"]["roof_type"] is None
    assert (
        data["advisory_sources"][0]["analysis_result"]["suggested_roof_type"]
        == "gable"
    )


def test_new_revision_preserves_geometry_and_accepts_explicit_metadata(client, app):
    location_id = _location(app)
    first = client.post(
        f"/api/locations/{location_id}/rooftop-design", json=_create_payload()
    ).get_json()

    second_response = client.post(
        f"/api/locations/{location_id}/rooftop-design",
        json={
            "schema_version": "1.1",
            "review_status": "reviewed",
            "roof_metadata": {
                "roof_type": "gable",
                "pitch_degrees": 18,
                "azimuth_degrees": 182,
                "height_meters": 8.5,
            },
            "accepted_roof_planes": [
                {"id": "plane-1", "acceptance": "accepted"}
            ],
        },
    )

    assert second_response.status_code == 201
    second = second_response.get_json()
    assert second["revision_number"] == 2
    assert second["schema_version"] == "1.1"
    assert second["authoritative"]["roof_boundary"] == ROOF
    assert second["authoritative"]["manual_obstructions"] == MANUAL_OBSTRUCTIONS
    assert second["authoritative"]["roof_metadata"]["roof_type"] == "gable"
    assert second["authoritative"]["roof_metadata"]["pitch_degrees"] == 18
    assert second["authoritative"]["accepted_roof_planes"][0]["id"] == "plane-1"

    with app.app_context():
        old = db.session.get(RooftopDesign, first["id"])
        current = db.session.get(RooftopDesign, second["id"])
        assert old.is_current is False
        assert current.is_current is True


@pytest.mark.parametrize("review_status", ["accepted", "rejected"])
def test_source_review_creates_revision_and_persists_decision(
    client, app, review_status
):
    location_id = _location(app)
    first = client.post(
        f"/api/locations/{location_id}/rooftop-design", json=_create_payload()
    ).get_json()
    source_id = first["advisory_sources"][0]["id"]

    reviewed_response = client.patch(
        f"/api/locations/{location_id}/rooftop-design/{first['id']}"
        f"/sources/{source_id}",
        json={
            "review_status": review_status,
            "review_state": {"accepted_candidate_ids": ["segment-1"]},
        },
    )

    assert reviewed_response.status_code == 201
    reviewed = reviewed_response.get_json()
    assert reviewed["revision_number"] == 2
    assert reviewed["advisory_sources"][0]["review_status"] == review_status
    assert reviewed["advisory_sources"][0]["review_state"] == {
        "accepted_candidate_ids": ["segment-1"]
    }
    assert reviewed["authoritative"]["roof_boundary"] == ROOF
    assert reviewed["authoritative"]["roof_metadata"]["roof_type"] is None


def test_rooftop_design_authentication_and_ownership(app):
    location_id = _location(app)
    anonymous = app.test_client()
    assert (
        anonymous.get(f"/api/locations/{location_id}/rooftop-design").status_code
        == 401
    )

    suffix = uuid.uuid4().hex[:8]
    other_email = f"other-design-{suffix}@example.com"
    with app.app_context():
        other = User(
            email=other_email,
            password_hash=generate_password_hash("password"),
            display_name="Other User",
        )
        db.session.add(other)
        db.session.commit()

    other_client = app.test_client()
    assert other_client.post(
        "/login", data={"email": other_email, "password": "password"}
    ).status_code == 302
    assert other_client.post(
        f"/api/locations/{location_id}/rooftop-design", json=_create_payload()
    ).status_code == 404
    assert other_client.get(
        f"/api/locations/{location_id}/rooftop-design"
    ).status_code == 404


def test_sqlite_schema_and_financial_estimate_are_unchanged(client, app):
    location_id = _location(app)
    with app.app_context():
        before = db.session.get(UserLocation, location_id)
        financials = (
            before.system_size,
            before.annual_generation,
            before.monthly_savings,
        )
        tables = set(inspect(db.engine).get_table_names())
        assert "rooftop_designs" in tables
        assert "rooftop_design_sources" in tables

    response = client.post(
        f"/api/locations/{location_id}/rooftop-design", json=_create_payload()
    )
    assert response.status_code == 201

    with app.app_context():
        after = db.session.get(UserLocation, location_id)
        assert (
            after.system_size,
            after.annual_generation,
            after.monthly_savings,
        ) == financials
