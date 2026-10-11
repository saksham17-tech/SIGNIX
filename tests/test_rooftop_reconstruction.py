from pathlib import Path
from types import SimpleNamespace
import math

import pytest

from app.models import User, UserLocation, db
from app.services import rooftop_reconstruction_service as reconstruction


DELHI_ROOF = {
    "type": "Polygon",
    "coordinates": [[
        [77.20900, 28.61390],
        [77.20914, 28.61390],
        [77.20914, 28.61400],
        [77.20900, 28.61400],
        [77.20900, 28.61390],
    ]],
}


def _location(polygon=DELHI_ROOF):
    return SimpleNamespace(polygon_geojson=polygon)


def _inside_or_on(point, ring):
    x, y = point
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        cross = (end[0] - start[0]) * (y - start[1]) - (end[1] - start[1]) * (x - start[0])
        if abs(cross) < 1e-10 and min(start[0], end[0]) - 1e-10 <= x <= max(start[0], end[0]) + 1e-10 and min(start[1], end[1]) - 1e-10 <= y <= max(start[1], end[1]) + 1e-10:
            return True
    inside = False
    for index, start in enumerate(ring):
        end = ring[index - 1]
        if (start[1] > y) != (end[1] > y) and x < (end[0] - start[0]) * (y - start[1]) / ((end[1] - start[1]) or 1e-15) + start[0]:
            inside = not inside
    return inside


def test_polygon_only_fixture_is_deterministic_flat_and_truthfully_labelled():
    first = reconstruction.build_reconstruction(_location())
    second = reconstruction.build_reconstruction(_location())

    assert first == second
    assert first["mode"] == "prototype_fixture"
    assert first["status_badge"] == {
        "label": "Prototype",
        "detail": "simulated roof geometry; roof pitch not verified",
        "tone": "prototype",
    }
    assert first["roof_type"] == "flat"
    assert len(first["surfaces"]) == 1
    assert first["surfaces"][0]["pitch_degrees"] == 0
    assert first["surfaces"][0]["plane_normal_enu"] == [0.0, 0.0, 1.0]
    assert first["structures"] == []
    assert first["shade_analysis"]["status"] == "insufficient_data"
    assert first["shade_analysis"]["placement_policy"]["unknown_cells"] == "exclude"
    assert first["shade_analysis"]["placement_policy"]["default_mode"] == "planning_preview"
    assert first["shade_analysis"]["areas_sqm"]["usable"] == 0
    assert first["shade_analysis"]["areas_sqm"]["unknown"] > 0
    assert all(surface["source"] == "prototype_fixture" for surface in first["surfaces"])
    assert all(surface["confidence"] == "simulated" for surface in first["surfaces"])


def test_fixture_geometry_changes_with_the_selected_roof():
    elongated = {
        "type": "Polygon",
        "coordinates": [[
            [77.20900, 28.61390], [77.20930, 28.61390],
            [77.20930, 28.61396], [77.20900, 28.61396],
            [77.20900, 28.61390],
        ]],
    }

    square_model = reconstruction.build_reconstruction(_location())
    elongated_model = reconstruction.build_reconstruction(_location(elongated))

    assert square_model["roof_type"] == "flat"
    assert elongated_model["roof_type"] == "flat"
    assert square_model["authoritative_boundary_geojson"] != elongated_model["authoritative_boundary_geojson"]
    assert square_model["surfaces"] != elongated_model["surfaces"]


def test_unequal_gable_surfaces_have_distinct_areas_and_complete_coverage():
    trapezoid = {
        "type": "Polygon",
        "coordinates": [[
            [77.20900, 28.61390], [77.20920, 28.61391],
            [77.20916, 28.61405], [77.20903, 28.61403],
            [77.20900, 28.61390],
        ]],
    }
    design = {"authoritative": {
        "roof_boundary": trapezoid,
        "roof_metadata": {"roof_type": "gable", "pitch_degrees": 18, "azimuth_degrees": 180, "height_meters": 8},
    }}

    model = reconstruction.build_reconstruction(_location(trapezoid), design)
    projected = [surface["projected_area_sqm"] for surface in model["surfaces"]]

    assert len(projected) == 2
    assert abs(projected[0] - projected[1]) > .05
    assert sum(projected) == pytest.approx(
        model["diagnostics"]["projected_boundary_area_sqm"], abs=.08
    )
    assert all(0 < surface["usable_area_sqm"] < surface["area_sqm"] for surface in model["surfaces"])


