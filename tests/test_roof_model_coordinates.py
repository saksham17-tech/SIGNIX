from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.geo import (
    canonicalize_stored_ring,
    geojson_ring_to_latlng,
    leaflet_ring_to_geojson,
)
from app import map_snapshot, roof_ai, roof_model
from app.installer_export import build_roof_geojson
from app.models import User, UserLocation, db


INDIAN_RING_GEOJSON = [
    [77.2090, 28.6139],
    [77.2092, 28.6139],
    [77.2092, 28.6141],
    [77.2090, 28.6141],
    [77.2090, 28.6139],
]


def test_leaflet_ring_converts_to_valid_geojson_and_back():
    leaflet_ring = [[28.6139, 77.2090], [28.6141, 77.2092]]

    geojson_ring = leaflet_ring_to_geojson(leaflet_ring)

    assert geojson_ring == [
        [77.2090, 28.6139],
        [77.2092, 28.6141],
        [77.2090, 28.6139],
    ]
    assert geojson_ring_to_latlng(geojson_ring) == leaflet_ring + [leaflet_ring[0]]


def test_legacy_stored_ring_is_disambiguated_using_saved_location():
    legacy_latlng_ring = [[28.6139, 77.2090], [28.6141, 77.2092]]

    canonical = canonicalize_stored_ring(
        legacy_latlng_ring, reference_latitude=28.6140, reference_longitude=77.2091
    )

    assert canonical == [
        [77.2090, 28.6139],
        [77.2092, 28.6141],
        [77.2090, 28.6139],
    ]


def test_polygon_coords_returns_canonical_geojson_positions():
    location = SimpleNamespace(
        latitude=28.6140,
        longitude=77.2091,
        polygon_geojson={"type": "Polygon", "coordinates": [INDIAN_RING_GEOJSON]},
    )

    ring = roof_model._polygon_coords(location)

    assert ring[0] == [77.2090, 28.6139]
    assert ring[0][0] == pytest.approx(location.longitude, abs=0.001)
    assert ring[0][1] == pytest.approx(location.latitude, abs=0.001)


def test_centroid_and_bbox_use_longitude_latitude_geojson_order():
    latitude, longitude = roof_model._centroid(INDIAN_RING_GEOJSON)

    assert latitude == pytest.approx(28.6140)
    assert longitude == pytest.approx(77.2091)
    assert roof_model._bbox_from_ring(INDIAN_RING_GEOJSON) == pytest.approx(
        (77.2090, 28.6139, 77.2092, 28.6141)
    )


def test_google_solar_requests_receive_explicit_latitude_longitude():
    with patch.object(roof_model, "_solar_get", return_value={}) as solar_get:
        roof_model._building_payload(28.6139, 77.2090)
        building_params = solar_get.call_args.args[1]
        assert building_params["location.latitude"] == "28.6139000"
        assert building_params["location.longitude"] == "77.2090000"

        roof_model._data_layers(28.6139, 77.2090)
        layer_params = solar_get.call_args.args[1]
        assert layer_params["location.latitude"] == "28.6139000"
        assert layer_params["location.longitude"] == "77.2090000"


def test_local_projection_returns_east_and_north_metres():
    east, north = roof_model._lnglat_to_local_meters(
        77.2091, 28.6141, origin_lng=77.2090, origin_lat=28.6140
    )

    assert east > 0
    assert north > 0
    assert east == pytest.approx(9.78, abs=0.15)
    assert north == pytest.approx(11.13, abs=0.05)


def test_raster_mask_geometry_remains_canonical_geojson_and_closes_ring():
    open_ring = INDIAN_RING_GEOJSON[:-1]

    geometry = roof_model._mask_geometry(open_ring)

    mask_ring = geometry["coordinates"][0]
    assert geometry["type"] == "Polygon"
    assert mask_ring[0] == [77.2090, 28.6139]
    assert mask_ring[-1] == mask_ring[0]
    assert mask_ring[0][0] > mask_ring[0][1]


def test_roof_imagery_bbox_reads_canonical_geojson_order():
    bbox = roof_ai._bbox_from_polygon(INDIAN_RING_GEOJSON, pad_ratio=0)

    assert bbox == pytest.approx((77.2090, 28.6139, 77.2092, 28.6141))


def test_report_snapshot_and_installer_export_keep_canonical_order():
    polygon = {"type": "Polygon", "coordinates": [INDIAN_RING_GEOJSON]}

    snapshot_ring = map_snapshot._ring_from_geojson(polygon)
    exported = build_roof_geojson(
        {
            "address": "Delhi test roof",
            "polygon_geojson": polygon,
            "obstructions_geojson": None,
        }
    )

    assert snapshot_ring[0] == (28.6139, 77.2090)
    assert exported["features"][0]["geometry"]["coordinates"][0][0] == [
        77.2090,
        28.6139,
    ]


def test_roof_layer_uses_interior_selection_point(client, app, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Concave layer request",
            latitude=28.6140,
            longitude=77.2091,
            polygon_geojson={
                "type": "Polygon",
                "coordinates": [INDIAN_RING_GEOJSON],
            },
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    app.config["GOOGLE_SOLAR_API_KEY"] = "test-key"
    monkeypatch.setattr(roof_model, "_selection_point", lambda ring: (28.61395, 77.20905))
    requested = []
    monkeypatch.setattr(
        roof_model,
        "_data_layers",
        lambda latitude, longitude: (
            requested.append((latitude, longitude))
            or {"dsmUrl": "https://solar.example/dsm"}
        ),
    )
    provider_response = Mock(content=b"test-tiff")
    provider_response.raise_for_status.return_value = None
    monkeypatch.setattr(roof_model.requests, "get", lambda *args, **kwargs: provider_response)

    response = client.get(f"/api/roof-model/{location_id}/layer/dsm")

    assert response.status_code == 200
    assert requested == [(28.61395, 77.20905)]
