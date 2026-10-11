"""Location-specific roof reconstruction using Google Solar API data layers.

The Solar API is used as the geographic source of truth for the selected
building.  Building Insights supplies roof-segment metadata while the DSM
GeoTIFF supplies a height field that can be turned into a Three.js roof mesh.
The user's saved roof polygon remains the final clip/selection boundary.
"""
from __future__ import annotations

import io
import math
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, request, send_file
from flask_login import current_user, login_required

from app.geo import canonicalize_stored_ring


roof_model_bp = Blueprint("roof_model", __name__)
SOLAR_BASE = "https://solar.googleapis.com/v1"


def _key():
    return current_app.config.get("GOOGLE_SOLAR_API_KEY") or current_app.config.get("GOOGLE_MAPS_API_KEY")


def _get_location(location_id):
    from app.services import location_service
    return location_service.get_location(location_id, current_user.id)


def _polygon_coords(location):
    geo = location.polygon_geojson or {}
    coords = geo.get("coordinates") if isinstance(geo, dict) else None
    if isinstance(coords, list) and coords and isinstance(coords[0], list):
        ring = canonicalize_stored_ring(
            coords[0], location.latitude, location.longitude
        )
        if len(ring) >= 3:
            return ring
    return None


def _solar_get(path: str, params: dict[str, Any]):
    key = _key()
    if not key:
        raise RuntimeError("GOOGLE_SOLAR_API_KEY is not configured")
    params = dict(params)
    params["key"] = key
    r = requests.get(f"{SOLAR_BASE}/{path}", params=params, timeout=25)
    if not r.ok:
        try:
            payload = r.json()
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            detail = err.get("message")
            reason = ((err.get("details") or [{}])[0].get("reason") if isinstance(err.get("details"), list) else None)
        except Exception:
            payload, detail, reason = None, None, None
        if r.status_code in (401, 403):
            hint = "Check that Solar API is enabled, billing is active, and the server-side key permits Solar API requests. If the key is restricted by HTTP referrer, create/use an IP-restricted server key for this Flask backend."
            raise RuntimeError(f"Google Solar API access denied (HTTP {r.status_code})" + (f": {detail}" if detail else "") + f". {hint}")
        if r.status_code == 404:
            raise RuntimeError(f"Google Solar API has no supported building/imagery at this location" + (f": {detail}" if detail else ""))
        raise RuntimeError(detail or f"Google Solar API returned HTTP {r.status_code}")
    return r.json()


def _bbox_from_ring(ring):
    """Bounds for a canonical GeoJSON ``[longitude, latitude]`` ring."""
    lngs = [float(p[0]) for p in ring]
    lats = [float(p[1]) for p in ring]
    return min(lngs), min(lats), max(lngs), max(lats)


def _centroid(ring):
    """Return ``(latitude, longitude)`` for a canonical GeoJSON ring."""
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    lng = sum(float(p[0]) for p in points) / max(1, len(points))
    lat = sum(float(p[1]) for p in points) / max(1, len(points))
    return lat, lng


def _lnglat_to_local_meters(lng, lat, origin_lng, origin_lat):
    """Convert WGS84 degrees to local east/north metres around an origin."""
    north = (float(lat) - float(origin_lat)) * 111320.0
    east = (
        (float(lng) - float(origin_lng))
        * 111320.0
        * math.cos(math.radians(float(origin_lat)))
    )
    return east, north


def _mask_geometry(ring):
    """Build an EPSG:4326 GeoJSON polygon for raster masking."""
    closed = [list(point) for point in ring]
    if closed and closed[0] != closed[-1]:
        closed.append(closed[0])
    return {"type": "Polygon", "coordinates": [closed]}


def _meters(a_lat, a_lng, b_lat, b_lng):
    r = 6371000.0
    x = math.radians(b_lng - a_lng) * math.cos(math.radians((a_lat + b_lat) / 2)) * r
    y = math.radians(b_lat - a_lat) * r
    return math.hypot(x, y)


def _point_in_polygon(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        hit = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi)
        if hit:
            inside = not inside
        j = i
    return inside