def test_gable_planes_with_complete_metadata_share_a_ridge_reference():
    design = {"authoritative": {
        "roof_boundary": DELHI_ROOF,
        "roof_metadata": {"roof_type": "gable", "pitch_degrees": 18, "azimuth_degrees": 180, "height_meters": 8},
    }}
    model = reconstruction.build_reconstruction(_location(), design)
    first, second = model["surfaces"]
    first_points = first["polygon_geojson"]["coordinates"][0][:-1]
    second_points = second["polygon_geojson"]["coordinates"][0][:-1]
    shared = [
        point for point in first_points
        if any(abs(point[0] - other[0]) < 1e-8 and abs(point[1] - other[1]) < 1e-8 for other in second_points)
    ]

    assert len(shared) == 2
    assert first["reference_point"] == second["reference_point"]

    def plane_offset(surface, point):
        origin = model["projection_origin"]
        reference = surface["reference_point"]
        metres_lng = 111_320 * math.cos(math.radians(origin["latitude"]))
        east = (point[0] - reference["longitude"]) * metres_lng
        north = (point[1] - reference["latitude"]) * 111_320
        azimuth = math.radians(surface["azimuth_degrees"])
        return -math.tan(math.radians(surface["pitch_degrees"])) * (
            east * math.sin(azimuth) + north * math.cos(azimuth)
        )

    for point in shared:
        assert plane_offset(first, point) == pytest.approx(plane_offset(second, point), abs=.003)


def test_missing_elevation_stays_unavailable_and_flat_plane_stays_flat():
    design = {"authoritative": {
        "roof_boundary": DELHI_ROOF,
        "roof_metadata": {"roof_type": "flat", "pitch_degrees": 25},
    }}
    surface = reconstruction.build_reconstruction(_location(), design)["surfaces"][0]

    assert surface["pitch_degrees"] == 0
    assert surface["reference_height_meters"] is None
    assert surface["plane_normal_enu"] == [0.0, 0.0, 1.0]


def test_pitch_and_clockwise_azimuth_produce_expected_plane_normal():
    design = {"authoritative": {
        "roof_boundary": DELHI_ROOF,
        "roof_metadata": {"roof_type": "shed", "pitch_degrees": 30, "azimuth_degrees": 90, "height_meters": 8},
    }}
    normal = reconstruction.build_reconstruction(_location(), design)["surfaces"][0]["plane_normal_enu"]

    assert normal == pytest.approx([0.5, 0.0, math.sqrt(3) / 2], abs=1e-7)


def test_invalid_provider_plane_falls_back_instead_of_receiving_panels():
    provider = {
        "available": True,
        "status": "available",
        "roof": {"segments": [{
            "segmentIndex": 1,
            "pitchDegrees": 95,
            "azimuthDegrees": 180,
            "footprint": DELHI_ROOF["coordinates"][0][:-1],
        }]},
    }

    model = reconstruction.build_reconstruction(_location(), provider_payload=provider)

    assert model["mode"] == "user_fallback"
    assert model["surfaces"][0]["source"] == "user_boundary"
    assert model["diagnostics"]["invalid_provider_surfaces"] > 0


def test_irregular_polygon_is_preserved_as_one_flat_fixture_without_facets():
    irregular = {
        "type": "Polygon",
        "coordinates": [[
            [77.20900, 28.61390], [77.20916, 28.61390],
            [77.20916, 28.61396], [77.20910, 28.61396],
            [77.20910, 28.61404], [77.20900, 28.61404],
            [77.20900, 28.61390],
        ]],
    }
    model = reconstruction.build_reconstruction(_location(irregular))

    assert model["roof_type"] == "flat"
    assert model["status_badge"]["label"] == "Prototype"
    assert len(model["surfaces"]) == 1
    assert model["surfaces"][0]["pitch_degrees"] == 0
    assert model["surfaces"][0]["polygon_geojson"] == irregular


def test_provider_surfaces_and_manual_structures_are_clipped_to_authoritative_boundary():
    provider = {
        "available": True,
        "source": "google_solar_building_insights",
        "status": "available",
        "roof": {"segments": [{
            "segmentIndex": 4,
            "pitchDegrees": 19,
            "azimuthDegrees": 172,
            "planeHeightAtCenterMeters": 7.8,
            "footprint": [
                [77.20890, 28.61385], [77.20920, 28.61385],
                [77.20920, 28.61405], [77.20890, 28.61405],
            ],
        }]},
    }
    design = {"authoritative": {
        "roof_boundary": DELHI_ROOF,
        "manual_obstructions": {"type": "MultiPolygon", "coordinates": [[[
            [77.20910, 28.61394], [77.20918, 28.61394],
            [77.20918, 28.61402], [77.20910, 28.61402],
            [77.20910, 28.61394],
        ]]]},
        "roof_metadata": {},
    }}

    model = reconstruction.build_reconstruction(_location(), design, provider)
    boundary = DELHI_ROOF["coordinates"][0][:-1]

    assert model["mode"] == "provider"
    assert model["surfaces"]
    assert model["structures"]
    for item in model["surfaces"]:
        assert item["pitch_degrees"] == 19
        assert item["azimuth_degrees"] == 172
        for point in item["polygon_geojson"]["coordinates"][0][:-1]:
            assert _inside_or_on(point, boundary)
    for item in model["structures"]:
        assert item["height_meters"] is None
        assert item["source"] == "user"
        for point in item["footprint_geojson"]["coordinates"][0][:-1]:
            assert _inside_or_on(point, boundary)


