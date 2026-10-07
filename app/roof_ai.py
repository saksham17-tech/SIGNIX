"""AI-assisted rooftop interpretation for SIGNIX Stage 2.

The service deliberately treats AI output as advisory geometry. The user-drawn
roof polygon remains authoritative, and the 3D renderer performs its own
polygon/obstruction validation before placing PV modules.
"""
from __future__ import annotations

import io
import json
import math
from typing import Any

import requests
from PIL import Image, ImageDraw
from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

roof_ai_bp = Blueprint("roof_ai", __name__)

ESRI_EXPORT_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
)

PROMPT = """
You are a rooftop-solar computer-vision assistant. Inspect the supplied overhead
satellite/aerial image. The image is a square crop around a USER-DRAWN ROOFTOP;
the user-drawn polygon is authoritative and may be irregular.

Return ONLY valid JSON. Coordinates are normalized to a 0..1000 image coordinate
system where (0,0) is top-left and (1000,1000) is bottom-right.

Estimate:
- roof_type: flat, gable, hip, shed, mixed, or unknown
- pitch_degrees: best estimate from 0 to 40; use null if not visually defensible
- ridge_azimuth_degrees: 0..359 clockwise from geographic north; use null if not defensible
- confidence: 0..1
- roof_planes: up to 4 visible roof planes, each with polygon, pitch_degrees,
  azimuth_degrees, confidence
- obstructions: visible objects that should normally be excluded from PV layout,
  such as water tanks, AC outdoor units, stair/lift rooms, skylights, large vents,
  or existing rooftop structures. Each has type, polygon, confidence.

Do NOT invent objects merely because they are common on Indian roofs. If image
quality is insufficient, return an empty list and lower confidence. Do not claim
precise structural measurements from a top-down image.
""".strip()


def _bbox_from_polygon(polygon: list[list[float]], pad_ratio: float = 0.20):
    pts = []
    for p in polygon or []:
        if len(p) < 2:
            continue
        try:
            a, b = float(p[0]), float(p[1])
        except (TypeError, ValueError):
            continue
        # SIGNIX stores drawn rings as [lat, lng].
        lat, lng = (b, a) if abs(a) > 90 and abs(b) <= 90 else (a, b)
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            pts.append((lat, lng))
    if len(pts) < 3:
        raise ValueError("A valid rooftop polygon is required")
    lats = [p[0] for p in pts]
    lngs = [p[1] for p in pts]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    dlat = max(max_lat - min_lat, 0.00012)
    dlng = max(max_lng - min_lng, 0.00012)
    return (
        min_lng - dlng * pad_ratio,
        min_lat - dlat * pad_ratio,
        max_lng + dlng * pad_ratio,
        max_lat + dlat * pad_ratio,
    )