def _building_payload(lat, lng):
    # BASE is the practical minimum for India; the Solar API returns the
    # highest available imagery quality at the requested point.
    return _solar_get(
        "buildingInsights:findClosest",
        {
            "location.latitude": f"{lat:.7f}",
            "location.longitude": f"{lng:.7f}",
            "requiredQuality": "BASE",
        },
    )


def _selection_point(ring):
    """Return a point likely to be inside the user's selected rooftop.

    A vertex-average can fall outside a concave polygon, which can make
    buildingInsights match a neighbouring building. Prefer an interior point.
    """
    lat, lng = _centroid(ring)
    if _point_in_polygon(lng, lat, ring):
        return lat, lng
    lngs=[float(p[0]) for p in ring]
    lats=[float(p[1]) for p in ring]
    candidates=[((min(lats)+max(lats))/2, (min(lngs)+max(lngs))/2)]
    # Try edge midpoints and finally the first vertex nudged toward the mean.
    for i in range(len(ring)-1):
        candidates.append(((float(ring[i][1])+float(ring[i+1][1]))/2, (float(ring[i][0])+float(ring[i+1][0]))/2))
    for plat,plng in candidates:
        if _point_in_polygon(plng, plat, ring):
            return plat, plng
    return lat, lng


def _data_layers(lat, lng):
    return _solar_get(
        "dataLayers:get",
        {
            "location.latitude": f"{lat:.7f}",
            "location.longitude": f"{lng:.7f}",
            "radiusMeters": "60",
            "view": "FULL_LAYERS",
            "requiredQuality": "BASE",
            "exactQualityRequired": "false",
            "pixelSizeMeters": "0.25",
        },
    )



def _dsm_grid(location, ring, layers):
    """Turn Google's DSM GeoTIFF into a compact local height grid for Three.js."""
    try:
        import numpy as np
        import rasterio
        from rasterio.mask import mask as rio_mask
        from rasterio.warp import transform_geom
    except ImportError as exc:
        raise RuntimeError("High-fidelity roof reconstruction requires rasterio and numpy") from exc
    key = _key()
    url = layers.get("dsmUrl")
    if not url:
        raise RuntimeError("Google Solar DSM is unavailable")
    sep = "&" if "?" in url else "?"
    response = requests.get(url + sep + "key=" + key, timeout=40)
    response.raise_for_status()
    with rasterio.io.MemoryFile(response.content) as mem:
        with mem.open() as ds:
            geom = _mask_geometry(ring)
            if ds.crs:
                geom = transform_geom("EPSG:4326", ds.crs, geom, precision=8)
            data, transform = rio_mask(ds, [geom], crop=True, filled=False, all_touched=True)
            arr = data[0]
            if arr.size == 0:
                raise RuntimeError("DSM crop is empty")
            # Downsample for a web-friendly mesh while preserving the actual roof shape.
            max_dim = 80
            rows, cols = arr.shape
            step = max(1, int(math.ceil(max(rows, cols) / max_dim)))
            arr = arr[::step, ::step]
            valid = np.ma.getmaskarray(arr) == False
            vals = np.asarray(arr.filled(np.nan), dtype=float)
            valid &= np.isfinite(vals)
            if valid.sum() < 12:
                raise RuntimeError("DSM contains insufficient roof samples")
            base = float(np.nanpercentile(vals[valid], 5))
            vals = vals - base
            # Avoid tiny DSM spikes dominating the camera/model.
            top = float(np.nanpercentile(vals[valid], 98))
            vals = np.clip(vals, 0, max(top, 1.0))
            crs = ds.crs
            inv = ~transform
            # Use pixel centers in the cropped/downsampled raster.
            xs = []
            zs = []
            transformer = None
            if crs:
                from pyproj import Transformer
                transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            for c in range(arr.shape[1]):
                px, py = transform * (c * step + 0.5 * step, 0.5 * step)
                if transformer:
                    lng, lat = transformer.transform(px, py)
                else:
                    lng, lat = px, py
                xs.append(lng); zs.append(lat)
            # y coordinate for each row; local conversion uses the saved roof centroid.
            lat_rows = []
            for r in range(arr.shape[0]):
                px, py = transform * (0.5 * step, r * step + 0.5 * step)
                if transformer:
                    lng, lat = transformer.transform(px, py)
                else:
                    lng, lat = px, py
                lat_rows.append(lat)
            c_lat, c_lng = _centroid(ring)
            x_coords = [
                _lnglat_to_local_meters(lng, c_lat, c_lng, c_lat)[0]
                for lng in xs
            ]
            z_coords = [
                -_lnglat_to_local_meters(c_lng, lat, c_lng, c_lat)[1]
                for lat in lat_rows
            ]
            return {
                "rows": int(arr.shape[0]),
                "cols": int(arr.shape[1]),
                "x": [round(v, 3) for v in x_coords],
                "z": [round(v, 3) for v in z_coords],
                "heights": [round(float(v), 3) if np.isfinite(v) else None for v in vals.ravel()],
                "baseElevationMeters": round(base, 2),
                "maxRelativeHeightMeters": round(top, 2),
            }