def test_user_boundary_fallback_is_explicit_and_preserves_geojson():
    model = reconstruction.build_reconstruction(
        _location(), provider_payload={"available": False, "status": "not_found"},
        prototype_fixture=False,
    )

    assert model["mode"] == "user_fallback"
    assert model["authoritative_boundary_geojson"] == DELHI_ROOF
    assert model["surfaces"][0]["source"] == "user_boundary"
    assert model["surfaces"][0]["pitch_degrees"] == 0
    assert model["diagnostics"]["provider_status"] == "not_found"


def test_failed_real_provider_does_not_masquerade_as_prototype_data():
    model = reconstruction.build_reconstruction(
        _location(),
        provider_payload={"available": False, "status": "outside_coverage"},
        prototype_fixture=True,
    )

    assert model["mode"] == "user_fallback"
    assert model["status_badge"]["tone"] == "fallback"
    assert model["surfaces"][0]["source"] == "user_boundary"


def test_roof_model_endpoint_uses_fixture_without_external_api(client, app, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Delhi reconstruction fixture",
            latitude=28.61395,
            longitude=77.20907,
            polygon_geojson=DELHI_ROOF,
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    app.config["ROOFTOP_PROTOTYPE_FIXTURE"] = True
    app.config["GOOGLE_SOLAR_API_KEY"] = None
    app.config["GOOGLE_MAPS_API_KEY"] = None
    from app.services import rooftop_ingestion_service
    monkeypatch.setattr(
        rooftop_ingestion_service,
        "ingest",
        lambda *args, **kwargs: pytest.fail("fixture mode must not call an external provider"),
    )

    response = client.get(f"/api/roof-model/{location_id}?high_fidelity=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["mode"] == "prototype_fixture"
    assert payload["projection_origin"]["longitude"] == pytest.approx(77.20907)
    assert payload["projection_origin"]["latitude"] == pytest.approx(28.61395)
    assert payload["constraints"]["boundary_authoritative"] is True
    assert payload["imagery"]["available"] is False


def test_roof_model_load_does_not_automatically_call_paid_provider(client, app, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Delhi cached reconstruction",
            latitude=28.61395,
            longitude=77.20907,
            polygon_geojson=DELHI_ROOF,
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    app.config["ROOFTOP_PROTOTYPE_FIXTURE"] = False
    app.config["GOOGLE_SOLAR_API_KEY"] = "configured-but-not-requested"
    from app.services import rooftop_ingestion_service
    monkeypatch.setattr(
        rooftop_ingestion_service,
        "ingest",
        lambda *args, **kwargs: pytest.fail("ordinary model loads must use persisted provider data"),
    )

    response = client.get(f"/api/roof-model/{location_id}")
    repeated = client.get(f"/api/roof-model/{location_id}")
    payload = response.get_json()

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert payload["mode"] == "user_fallback"
    assert payload["shade_analysis"]["status"] == "insufficient_data"


def test_renderer_contract_keeps_geometry_and_generation_concerns_separate():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app/static/js/roof3d.js").read_text(encoding="utf-8")
    template = (root / "app/templates/report.html").read_text(encoding="utf-8")

    assert "new THREE.OrthographicCamera" in source
    assert "requestAnimationFrame(animate);renderer.render(scene,camera)" in source
    assert "cameraAzimuth+=" not in source
    assert "polygonFits(corners,surface.polygon,obstructions,{edgeSetback:0,obstructionSetback:0,checkGrid:false})" in source
    assert "polygonFits(corners,localRing,obstructions)" in source
    assert "surfaceHeight(panel.surface" in source
    assert "reconstructedSurfaces.filter(function(surface){return surface.valid!==false;}).forEach(makeSurfaceMesh)" in source
    assert "roofHeightFromSurfaces(point[0],point[1])" in source
    assert "pair[1].visible=" in source
    assert "surface.azimuth" in source
    assert "surface.valid===false" in source
    assert "addSurfaceEdges()" in source
    assert "fitCamera(true)" in source
    assert "viewMode:viewMode" in source
    assert "Visible layout DC" in template
    assert "Selected estimate" in template
    assert "Max roof capacity" in template
    assert "northDegrees:0" in source
    assert "data-roof3d-layer=\"roof-planes\"" in template
    assert "data-roof3d-layer=\"panels\"" in template
    assert "data-roof3d-layer=\"structures\"" in template
    assert "data-roof3d-layer=\"boundary\"" in template
    assert 'id="roof3d-layout-mode"' in template
    assert "Planning preview — shade unverified" in template
    assert "Preview panels" in template
    assert "Confirmed panels" in template
    assert "setPanelLayoutMode:setPanelLayoutMode" in source
    mode_switch = source[source.index("function setPanelLayoutMode"):source.index("function bindPlannerControls")]
    assert "savedAnnualGeneration=" not in mode_switch
    assert "localRing=" not in mode_switch
    assert "fetch(" not in mode_switch
    assert "Prototype geometry is explicitly identified above." in template
    assert "/orientation-preview" in source
    assert "/orientation'" in source
