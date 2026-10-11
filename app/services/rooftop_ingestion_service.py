"""Cache-backed ingestion of advisory, location-specific rooftop data."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from flask import current_app

from app.models import RooftopDesign, RooftopDesignSource
from app.services import rooftop_design_service


PROVIDER_GOOGLE = "google_solar"
PROVIDER_GEMINI = "gemini"
BUILDING_INSIGHTS = "building_insights"
DATA_LAYERS = "data_layers"
GEMINI_ANALYSIS = "roof_analysis"


def _now():
    return datetime.now(timezone.utc)


def _naive_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _safe_url(value):
    """Remove credentials from a provider URL before persistence."""
    if not value:
        return None
    parts = urlsplit(str(value))
    query = urlencode(
        [(key, item) for key, item in parse_qsl(parts.query) if key.lower() != "key"]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _cache_key(location, data_type):
    # Rounded location plus polygon identity prevents reuse after a roof edit.
    polygon = (location.polygon_geojson or {}).get("coordinates") or []
    identity = json.dumps(polygon, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return (
        f"{data_type}:{float(location.latitude):.7f}:"
        f"{float(location.longitude):.7f}:{digest}"
    )


def _cached_source(design, provider, data_type, cache_key):
    if design is None:
        return None
    now = datetime.utcnow()
    candidates = [
        source
        for source in design.sources
        if source.provider == provider
        and source.data_type == data_type
        and source.cache_key == cache_key
        and source.reusable_until is not None
        and _naive_utc(source.reusable_until) > now
    ]
    return max(
        candidates,
        key=lambda source: (
            bool(source.is_usable),
            source.retrieved_at or datetime.min,
        ),
        default=None,
    )


def _status_for_exception(exc):
    if isinstance(exc, requests.Timeout):
        return "timeout"
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in {401, 403}:
        return "authentication_error"
    if code == 404:
        return "not_found"
    if code == 429:
        return "quota_error"
    message = str(exc).lower()
    if "not configured" in message or "api key" in message and "missing" in message:
        return "missing_api_key"
    if (
        "quota" in message
        or "resource_exhausted" in message
        or "resource exhausted" in message
        or "429" in message
    ):
        return "quota_error"
    if "access denied" in message or "unauthorized" in message or "forbidden" in message:
        return "authentication_error"
    if "not found" in message or "no supported building" in message:
        return "not_found"
    if "coverage" in message or "not covered" in message:
        return "outside_coverage"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "malformed_response"
    if isinstance(exc, requests.RequestException):
        return "unavailable"
    return "processing_error"


def _diagnostic(exc):
    return {
        "exception_type": type(exc).__name__,
        "message": str(exc)[:500],
    }


def _expiry(success):
    days = max(1, int(current_app.config.get("ROOFTOP_EXTERNAL_CACHE_DAYS", 30)))
    return _now() + (timedelta(days=days) if success else timedelta(hours=1))


def _source_record(
    *, provider, data_type, cache_key, status, model=None, confidence=None,
    quality=None, coverage=None, reference=None, raw=None, normalized=None,
    diagnostics=None
):
    usable = status == "available" and normalized is not None
    return {
        "provider": provider,
        "data_type": data_type,
        "retrieved_at": _now().isoformat(),
        "source_version": model,
        "confidence": confidence,
        "quality": quality,
        "availability_status": status,
        "coverage": coverage or {"in_coverage": status == "available"},
        "cache_key": cache_key,
        "reusable_until": _expiry(usable).isoformat(),
        "is_usable": usable,
        "review_status": "pending",
        "external_reference": reference,
        "raw_payload": raw,
        "analysis_result": normalized,
        "diagnostics": diagnostics or {},
    }


def _bbox_polygon(bbox):
    if not isinstance(bbox, dict):
        return None
    sw, ne = bbox.get("sw") or {}, bbox.get("ne") or {}
    try:
        west, south = float(sw["longitude"]), float(sw["latitude"])
        east, north = float(ne["longitude"]), float(ne["latitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south], [east, south], [east, north], [west, north],
            [west, south],
        ]],
    }


def normalize_building_insights(payload, selected_ring, requested_lat, requested_lng):
    """Normalize Google JSON without treating provider geometry as authoritative."""
    if not isinstance(payload, dict) or not isinstance(payload.get("solarPotential"), dict):
        raise ValueError("Google Solar Building Insights response is malformed")
    from app import roof_model

    center = payload.get("center") or {}
    try:
        center_lat = float(center.get("latitude", requested_lat))
        center_lng = float(center.get("longitude", requested_lng))
    except (TypeError, ValueError) as exc:
        raise ValueError("Google Solar building center is malformed") from exc
    distance = roof_model._meters(
        requested_lat, requested_lng, center_lat, center_lng
    )
    if distance > 60:
        raise LookupError(
            f"Building Insights result is outside coverage for the selected roof ({distance:.1f} m away)"
        )

    solar = payload["solarPotential"]
    segments = []
    for index, segment in enumerate(solar.get("roofSegmentStats") or []):
        if not isinstance(segment, dict):
            continue
        stats = segment.get("stats") or {}
        footprint = _bbox_polygon(segment.get("boundingBox"))
        segments.append({
            "segment_index": index,
            "pitch_degrees": segment.get("pitchDegrees"),
            "azimuth_degrees": segment.get("azimuthDegrees"),
            "area_meters2": stats.get("areaMeters2"),
            "ground_area_meters2": stats.get("groundAreaMeters2"),
            "center": segment.get("center"),
            "provider_footprint_geojson": footprint,
            "plane_height_at_center_meters": segment.get(
                "planeHeightAtCenterMeters"
            ),
            "annual_flux_kwh_m2": stats.get("sunshineQuantiles"),
        })

    return {
        "coordinate_contract": "GeoJSON [longitude, latitude]",
        "authoritative_footprint_geojson": {
            "type": "Polygon",
            "coordinates": [deepcopy(selected_ring)],
        },
        "provider_building_footprint_geojson": _bbox_polygon(
            payload.get("boundingBox")
        ),
        "building_center": {"latitude": center_lat, "longitude": center_lng},
        "matched_distance_meters": round(distance, 2),
        "roof": {
            "whole_area_meters2": (solar.get("wholeRoofStats") or {}).get(
                "areaMeters2"
            ),
            "segments": segments,
        },
        "solar": {
            "max_array_panels_count": solar.get("maxArrayPanelsCount"),
            "panel_width_meters": solar.get("panelWidthMeters"),
            "panel_height_meters": solar.get("panelHeightMeters"),
            "max_array_area_meters2": solar.get("maxArrayAreaMeters2"),
            "max_sunshine_hours_per_year": solar.get(
                "maxSunshineHoursPerYear"
            ),
            "carbon_offset_factor_kg_per_mwh": solar.get(
                "carbonOffsetFactorKgPerMwh"
            ),
        },
        "imagery": {
            "date": payload.get("imageryDate"),
            "quality": payload.get("imageryQuality"),
        },
    }


def normalize_data_layers(payload, dsm_grid=None, flux_grid=None):
    if not isinstance(payload, dict):
        raise ValueError("Google Solar Data Layers response is malformed")
    urls = {
        "dsm": _safe_url(payload.get("dsmUrl")),
        "rgb": _safe_url(payload.get("rgbUrl")),
        "mask": _safe_url(payload.get("maskUrl")),
        "annual_flux": _safe_url(payload.get("annualFluxUrl")),
    }
    if not any(urls.values()) and dsm_grid is None and flux_grid is None:
        raise ValueError("Google Solar Data Layers response contains no usable layers")
    return {
        "coordinate_contract": "GeoJSON [longitude, latitude]; local east/+X, north/-Z",
        "imagery": {
            "date": payload.get("imageryDate"),
            "quality": payload.get("imageryQuality"),
        },
        "resolution_meters": payload.get("pixelSizeMeters") or 0.25,
        "layers": urls,
        "dsm_grid": dsm_grid,
        "annual_flux_grid": flux_grid,
    }


def _fetch_building(location, ring):
    from app import roof_model

    key = roof_model._key()
    cache_key = _cache_key(location, BUILDING_INSIGHTS)
    if not key:
        return _source_record(
            provider=PROVIDER_GOOGLE,
            data_type=BUILDING_INSIGHTS,
            cache_key=cache_key,
            status="missing_api_key",
            diagnostics={"message": "GOOGLE_SOLAR_API_KEY is not configured"},
        )
    lat, lng = roof_model._selection_point(ring)
    try:
        raw = roof_model._building_payload(lat, lng)
        normalized = normalize_building_insights(raw, ring, lat, lng)
        quality = (normalized.get("imagery") or {}).get("quality")
        return _source_record(
            provider=PROVIDER_GOOGLE,
            data_type=BUILDING_INSIGHTS,
            cache_key=cache_key,
            status="available",
            model="Google Solar API v1",
            quality=quality,
            coverage={
                "in_coverage": True,
                "matched_distance_meters": normalized["matched_distance_meters"],
            },
            reference={
                "endpoint": "buildingInsights:findClosest",
                "request_location": {"latitude": lat, "longitude": lng},
            },
            raw=raw,
            normalized=normalized,
        )
    except Exception as exc:
        status = "outside_coverage" if isinstance(exc, LookupError) else _status_for_exception(exc)
        return _source_record(
            provider=PROVIDER_GOOGLE,
            data_type=BUILDING_INSIGHTS,
            cache_key=cache_key,
            status=status,
            model="Google Solar API v1",
            diagnostics=_diagnostic(exc),
        )


def _fetch_data_layers(location, ring):
    from app import roof_model

    cache_key = _cache_key(location, DATA_LAYERS)
    lat, lng = roof_model._selection_point(ring)
    diagnostics = {}
    try:
        raw = roof_model._data_layers(lat, lng)
        dsm = flux = None
        try:
            dsm = roof_model._dsm_grid(location, ring, raw)
        except Exception as exc:
            diagnostics["dsm"] = _diagnostic(exc)
        try:
            flux = roof_model._flux_grid(location, ring, raw)
        except Exception as exc:
            diagnostics["annual_flux"] = _diagnostic(exc)
        normalized = normalize_data_layers(raw, dsm, flux)
        refs = deepcopy(normalized["layers"])
        return _source_record(
            provider=PROVIDER_GOOGLE,
            data_type=DATA_LAYERS,
            cache_key=cache_key,
            status="available",
            model="Google Solar API v1",
            quality=(normalized.get("imagery") or {}).get("quality"),
            coverage={
                "in_coverage": True,
                "dsm_available": dsm is not None,
                "annual_flux_available": flux is not None,
            },
            reference=refs,
            normalized=normalized,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        return _source_record(
            provider=PROVIDER_GOOGLE,
            data_type=DATA_LAYERS,
            cache_key=cache_key,
            status=_status_for_exception(exc),
            model="Google Solar API v1",
            diagnostics=_diagnostic(exc),
        )


def _fetch_gemini(location, ring):
    from app import roof_ai

    cache_key = _cache_key(location, GEMINI_ANALYSIS)
    model = current_app.config.get("GEMINI_ROOF_MODEL") or current_app.config.get(
        "GEMINI_MODEL", "gemini-3.6-flash"
    )
    if not current_app.config.get("GEMINI_API_KEY"):
        return _source_record(
            provider=PROVIDER_GEMINI,
            data_type=GEMINI_ANALYSIS,
            cache_key=cache_key,
            status="missing_api_key",
            model=model,
            diagnostics={"message": "GEMINI_API_KEY is not configured"},
        )
    try:
        normalized = roof_ai.analyze_polygon(ring)
        return _source_record(
            provider=PROVIDER_GEMINI,
            data_type=GEMINI_ANALYSIS,
            cache_key=cache_key,
            status="available",
            model=model,
            confidence=str(normalized.get("confidence", 0.0)),
            coverage={"in_coverage": True, "imagery_available": True},
            reference={"imagery_provider": normalized.get("imagery_provider")},
            normalized=normalized,
        )
    except Exception as exc:
        return _source_record(
            provider=PROVIDER_GEMINI,
            data_type=GEMINI_ANALYSIS,
            cache_key=cache_key,
            status=_status_for_exception(exc),
            model=model,
            diagnostics=_diagnostic(exc),
        )


def _source_summary(source, cached):
    if isinstance(source, RooftopDesignSource):
        data = source.to_dict()
    else:
        data = deepcopy(source)
    return {
        "provider": data.get("provider"),
        "data_type": data.get("data_type"),
        "status": data.get("availability_status"),
        "cached": cached,
        "usable": bool(data.get("is_usable")),
        "coverage": data.get("coverage"),
        "analysis": data.get("analysis_result"),
        "diagnostics": data.get("diagnostics") or {},
    }


def ingest(location_id, user_id, providers=None, include_data_layers=False, refresh=False):
    """Ingest requested advisory sources and create at most one new revision."""
    location = rooftop_design_service.get_owned_location(location_id, user_id)
    if location is None:
        return None
    providers = providers or [PROVIDER_GOOGLE]
    providers = list(dict.fromkeys(providers))
    ring = rooftop_design_service._canonical_polygon(
        location.polygon_geojson, "polygon_geojson", required=True
    )["coordinates"][0]
    current, _ = rooftop_design_service.get_current_design(location_id, user_id)
    new_sources = []
    summaries = []

    def obtain(provider, data_type, fetcher):
        key = _cache_key(location, data_type)
        cached = None if refresh else _cached_source(current, provider, data_type, key)
        if cached is not None:
            summaries.append(_source_summary(cached, True))
            return cached
        record = fetcher(location, ring)
        new_sources.append(record)
        summaries.append(_source_summary(record, False))
        return record

    if PROVIDER_GOOGLE in providers:
        building = obtain(PROVIDER_GOOGLE, BUILDING_INSIGHTS, _fetch_building)
        building_status = (
            building.availability_status
            if isinstance(building, RooftopDesignSource)
            else building["availability_status"]
        )
        if include_data_layers and building_status == "available":
            obtain(PROVIDER_GOOGLE, DATA_LAYERS, _fetch_data_layers)
    if PROVIDER_GEMINI in providers:
        obtain(PROVIDER_GEMINI, GEMINI_ANALYSIS, _fetch_gemini)

    if new_sources:
        existing = (
            [rooftop_design_service._source_from_model(item) for item in current.sources]
            if current
            else []
        )
        current = rooftop_design_service.create_revision(
            location_id,
            user_id,
            {"advisory_sources": existing + new_sources},
            base_design=current,
        )
    elif current is None:
        current = rooftop_design_service.create_revision(location_id, user_id, {})

    return {
        "design": current.to_dict(),
        "sources": summaries,
        "used_cache": bool(summaries) and all(item["cached"] for item in summaries),
    }


def legacy_roof_model_payload(design):
    """Adapt persisted normalized sources to the existing roof3d.js contract."""
    building = next(
        (
            item for item in reversed(design.sources)
            if item.provider == PROVIDER_GOOGLE
            and item.data_type == BUILDING_INSIGHTS
            and item.is_usable
        ),
        None,
    )
    if building is None:
        latest = next(
            (
                item for item in reversed(design.sources)
                if item.provider == PROVIDER_GOOGLE
                and item.data_type == BUILDING_INSIGHTS
            ),
            None,
        )
        return {
            "available": False,
            "status": latest.availability_status if latest else "unavailable",
            "error": ((latest.diagnostics or {}).get("message") if latest else None)
            or "Location-specific rooftop data is unavailable",
        }
    normalized = building.analysis_result or {}
    roof = normalized.get("roof") or {}
    solar = normalized.get("solar") or {}
    segments = []
    for segment in roof.get("segments") or []:
        footprint = ((segment.get("provider_footprint_geojson") or {}).get("coordinates") or [[]])[0]
        segments.append({
            "segmentIndex": segment.get("segment_index"),
            "pitchDegrees": segment.get("pitch_degrees"),
            "azimuthDegrees": segment.get("azimuth_degrees"),
            "areaMeters2": segment.get("area_meters2"),
            "groundAreaMeters2": segment.get("ground_area_meters2"),
            "center": segment.get("center"),
            "planeHeightAtCenterMeters": segment.get("plane_height_at_center_meters"),
            "footprint": footprint[:-1] if footprint and footprint[0] == footprint[-1] else footprint,
        })
    layers = next(
        (
            item for item in reversed(design.sources)
            if item.provider == PROVIDER_GOOGLE
            and item.data_type == DATA_LAYERS
            and item.is_usable
        ),
        None,
    )
    layer_data = (layers.analysis_result or {}) if layers else {}
    dsm = layer_data.get("dsm_grid")
    flux = layer_data.get("annual_flux_grid")
    if flux:
        flux = dict(flux)
        flux["provenance"] = {
            "provider": PROVIDER_GOOGLE,
            "retrieved_at": layers.retrieved_at.isoformat() if layers.retrieved_at else None,
            "source_version": layers.source_version,
            "quality": layers.quality,
            "resolution_meters": layer_data.get("resolution_meters"),
        }
    return {
        "available": True,
        "status": "available",
        "source": "google_solar_dsm" if dsm else "google_solar_building_insights",
        "cached": True,
        "dsmAvailable": bool(dsm),
        "shadeAvailable": bool(flux),
        "shade": flux,
        "imageryQuality": (normalized.get("imagery") or {}).get("quality"),
        "imageryDate": (normalized.get("imagery") or {}).get("date"),
        "matchedDistanceMeters": normalized.get("matched_distance_meters"),
        "warnings": list((layers.diagnostics or {}).keys()) if layers else [],
        "building": {
            "center": normalized.get("building_center"),
            "footprint": normalized.get("provider_building_footprint_geojson"),
        },
        "roof": {
            "wholeAreaMeters2": roof.get("whole_area_meters2"),
            "maxArrayPanelsCount": solar.get("max_array_panels_count"),
            "panelWidthMeters": solar.get("panel_width_meters"),
            "panelHeightMeters": solar.get("panel_height_meters"),
            "segments": segments,
        },
        "layers": (layer_data.get("layers") or {}),
        "selectedRoof": ((normalized.get("authoritative_footprint_geojson") or {}).get("coordinates") or [[]])[0],
        "dsmGrid": dsm,
    }