def _flux_grid(location, ring, layers):
    """Read Google's annual solar-flux raster over the selected roof.

    Flux already accounts for roof orientation, weather and modeled shade from
    surrounding obstacles/roof geometry. We return a compact relative grid so
    the browser can keep panels out of materially shaded parts of the roof.
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.mask import mask as rio_mask
        from rasterio.warp import transform_geom
    except ImportError as exc:
        raise RuntimeError("Shade-aware reconstruction requires rasterio and numpy") from exc
    key = _key()
    url = layers.get("annualFluxUrl")
    if not url:
        raise RuntimeError("Google Solar annual flux layer is unavailable")
    sep = "&" if "?" in url else "?"
    response = requests.get(url + sep + "key=" + key, timeout=40)
    response.raise_for_status()
    with rasterio.io.MemoryFile(response.content) as mem:
        with mem.open() as ds:
            geom = _mask_geometry(ring)
            if ds.crs:
                geom = transform_geom("EPSG:4326", ds.crs, geom, precision=8)
            data, transform = rio_mask(ds, [geom], crop=True, filled=False, all_touched=True)
            arr = data[0]
            if arr.size == 0:
                raise RuntimeError("Annual flux crop is empty")
            max_dim = 80
            rows, cols = arr.shape
            step = max(1, int(math.ceil(max(rows, cols) / max_dim)))
            arr = arr[::step, ::step]
            vals = np.asarray(arr.filled(np.nan), dtype=float)
            valid = (~np.ma.getmaskarray(arr)) & np.isfinite(vals) & (vals > 0)
            if valid.sum() < 12:
                raise RuntimeError("Annual flux contains insufficient roof samples")
            p10, p90 = [float(v) for v in np.nanpercentile(vals[valid], [10, 90])]
            span = max(p90 - p10, 1.0)
            relative = np.clip((vals - p10) / span, 0, 1)
            # Below ~65% of the useful roof-flux range is treated as a
            # materially shaded/low-sun zone for panel placement.
            threshold = 0.65
            c_lat, c_lng = _centroid(ring)
            transformer = None
            if ds.crs:
                from pyproj import Transformer
                transformer = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
            xs=[]
            for c in range(arr.shape[1]):
                px, py = transform * (c * step + 0.5 * step, 0.5 * step)
                lng, lat = transformer.transform(px, py) if transformer else (px, py)
                east, _ = _lnglat_to_local_meters(lng, lat, c_lng, c_lat)
                xs.append(east)
            zs=[]
            for r in range(arr.shape[0]):
                px, py = transform * (0.5 * step, r * step + 0.5 * step)
                lng, lat = transformer.transform(px, py) if transformer else (px, py)
                _, north = _lnglat_to_local_meters(lng, lat, c_lng, c_lat)
                zs.append(-north)
            low_x=[]; low_z=[]
            for r in range(arr.shape[0]):
                for c in range(arr.shape[1]):
                    if valid[r,c] and relative[r,c] < threshold:
                        low_x.append(xs[c]); low_z.append(zs[r])
            shade_side = None
            if low_x:
                sx=sum(low_x)/len(low_x); sz=sum(low_z)/len(low_z)
                deg=(math.degrees(math.atan2(sx,-sz))+360)%360
                dirs=["N","NE","E","SE","S","SW","W","NW"]
                shade_side=dirs[int((deg+22.5)//45)%8]
            return {
                "rows": int(arr.shape[0]),
                "cols": int(arr.shape[1]),
                "x": [round(v,3) for v in xs],
                "z": [round(v,3) for v in zs],
                "relativeFlux": [round(float(v),4) if np.isfinite(v) else None for v in relative.ravel()],
                "rawFlux": [round(float(v),2) if np.isfinite(v) else None for v in vals.ravel()],
                "threshold": threshold,
                "minFlux": round(float(np.nanmin(vals[valid])),2),
                "maxFlux": round(float(np.nanmax(vals[valid])),2),
                "shadeSide": shade_side,
                "shadedSamples": len(low_x),
            }

def _proxy_url(location_id, layer):
    return f"/api/roof-model/{location_id}/layer/{layer}"


@roof_model_bp.route("/api/roof-model/<int:location_id>")
@login_required
def roof_model(location_id):
    location = _get_location(location_id)
    if location is None:
        return jsonify(error="Location not found"), 404
    ring = _polygon_coords(location)
    if not ring:
        return jsonify(error="A saved rooftop polygon is required first"), 400
    try:
        high_fidelity = str(request.args.get("high_fidelity", "1")).lower() not in {"0", "false", "no"}
        from app.services import (
            rooftop_design_service,
            rooftop_ingestion_service,
            rooftop_reconstruction_service,
        )

        prototype_fixture = bool(
            current_app.config.get("ROOFTOP_PROTOTYPE_FIXTURE", True)
        )
        design, design_data = rooftop_design_service.get_current_design(
            location_id, current_user.id
        )
        if design is None:
            design = rooftop_design_service.create_revision(
                location_id, current_user.id, {}
            )
            design_data = design.to_dict()

        provider_payload = None
        if not prototype_fixture or _key():
            result = rooftop_ingestion_service.ingest(
                location_id,
                current_user.id,
                providers=["google_solar"],
                include_data_layers=high_fidelity,
            )
            design, design_data = rooftop_design_service.get_current_design(
                location_id, current_user.id
            )
            provider_payload = rooftop_ingestion_service.legacy_roof_model_payload(
                design
            )
            provider_payload["cached"] = result["used_cache"]

        payload = rooftop_reconstruction_service.build_reconstruction(
            location,
            design_data=design_data,
            provider_payload=provider_payload,
            prototype_fixture=prototype_fixture,
        )
        return jsonify(payload), 200 if payload.get("available") else 422
    except Exception as exc:
        current_app.logger.warning("Roof reconstruction unavailable: %s", exc)
        return jsonify(error=str(exc), configured=True), 502


@roof_model_bp.route("/api/roof-model/<int:location_id>/layer/<layer>")
@login_required
def roof_layer(location_id, layer):
    location = _get_location(location_id)
    if location is None:
        return jsonify(error="Location not found"), 404
    if layer not in {"dsm", "rgb", "mask"}:
        return jsonify(error="Unsupported layer"), 400
    ring = _polygon_coords(location)
    if not ring:
        return jsonify(error="A saved rooftop polygon is required first"), 400
    key = _key()
    if not key:
        return jsonify(error="Google Solar API is not configured"), 503
    lat, lng = _centroid(ring)
    try:
        layers = _data_layers(lat, lng)
        field = {"dsm": "dsmUrl", "rgb": "rgbUrl", "mask": "maskUrl"}[layer]
        url = layers.get(field)
        if not url:
            return jsonify(error=f"{layer.upper()} layer unavailable"), 404
        sep = "&" if "?" in url else "?"
        response = requests.get(url + sep + "key=" + key, timeout=35)
        response.raise_for_status()
        return send_file(io.BytesIO(response.content), mimetype="image/tiff", max_age=300)
    except Exception as exc:
        current_app.logger.warning("Roof layer %s unavailable: %s", layer, exc)
        return jsonify(error=str(exc)), 502
