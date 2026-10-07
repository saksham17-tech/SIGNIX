"""Installer-ready export package for a saved SIGNIX estimate.

Bundles roof geometry (GeoJSON), key metrics (JSON), and the homeowner PDF
summary into one zip the homeowner can share with any installer — vendor-neutral.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


PACKAGE_README = """SIGNIX installer-ready package
================================

This zip is produced by SIGNIX for the homeowner. It is vendor-neutral:
take it to any solar installer of your choice. SIGNIX does not send your
data to partner installers.

Contents
--------
1. roof.geojson     — rooftop boundary + obstruction polygons
2. metrics.json     — key system metrics (size, generation, subsidy, rating)
3. summary.pdf      — 2-page homeowner summary report
4. README.txt       — this file

All figures are indicative estimates only — verify with DISCOM / vendor.
"""


def _ring_to_lnglat(ring) -> List[List[float]]:
    """GeoJSON positions are [lng, lat]. SIGNIX often stores [lat, lng]."""
    out = []
    for pt in ring or []:
        if not pt or len(pt) < 2:
            continue
        a, b = float(pt[0]), float(pt[1])
        if abs(a) <= 90 and abs(b) > 90:
            lat, lng = a, b
        elif abs(a) > 90 and abs(b) <= 90:
            lat, lng = b, a
        else:
            # Prefer SIGNIX drawing convention [lat, lng]
            lat, lng = a, b
        out.append([lng, lat])
    # Close ring if needed
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


def _polygon_geometry(polygon_geojson) -> Optional[Dict[str, Any]]:
    if not polygon_geojson:
        return None
    if isinstance(polygon_geojson, dict) and polygon_geojson.get("type") == "Polygon":
        coords = polygon_geojson.get("coordinates") or []
        rings = [_ring_to_lnglat(r) for r in coords]
        rings = [r for r in rings if len(r) >= 4]
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}
    if isinstance(polygon_geojson, list):
        ring = _ring_to_lnglat(polygon_geojson)
        if len(ring) < 4:
            return None
        return {"type": "Polygon", "coordinates": [ring]}
    return None


def _obstruction_features(obstructions_geojson) -> List[Dict[str, Any]]:
    features = []
    if not obstructions_geojson:
        return features
    if isinstance(obstructions_geojson, dict):
        typ = obstructions_geojson.get("type")
        coords = obstructions_geojson.get("coordinates") or []
        if typ == "MultiPolygon":
            for i, poly in enumerate(coords):
                rings = [_ring_to_lnglat(r) for r in poly]
                rings = [r for r in rings if len(r) >= 4]
                if rings:
                    features.append(
                        {
                            "type": "Feature",
                            "properties": {"kind": "obstruction", "index": i},
                            "geometry": {"type": "Polygon", "coordinates": rings},
                        }
                    )
        elif typ == "Polygon":
            geom = _polygon_geometry(obstructions_geojson)
            if geom:
                features.append(
                    {
                        "type": "Feature",
                        "properties": {"kind": "obstruction", "index": 0},
                        "geometry": geom,
                    }
                )
        return features
    if isinstance(obstructions_geojson, list):
        for i, item in enumerate(obstructions_geojson):
            geom = _polygon_geometry(item)
            if geom:
                features.append(
                    {
                        "type": "Feature",
                        "properties": {"kind": "obstruction", "index": i},
                        "geometry": geom,
                    }
                )
    return features


def build_roof_geojson(location) -> Dict[str, Any]:
    poly = getattr(location, "polygon_geojson", None)
    if poly is None and isinstance(location, dict):
        poly = location.get("polygon_geojson")
    obs = getattr(location, "obstructions_geojson", None)
    if obs is None and isinstance(location, dict):
        obs = location.get("obstructions_geojson")

    features: List[Dict[str, Any]] = []
    roof_geom = _polygon_geometry(poly)
    if roof_geom:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "rooftop",
                    "address": getattr(location, "address", None)
                    if not isinstance(location, dict)
                    else location.get("address"),
                },
                "geometry": roof_geom,
            }
        )
    features.extend(_obstruction_features(obs))
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "generator": "SIGNIX",
            "crs_note": "Coordinates are WGS84 (EPSG:4326), [longitude, latitude].",
        },
    }


def build_metrics_json(location) -> Dict[str, Any]:
    if hasattr(location, "to_dict"):
        data = location.to_dict()
    else:
        data = dict(location)
    extras = data.get("extras") or {}
    rating = data.get("suitability_rating") or {}
    return {
        "package": "signix_installer_export",
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vendor_neutral": True,
        "notice": (
            "Vendor-neutral homeowner package — share with any installer. "
            "SIGNIX does not forward this to partner installers. "
            "Indicative only — verify with DISCOM / vendor."
        ),
        "location": {
            "id": data.get("id"),
            "address": data.get("address"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        },
        "system": {
            "system_size_kw": data.get("system_size"),
            "panel_count": extras.get("panel_count") or data.get("panel_count"),
            "annual_generation_kwh": data.get("annual_generation"),
            "battery_kwh": data.get("battery_kwh"),
            "roof_area_sqm": data.get("roof_area_sqm"),
            "usable_area_sqm": data.get("usable_area_sqm"),
        },
        "financials": {
            "system_cost": data.get("system_cost"),
            "subsidy_amount": data.get("subsidy_amount"),
            "net_investment": data.get("net_investment"),
            "monthly_savings": data.get("monthly_savings"),
            "payback_years": data.get("payback_years"),
            "simple_payback_years": extras.get("simple_payback_years"),
            "nominal_payback_years": extras.get("nominal_payback_years"),
        },
        "suitability_rating": {
            "overall_viability": rating.get("overall_viability"),
            "data_confidence": rating.get("data_confidence"),
            "user_priority": rating.get("user_priority"),
            "top_positive_factor": rating.get("top_positive_factor"),
            "top_limiting_factor": rating.get("top_limiting_factor"),
            "rating_algorithm_version": rating.get("rating_algorithm_version") or "1.0",
        },
        "irradiance": {
            "source": data.get("irradiance_source"),
            "confidence": data.get("irradiance_confidence")
            or extras.get("irradiance_confidence"),
        },
    }


def build_installer_package_zip(location, pdf_bytes: bytes) -> io.BytesIO:
    """Return a BytesIO zip: roof.geojson, metrics.json, summary.pdf, README.txt."""
    geojson = build_roof_geojson(location)
    metrics = build_metrics_json(location)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "roof.geojson",
            json.dumps(geojson, indent=2, ensure_ascii=False),
        )
        zf.writestr(
            "metrics.json",
            json.dumps(metrics, indent=2, ensure_ascii=False),
        )
        zf.writestr("summary.pdf", pdf_bytes)
        zf.writestr("README.txt", PACKAGE_README)
    buf.seek(0)
    return buf
