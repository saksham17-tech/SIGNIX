
"""Auth and ownership tests for estimate / location endpoints after CSRF hardening."""

import uuid
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import solar_logic
from app.models import User, UserLocation, db


def _login(client, email, password):
    return client.post("/login", data={"email": email, "password": password})


def _sample_payload(address="Auth test rooftop"):
    return {
        "address": address,
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "tariff_per_kwh": 8.0,
    }


def _mock_irradiance():
    return {month: 5.0 for month in solar_logic.MONTH_NAMES}


def test_unauthenticated_estimate_is_rejected(app):
    """POST /api/estimate without a session must not succeed."""
    anonymous = app.test_client()
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(_mock_irradiance(), None, "test_fixture", "low"),
    ):
        response = anonymous.post("/api/estimate", json=_sample_payload())

    # Flask-Login redirects unauthenticated users to login (302) by default.
    assert response.status_code in (401, 403, 302)
    if response.status_code == 302:
        assert "/login" in response.headers.get("Location", "")


def test_authenticated_estimate_succeeds_for_own_user(client, app):
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(_mock_irradiance(), None, "test_fixture", "low"),
    ):
        response = client.post("/api/estimate", json=_sample_payload("Own rooftop"))

    assert response.status_code == 200
    data = response.get_json()
    assert data["location_id"] > 0

    with app.app_context():
        location = db.session.get(UserLocation, data["location_id"])
        owner = User.query.filter_by(email="test@example.com").first()
        assert location is not None
        assert location.user_id == owner.id


def test_user_cannot_read_another_users_location(app):
    """User B must get 404 when fetching User A's saved location."""
    suffix = uuid.uuid4().hex[:8]
    email_a = f"owner-a-{suffix}@example.com"
    email_b = f"owner-b-{suffix}@example.com"

    with app.app_context():
        user_a = User(
            email=email_a,
            password_hash=generate_password_hash("password-a"),
            display_name="Owner A",
        )
        user_b = User(
            email=email_b,
            password_hash=generate_password_hash("password-b"),
            display_name="Owner B",
        )
        db.session.add_all([user_a, user_b])
        db.session.commit()
        location = UserLocation(
            user_id=user_a.id,
            address="A's rooftop",
            latitude=28.61,
            longitude=77.20,
            system_size=3.0,
            annual_generation=4500.0,
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    client_b = app.test_client()
    assert _login(client_b, email_b, "password-b").status_code == 302

    detail = client_b.get(f"/api/locations/{location_id}")
    assert detail.status_code == 404

    report = client_b.get(f"/report/{location_id}")
    assert report.status_code == 404

    pdf = client_b.get(f"/report/{location_id}/pdf")
    assert pdf.status_code == 404


def test_missing_report_returns_404_for_authenticated_user(client):
    response = client.get("/report/999999")

    assert response.status_code == 404


def test_user_cannot_list_another_users_locations(app):
    suffix = uuid.uuid4().hex[:8]
    email_a = f"list-a-{suffix}@example.com"
    email_b = f"list-b-{suffix}@example.com"

    with app.app_context():
        user_a = User(
            email=email_a,
            password_hash=generate_password_hash("password-a"),
            display_name="List A",
        )
        user_b = User(
            email=email_b,
            password_hash=generate_password_hash("password-b"),
            display_name="List B",
        )
        db.session.add_all([user_a, user_b])
        db.session.commit()
        db.session.add(
            UserLocation(
                user_id=user_a.id,
                address="Private roof",
                latitude=28.61,
                longitude=77.20,
                system_size=2.0,
                annual_generation=3000.0,
            )
        )
        db.session.commit()

    client_b = app.test_client()
    assert _login(client_b, email_b, "password-b").status_code == 302
    response = client_b.get("/api/locations")
    assert response.status_code == 200
    data = response.get_json()
    # Must not include the other user's location
    assert all(loc.get("address") != "Private roof" for loc in data)


def test_estimate_requires_csrf_when_enabled(app):
    """With CSRF enabled, a JSON POST without token must fail (400)."""
    client = app.test_client()
    assert _login(client, "test@example.com", "unused").status_code == 302
    app.config["WTF_CSRF_ENABLED"] = True

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(_mock_irradiance(), None, "test_fixture", "low"),
    ):
        response = client.post(
            "/api/estimate",
            json=_sample_payload("CSRF check"),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
