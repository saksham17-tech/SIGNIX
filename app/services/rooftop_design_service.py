"""Persistence service for versioned rooftop-design state."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from sqlalchemy import func

from app.geo import canonicalize_stored_ring
from app.models import RooftopDesign, RooftopDesignSource, UserLocation, db


DESIGN_REVIEW_STATUSES = {"draft", "reviewed", "accepted", "rejected"}
SOURCE_REVIEW_STATUSES = {"pending", "reviewed", "accepted", "rejected"}
SOURCE_AVAILABILITY_STATUSES = {
    "available",
    "unavailable",
    "not_found",
    "outside_coverage",
    "missing_api_key",
    "authentication_error",
    "quota_error",
    "timeout",
    "malformed_response",
    "processing_error",
}
ROOF_TYPES = {"flat", "gable", "hip", "shed", "mixed", "unknown"}


class RooftopDesignInputError(ValueError):
    pass


def get_owned_location(location_id, user_id):
    return UserLocation.query.filter_by(id=location_id, user_id=user_id).first()


def get_current_design(location_id, user_id):
    if get_owned_location(location_id, user_id) is None:
        return None, None
    design = (
        RooftopDesign.query.filter_by(user_location_id=location_id, is_current=True)
        .order_by(RooftopDesign.revision_number.desc())
        .first()
    )
    return design, design.to_dict() if design else None


def _canonical_polygon(value, field_name, required=False):
    if value is None:
        if required:
            raise RooftopDesignInputError(f"{field_name} is required")
        return None
    if not isinstance(value, dict) or value.get("type") != "Polygon":
        raise RooftopDesignInputError(f"{field_name} must be a GeoJSON Polygon")
    rings = value.get("coordinates")
    if not isinstance(rings, list) or not rings:
        raise RooftopDesignInputError(f"{field_name} must contain a polygon ring")
    normalized = []
    for ring in rings:
        canonical = canonicalize_stored_ring(ring)
        if len(canonical) < 4:
            raise RooftopDesignInputError(
                f"{field_name} rings must contain at least three positions"
            )
        for lng, lat in canonical:
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                raise RooftopDesignInputError(
                    f"{field_name} contains coordinates outside WGS84 bounds"
                )
        normalized.append(canonical)
    return {"type": "Polygon", "coordinates": normalized}


def _canonical_multipolygon(value, field_name):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("type") != "MultiPolygon":
        raise RooftopDesignInputError(f"{field_name} must be a GeoJSON MultiPolygon")
    polygons = value.get("coordinates")
    if not isinstance(polygons, list):
        raise RooftopDesignInputError(f"{field_name} must contain coordinates")
    normalized = []
    for polygon in polygons:
        parsed = _canonical_polygon(
            {"type": "Polygon", "coordinates": polygon}, field_name, required=True
        )
        normalized.append(parsed["coordinates"])
    return {"type": "MultiPolygon", "coordinates": normalized}


def _optional_number(value, field_name, minimum=None, maximum=None):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RooftopDesignInputError(f"{field_name} must be numeric") from exc
    if minimum is not None and number < minimum:
        raise RooftopDesignInputError(f"{field_name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise RooftopDesignInputError(f"{field_name} must be at most {maximum}")
    return number


def _parse_datetime(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RooftopDesignInputError("retrieved_at must be an ISO-8601 timestamp") from exc


def _source_payload(source):
    if not isinstance(source, dict):
        raise RooftopDesignInputError("Each advisory source must be an object")
    provider = str(source.get("provider") or "").strip()
    data_type = str(source.get("data_type") or "").strip()
    if not provider or not data_type:
        raise RooftopDesignInputError(
            "Each advisory source requires provider and data_type"
        )
    status = str(source.get("review_status") or "pending").strip().lower()
    if status not in SOURCE_REVIEW_STATUSES:
        raise RooftopDesignInputError("Invalid advisory source review_status")
    availability_status = str(
        source.get("availability_status") or "available"
    ).strip().lower()
    if availability_status not in SOURCE_AVAILABILITY_STATUSES:
        raise RooftopDesignInputError("Invalid advisory source availability_status")
    return {
        "provider": provider[:64],
        "data_type": data_type[:64],
        "retrieved_at": _parse_datetime(source.get("retrieved_at")),
        "source_version": (str(source["source_version"])[:128]
                           if source.get("source_version") is not None else None),
        "confidence": (str(source["confidence"])[:64]
                       if source.get("confidence") is not None else None),
        "quality": (str(source["quality"])[:64]
                       if source.get("quality") is not None else None),
        "availability_status": availability_status,
        "coverage": deepcopy(source.get("coverage")),
        "cache_key": (str(source["cache_key"])[:160]
                      if source.get("cache_key") is not None else None),
        "reusable_until": _parse_datetime(source.get("reusable_until")),
        "is_usable": bool(source.get("is_usable", False)),
        "review_status": status,
        "external_reference": deepcopy(source.get("external_reference")),
        "raw_payload": deepcopy(source.get("raw_payload")),
        "analysis_result": deepcopy(source.get("analysis_result")),
        "review_state": deepcopy(source.get("review_state")),
        "diagnostics": deepcopy(source.get("diagnostics")),
    }


def _source_from_model(source):
    return {
        "provider": source.provider,
        "data_type": source.data_type,
        "retrieved_at": source.retrieved_at,
        "source_version": source.source_version,
        "confidence": source.confidence,
        "quality": source.quality,
        "availability_status": source.availability_status,
        "coverage": deepcopy(source.coverage),
        "cache_key": source.cache_key,
        "reusable_until": source.reusable_until,
        "is_usable": bool(source.is_usable),
        "review_status": source.review_status,
        "external_reference": deepcopy(source.external_reference),
        "raw_payload": deepcopy(source.raw_payload),
        "analysis_result": deepcopy(source.analysis_result),
        "review_state": deepcopy(source.review_state),
        "diagnostics": deepcopy(source.diagnostics),
    }


def _design_snapshot(design):
    if design is None:
        return {}
    return {
        "schema_version": design.schema_version,
        "review_status": design.review_status,
        "authoritative_roof_geojson": deepcopy(design.authoritative_roof_geojson),
        "manual_obstructions_geojson": deepcopy(design.manual_obstructions_geojson),
        "accepted_roof_planes": deepcopy(design.accepted_roof_planes),
        "accepted_obstructions_geojson": deepcopy(
            design.accepted_obstructions_geojson
        ),
        "roof_type": design.roof_type,
        "pitch_degrees": design.pitch_degrees,
        "azimuth_degrees": design.azimuth_degrees,
        "elevation_meters": design.elevation_meters,
        "height_meters": design.height_meters,
        "panel_layout": deepcopy(design.panel_layout),
        "shade_analysis": deepcopy(design.shade_analysis),
        "diagnostics": deepcopy(design.diagnostics),
        "sources": [_source_from_model(source) for source in design.sources],
    }


def create_revision(location_id, user_id, payload=None, base_design=None):
    location = get_owned_location(location_id, user_id)
    if location is None:
        return None
    payload = payload or {}
    if not isinstance(payload, dict):
        raise RooftopDesignInputError("Request body must be a JSON object")

    if base_design is None:
        base_design = (
            RooftopDesign.query.filter_by(
                user_location_id=location_id, is_current=True
            )
            .order_by(RooftopDesign.revision_number.desc())
            .first()
        )
    state = _design_snapshot(base_design)
    location_data = location.to_dict()

    boundary_input = payload.get(
        "authoritative_roof_geojson",
        state.get("authoritative_roof_geojson")
        or location_data.get("polygon_geojson"),
    )
    boundary = _canonical_polygon(
        boundary_input, "authoritative_roof_geojson", required=True
    )
    manual_input = payload.get(
        "manual_obstructions_geojson",
        state.get("manual_obstructions_geojson")
        if "manual_obstructions_geojson" in state
        else location_data.get("obstructions_geojson"),
    )
    manual_obstructions = _canonical_multipolygon(
        manual_input, "manual_obstructions_geojson"
    )

    status = str(
        payload.get("review_status", state.get("review_status", "draft"))
    ).lower()
    if status not in DESIGN_REVIEW_STATUSES:
        raise RooftopDesignInputError("Invalid rooftop design review_status")

    roof_metadata = payload.get("roof_metadata") or {}
    if not isinstance(roof_metadata, dict):
        raise RooftopDesignInputError("roof_metadata must be an object")
    roof_type = roof_metadata.get("roof_type", state.get("roof_type"))
    if roof_type is not None:
        roof_type = str(roof_type).lower()
        if roof_type not in ROOF_TYPES:
            raise RooftopDesignInputError("Invalid roof_type")

    revision_number = (
        db.session.query(func.max(RooftopDesign.revision_number))
        .filter(RooftopDesign.user_location_id == location_id)
        .scalar()
        or 0
    ) + 1
    RooftopDesign.query.filter_by(
        user_location_id=location_id, is_current=True
    ).update({RooftopDesign.is_current: False}, synchronize_session="fetch")

    design = RooftopDesign(
        user_location_id=location_id,
        revision_number=revision_number,
        schema_version=str(
            payload.get("schema_version", state.get("schema_version", "1.0"))
        )[:32],
        review_status=status,
        is_current=True,
        authoritative_roof_geojson=boundary,
        manual_obstructions_geojson=manual_obstructions,
        accepted_roof_planes=deepcopy(
            payload.get("accepted_roof_planes", state.get("accepted_roof_planes"))
        ),
        accepted_obstructions_geojson=_canonical_multipolygon(
            payload.get(
                "accepted_obstructions_geojson",
                state.get("accepted_obstructions_geojson"),
            ),
            "accepted_obstructions_geojson",
        ),
        roof_type=roof_type,
        pitch_degrees=_optional_number(
            roof_metadata.get("pitch_degrees", state.get("pitch_degrees")),
            "pitch_degrees",
            0,
            90,
        ),
        azimuth_degrees=_optional_number(
            roof_metadata.get("azimuth_degrees", state.get("azimuth_degrees")),
            "azimuth_degrees",
            0,
            360,
        ),
        elevation_meters=_optional_number(
            roof_metadata.get("elevation_meters", state.get("elevation_meters")),
            "elevation_meters",
        ),
        height_meters=_optional_number(
            roof_metadata.get("height_meters", state.get("height_meters")),
            "height_meters",
            0,
        ),
        panel_layout=deepcopy(payload.get("panel_layout", state.get("panel_layout"))),
        shade_analysis=deepcopy(
            payload.get("shade_analysis", state.get("shade_analysis"))
        ),
        diagnostics=deepcopy(payload.get("diagnostics", state.get("diagnostics"))),
    )
    db.session.add(design)
    db.session.flush()

    source_payloads = payload.get("advisory_sources", state.get("sources", []))
    if not isinstance(source_payloads, list):
        raise RooftopDesignInputError("advisory_sources must be a list")
    for source in source_payloads:
        db.session.add(
            RooftopDesignSource(
                rooftop_design_id=design.id,
                **_source_payload(source),
            )
        )
    db.session.commit()
    return design


def review_source(location_id, design_id, source_id, user_id, payload):
    location = get_owned_location(location_id, user_id)
    if location is None:
        return None
    design = RooftopDesign.query.filter_by(
        id=design_id, user_location_id=location_id
    ).first()
    if design is None:
        return None
    source = RooftopDesignSource.query.filter_by(
        id=source_id, rooftop_design_id=design.id
    ).first()
    if source is None:
        return None
    status = str((payload or {}).get("review_status") or "").lower()
    if status not in {"reviewed", "accepted", "rejected"}:
        raise RooftopDesignInputError(
            "review_status must be reviewed, accepted, or rejected"
        )

    state = _design_snapshot(design)
    source_index = list(design.sources).index(source)
    state["sources"][source_index]["review_status"] = status
    if "review_state" in (payload or {}):
        state["sources"][source_index]["review_state"] = deepcopy(
            payload["review_state"]
        )
    revision_payload = {
        "schema_version": state["schema_version"],
        "review_status": state["review_status"],
        "authoritative_roof_geojson": state["authoritative_roof_geojson"],
        "manual_obstructions_geojson": state["manual_obstructions_geojson"],
        "accepted_roof_planes": state["accepted_roof_planes"],
        "accepted_obstructions_geojson": state[
            "accepted_obstructions_geojson"
        ],
        "roof_metadata": {
            "roof_type": state["roof_type"],
            "pitch_degrees": state["pitch_degrees"],
            "azimuth_degrees": state["azimuth_degrees"],
            "elevation_meters": state["elevation_meters"],
            "height_meters": state["height_meters"],
        },
        "panel_layout": state["panel_layout"],
        "shade_analysis": state["shade_analysis"],
        "diagnostics": state["diagnostics"],
        "advisory_sources": state["sources"],
    }
    return create_revision(
        location_id, user_id, revision_payload, base_design=design
    )