def _lonlat_to_tile(lon, lat, zoom):
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    n = 2 ** zoom
    x = (float(lon) + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _download_esri_tile(x, y, z):
    url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    response = requests.get(url, timeout=12, headers={"User-Agent": "SIGNIX-rooftop-analysis/1.0"})
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("Empty satellite tile")
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def _download_imagery(bbox, polygon=None):
    """Fetch the same Esri World Imagery source used by the map, as a crisp
    local mosaic. Export requests are less reliable than the public tile
    endpoint, so the AI path uses tiles and crops them to the selected roof."""
    min_lng, min_lat, max_lng, max_lat = bbox
    # Zoom 20 gives useful rooftop detail while keeping the mosaic bounded.
    z = 20
    x0f, y0f = _lonlat_to_tile(min_lng, max_lat, z)
    x1f, y1f = _lonlat_to_tile(max_lng, min_lat, z)
    x0, x1 = math.floor(x0f), math.floor(x1f)
    y0, y1 = math.floor(y0f), math.floor(y1f)
    # Avoid pathological requests if a user draws a very large polygon.
    if x1 - x0 > 5 or y1 - y0 > 5:
        z = 19
        x0f, y0f = _lonlat_to_tile(min_lng, max_lat, z)
        x1f, y1f = _lonlat_to_tile(max_lng, min_lat, z)
        x0, x1 = math.floor(x0f), math.floor(x1f)
        y0, y1 = math.floor(y0f), math.floor(y1f)
    if x1 - x0 > 6 or y1 - y0 > 6:
        raise RuntimeError("Selected area is too large for rooftop imagery")

    tile_size = 256
    mosaic = Image.new("RGB", ((x1 - x0 + 1) * tile_size, (y1 - y0 + 1) * tile_size))
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            tile = _download_esri_tile(tx, ty, z)
            mosaic.paste(tile, ((tx - x0) * tile_size, (ty - y0) * tile_size))

    # Pixel coordinates of the requested bbox inside the mosaic.
    left = max(0, int(round((x0f - x0) * tile_size)))
    top = max(0, int(round((y0f - y0) * tile_size)))
    right = min(mosaic.width, int(round((x1f - x0) * tile_size)))
    bottom = min(mosaic.height, int(round((y1f - y0) * tile_size)))
    if right <= left or bottom <= top:
        raise RuntimeError("Satellite crop has invalid dimensions")
    crop = mosaic.crop((left, top, right, bottom)).resize((1024, 1024), Image.Resampling.LANCZOS)

    # Draw the authoritative user roof outline so the vision model knows
    # exactly which building/roof to analyze when neighbouring roofs are close.
    if polygon:
        draw = ImageDraw.Draw(crop)
        pts = []
        for p in polygon:
            if len(p) < 2:
                continue
            a, b = float(p[0]), float(p[1])
            lat, lng = (b, a) if abs(a) > 90 and abs(b) <= 90 else (a, b)
            px = ((lng - min_lng) / max(max_lng - min_lng, 1e-12)) * 1023
            py = ((max_lat - lat) / max(max_lat - min_lat, 1e-12)) * 1023
            pts.append((int(max(0, min(1023, px))), int(max(0, min(1023, py)))))
        if len(pts) >= 3:
            draw.line(pts + [pts[0]], fill=(255, 0, 255), width=7, joint="curve")
            draw.text((18, 18), "USER ROOF BOUNDARY — ANALYZE INSIDE THIS OUTLINE", fill=(255, 0, 255), stroke_width=2, stroke_fill=(0, 0, 0))

    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI response did not contain JSON")
    return json.loads(text[start : end + 1])


def _clamp01(value, default=0.0):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _normalize_points(points):
    out = []
    if not isinstance(points, list):
        return out
    for p in points:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            x = max(0.0, min(1000.0, float(p[0])))
            y = max(0.0, min(1000.0, float(p[1])))
        except (TypeError, ValueError):
            continue
        out.append([round(x, 2), round(y, 2)])
    return out if len(out) >= 3 else []


def _sanitize(data):
    if not isinstance(data, dict):
        raise ValueError("AI response must be an object")
    roof_type = str(data.get("roof_type") or "unknown").lower()
    if roof_type not in {"flat", "gable", "hip", "shed", "mixed", "unknown"}:
        roof_type = "unknown"
    pitch = data.get("pitch_degrees")
    try:
        pitch = max(0.0, min(40.0, float(pitch))) if pitch is not None else None
    except (TypeError, ValueError):
        pitch = None
    az = data.get("ridge_azimuth_degrees")
    try:
        az = float(az) % 360 if az is not None else None
    except (TypeError, ValueError):
        az = None

    planes = []
    for item in data.get("roof_planes") or []:
        if not isinstance(item, dict):
            continue
        poly = _normalize_points(item.get("polygon"))
        if not poly:
            continue
        pp = item.get("pitch_degrees")
        try:
            pp = max(0.0, min(40.0, float(pp))) if pp is not None else pitch
        except (TypeError, ValueError):
            pp = pitch
        pa = item.get("azimuth_degrees")
        try:
            pa = float(pa) % 360 if pa is not None else None
        except (TypeError, ValueError):
            pa = None
        planes.append({
            "polygon": poly,
            "pitch_degrees": pp,
            "azimuth_degrees": pa,
            "confidence": _clamp01(item.get("confidence"), 0.0),
        })

    obstructions = []
    for item in data.get("obstructions") or []:
        if not isinstance(item, dict):
            continue
        poly = _normalize_points(item.get("polygon"))
        confidence = _clamp01(item.get("confidence"), 0.0)
        if not poly or confidence < 0.45:
            continue
        obstructions.append({
            "type": str(item.get("type") or "rooftop structure")[:80],
            "polygon": poly,
            "confidence": confidence,
        })

    return {
        "roof_type": roof_type,
        "pitch_degrees": pitch,
        "ridge_azimuth_degrees": az,
        "confidence": _clamp01(data.get("confidence"), 0.0),
        "roof_planes": planes[:4],
        "obstructions": obstructions[:12],
    }


@roof_ai_bp.post("/api/roof-ai/imagery")
@login_required
def roof_imagery():
    """Return a bounded satellite crop for the 3D visualizer.

    The browser uses this only as a low-opacity visual reference; the saved
    rooftop polygon remains authoritative.
    """
    payload = request.get_json(silent=True) or {}
    polygon = payload.get("polygon")
    if not isinstance(polygon, list) or len(polygon) < 3:
        return jsonify(error="A valid rooftop polygon is required."), 400
    try:
        bbox = _bbox_from_polygon(polygon, pad_ratio=0.08)
        image_bytes = _download_imagery(bbox, polygon=polygon)
        from flask import Response
        return Response(image_bytes, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
    except requests.RequestException:
        return jsonify(error="Satellite imagery is temporarily unavailable."), 502
    except Exception:
        current_app.logger.exception("Roof imagery request failed")
        return jsonify(error="Satellite imagery is temporarily unavailable."), 502


@roof_ai_bp.post("/api/roof-ai/analyze")
@login_required
def analyze_roof():
    payload = request.get_json(silent=True) or {}
    polygon = payload.get("polygon")
    if not isinstance(polygon, list) or len(polygon) < 3:
        return jsonify(error="A valid rooftop polygon is required."), 400

    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify(
            error="AI roof analysis is not configured. Set GEMINI_API_KEY on the server."
        ), 503

    try:
        bbox = _bbox_from_polygon(polygon)
        image_bytes = _download_imagery(bbox, polygon=polygon)
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=current_app.config.get("GEMINI_ROOF_MODEL")
            or current_app.config.get("GEMINI_MODEL", "gemini-3.6-flash"),
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                PROMPT,
            ],
        )
        analysis = _sanitize(_parse_json(response.text))
        analysis["imagery_bbox"] = [round(v, 8) for v in bbox]
        analysis["imagery_provider"] = "Esri World Imagery"
        analysis["model"] = current_app.config.get("GEMINI_ROOF_MODEL") or current_app.config.get("GEMINI_MODEL")
        analysis["user_drawn_roof_remains_authoritative"] = True
        return jsonify(analysis=analysis)
    except requests.RequestException as exc:
        current_app.logger.warning("Roof imagery unavailable: %s", exc)
        return jsonify(error="Satellite imagery could not be retrieved right now. Check the imagery service/network on the deployed server."), 502
    except Exception as exc:
        current_app.logger.exception("AI roof analysis failed: %s", exc)
        return jsonify(error="AI roof analysis is temporarily unavailable."), 502
