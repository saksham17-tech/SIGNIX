from unittest.mock import Mock, patch

from app.services import location_service


def _response(payload, url="https://example.test/"):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    response.url = url
    return response


def test_geocode_provider_failure_never_returns_hardcoded_location():
    with patch.object(
        location_service.requests,
        "get",
        side_effect=[_response([]), _response({"features": []})],
    ):
        result = location_service.geocode("A rooftop that is not a provider result")

    assert result["results"] == []
    assert result["source"] == "unavailable"
    assert "unavailable" in result["error"].lower()
    assert "28.6139" not in str(result)
    assert "77.209" not in str(result)


def test_geocode_preserves_provider_coordinates_and_address_metadata():
    provider_row = {
        "display_name": "Connaught Place, New Delhi, Delhi, India",
        "lat": "28.63150",
        "lon": "77.21670",
        "type": "commercial",
        "importance": 0.7,
        "address": {
            "road": "Connaught Place",
            "city": "New Delhi",
            "state": "Delhi",
            "postcode": "110001",
        },
    }
    with patch.object(
        location_service.requests,
        "get",
        return_value=_response([provider_row]),
    ):
        result = location_service.geocode("Connaught Place")

    assert result["source"] == "nominatim"
    assert result["results"][0]["lat"] == 28.6315
    assert result["results"][0]["lon"] == 77.2167
    assert result["results"][0]["state"] == "Delhi"
    assert result["results"][0]["postcode"] == "110001"


def test_parse_maps_url_preserves_latitude_longitude_order():
    with patch.object(location_service, "reverse_address", return_value="New Delhi"):
        data, status = location_service.parse_maps_url(
            "https://www.google.com/maps/@28.6315,77.2167,19z"
        )

    assert status == 200
    assert data["lat"] == 28.6315
    assert data["lon"] == 77.2167
    assert data["source"] == "maps_url"


def test_reports_navigation_has_real_authenticated_page(client):
    response = client.get("/reports")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "All saved reports" in body
    assert 'href="/reports"' in body


def test_saved_locations_navigation_has_distinct_authenticated_page(client):
    response = client.get("/saved-locations")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Saved locations" in body
    assert 'href="/saved-locations"' in body


def test_dashboard_uses_persisted_summary_values(client):
    response = client.get("/dashboard")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Your solar planning workspace" in body
    assert "Saved rooftops" in body
    assert "Planned capacity" in body


def test_landing_contains_accessible_location_states(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="location-status"' in body
    assert 'aria-live="polite"' in body
    assert 'id="search-clear-btn"' in body
    assert 'id="map-feedback"' in body
    assert 'id="cancel-draw-btn"' in body
    assert 'id="estimate-action-status"' in body
