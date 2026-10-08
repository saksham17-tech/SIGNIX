"""Static rooftop map snapshot for PDF reports.

Renders the saved rooftop polygon (+ obstruction patches) as a self-contained
SVG — no external tile service required, so generation stays offline-reliable
and fast (typically a few milliseconds).
"""

from __future__ import annotations

import io
import math
import re
from typing import Any, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


def _ring_from_geojson(polygon_geojson) -> List[Point]:
    """Extract outer ring as ``[(lat, lng), ...]`` from canonical GeoJSON."""
    if not polygon_geojson:
        return []
    if isinstance(polygon_geojson, dict):
        coords = polygon_geojson.get("coordinates") or []
        if not coords:
            return []
        # Polygon: [ring, ...]; MultiPolygon handled elsewhere
        ring = coords[0] if coords and isinstance(coords[0][0], (list, tuple)) else coords
    elif isinstance(polygon_geojson, list):
        ring = polygon_geojson
    else:
        return []
    out: List[Point] = []
    for pt in ring:
        if not pt or len(pt) < 2:
            continue
        lng, lat = float(pt[0]), float(pt[1])
        out.append((lat, lng))
    return out


def _obstruction_rings(obstructions_geojson) -> List[List[Point]]:
    if not obstructions_geojson:
        return []
    rings: List[List[Point]] = []
    if isinstance(obstructions_geojson, dict):
        coords = obstructions_geojson.get("coordinates") or []
        # MultiPolygon: [[[ring]], ...] or list of polygons
        for poly in coords:
            if not poly:
                continue
            ring = poly[0] if poly and isinstance(poly[0][0], (list, tuple)) else poly
            parsed = _ring_from_geojson(ring)
            if len(parsed) >= 3:
                rings.append(parsed)
    elif isinstance(obstructions_geojson, list):
        for item in obstructions_geojson:
            parsed = _ring_from_geojson(item)
            if len(parsed) >= 3:
                rings.append(parsed)
    return rings


def _project(
    points: Sequence[Point],
    width: float,
    height: float,
    pad: float = 16.0,
) -> List[Tuple[float, float]]:
    """Equirectangular project lat/lng into SVG pixel space (y down)."""
    if not points:
        return []
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    # Avoid zero span
    if abs(max_lat - min_lat) < 1e-9:
        max_lat += 1e-5
        min_lat -= 1e-5
    if abs(max_lng - min_lng) < 1e-9:
        max_lng += 1e-5
        min_lng -= 1e-5
    # Mid-latitude cosine correction for aspect
    mid_lat = (min_lat + max_lat) / 2.0
    cos_lat = max(math.cos(math.radians(mid_lat)), 0.3)
    span_x = (max_lng - min_lng) * cos_lat
    span_y = max_lat - min_lat
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    scale = min(inner_w / span_x, inner_h / span_y)
    # Center
    used_w = span_x * scale
    used_h = span_y * scale
    ox = pad + (inner_w - used_w) / 2.0
    oy = pad + (inner_h - used_h) / 2.0

    out = []
    for lat, lng in points:
        x = ox + (lng - min_lng) * cos_lat * scale
        y = oy + (max_lat - lat) * scale  # north up
        out.append((x, y))
    return out


def _path_d(points: Sequence[Tuple[float, float]]) -> str:
    if not points:
        return ""
    parts = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.2f} {y:.2f}")
    parts.append("Z")
    return " ".join(parts)


def render_rooftop_svg(
    polygon_geojson,
    obstructions_geojson=None,
    width: int = 520,
    height: int = 320,
    title: str = "Rooftop outline",
) -> Optional[str]:
    """Return SVG markup for the rooftop, or None if geometry is missing."""
    roof = _ring_from_geojson(polygon_geojson)
    if len(roof) < 3:
        return None
    obs = _obstruction_rings(obstructions_geojson)
    all_pts = list(roof)
    for r in obs:
        all_pts.extend(r)

    # Project all points in one frame so roof + obs share scale
    # Project roof/obs separately using global bounds
    lats = [p[0] for p in all_pts]
    lngs = [p[1] for p in all_pts]
    bounds_pts = [(min(lats), min(lngs)), (max(lats), max(lngs))]
    # Use full set for projection
    projected_all = _project(all_pts, width, height)
    # Map identity by re-projecting each ring with same bounds via full list order
    # Simpler: project each ring with helper that takes global min/max
    def project_ring(ring):
        return _project_with_bounds(ring, all_pts, width, height)

    roof_xy = project_ring(roof)
    obs_xy = [project_ring(r) for r in obs]

    obs_paths = "\n".join(
        f'<path d="{_path_d(r)}" fill="#f0c14b" fill-opacity="0.55" '
        f'stroke="#c47a0b" stroke-width="1.5"/>'
        for r in obs_xy
        if r
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">
  <rect width="100%" height="100%" fill="#f4f7fb"/>
  <rect x="1" y="1" width="{width-2}" height="{height-2}" fill="none" stroke="#d0d7e2" stroke-width="1"/>
  <path d="{_path_d(roof_xy)}" fill="#3d2570" fill-opacity="0.18" stroke="#3d2570" stroke-width="2.5"/>
  {obs_paths}
  <text x="12" y="{height - 12}" font-family="Helvetica, Arial, sans-serif" font-size="11" fill="#666666">{title}</text>
  <text x="{width - 12}" y="18" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="10" fill="#888888">N</text>
</svg>'''
    return svg


def _project_with_bounds(
    ring: Sequence[Point],
    all_pts: Sequence[Point],
    width: float,
    height: float,
    pad: float = 16.0,
) -> List[Tuple[float, float]]:
    if not ring or not all_pts:
        return []
    lats = [p[0] for p in all_pts]
    lngs = [p[1] for p in all_pts]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    if abs(max_lat - min_lat) < 1e-9:
        max_lat += 1e-5
        min_lat -= 1e-5
    if abs(max_lng - min_lng) < 1e-9:
        max_lng += 1e-5
        min_lng -= 1e-5
    mid_lat = (min_lat + max_lat) / 2.0
    cos_lat = max(math.cos(math.radians(mid_lat)), 0.3)
    span_x = (max_lng - min_lng) * cos_lat
    span_y = max_lat - min_lat
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    scale = min(inner_w / span_x, inner_h / span_y)
    used_w = span_x * scale
    used_h = span_y * scale
    ox = pad + (inner_w - used_w) / 2.0
    oy = pad + (inner_h - used_h) / 2.0
    out = []
    for lat, lng in ring:
        x = ox + (lng - min_lng) * cos_lat * scale
        y = oy + (max_lat - lat) * scale
        out.append((x, y))
    return out



def _parse_svg_path_d(svg_markup: str) -> List[Tuple[float, float]]:
    """Extract first path d= coordinates (ignores fill-opacity etc. attribute numbers)."""
    if not svg_markup:
        return []
    m = re.search(r'\bd="([^"]+)"', svg_markup)
    if not m:
        return []
    d = m.group(1)
    nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)]
    if len(nums) < 6:
        return []
    return list(zip(nums[0::2], nums[1::2]))


def svg_to_png_bytes(svg_markup: str, scale: int = 2) -> Optional[bytes]:
    """Rasterize SVG → PNG for ReportLab embedding. Returns None if unavailable."""
    if not svg_markup:
        return None
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg_markup.encode("utf-8"))
    except Exception:
        pass
    # Pillow fallback — parse only path d= so attribute numbers never corrupt geometry
    try:
        from PIL import Image, ImageDraw

        w_m = re.search(r'width="(\d+)"', svg_markup)
        h_m = re.search(r'height="(\d+)"', svg_markup)
        w = int(w_m.group(1)) if w_m else 520
        h = int(h_m.group(1)) if h_m else 320
        img = Image.new("RGB", (w * scale, h * scale), "#f4f7fb")
        draw = ImageDraw.Draw(img)

        # All path d= attributes (roof first, then obstructions)
        for i, m in enumerate(re.finditer(r'\bd="([^"]+)"', svg_markup)):
            d = m.group(1)
            nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)]
            if len(nums) < 6:
                continue
            pts = list(zip(nums[0::2], nums[1::2]))
            scaled = [(x * scale, y * scale) for x, y in pts]
            if i == 0:
                draw.polygon(scaled, fill="#d8d0ec", outline="#3d2570")
                # thicker outline
                draw.line(scaled + [scaled[0]], fill="#3d2570", width=max(2, scale))
            else:
                draw.polygon(scaled, fill="#f0c14b", outline="#c47a0b")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def render_rooftop_png(
    polygon_geojson,
    obstructions_geojson=None,
    width: int = 520,
    height: int = 320,
    scale: int = 2,
) -> Optional[bytes]:
    """Direct PNG of rooftop geometry (no SVG parse). Preferred for PDF embedding."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        svg = render_rooftop_svg(polygon_geojson, obstructions_geojson, width, height)
        return svg_to_png_bytes(svg) if svg else None

    roof = _ring_from_geojson(polygon_geojson)
    if len(roof) < 3:
        return None
    obs = _obstruction_rings(obstructions_geojson)
    all_pts = list(roof)
    for r in obs:
        all_pts.extend(r)

    def project_ring(ring):
        return _project_with_bounds(ring, all_pts, width, height)

    roof_xy = project_ring(roof)
    if len(roof_xy) < 3:
        return None

    img = Image.new("RGB", (width * scale, height * scale), "#f4f7fb")
    draw = ImageDraw.Draw(img)
    # border
    draw.rectangle(
        [scale, scale, width * scale - scale, height * scale - scale],
        outline="#d0d7e2",
        width=max(1, scale // 2),
    )

    def sc(pts):
        return [(x * scale, y * scale) for x, y in pts]

    roof_s = sc(roof_xy)
    draw.polygon(roof_s, fill="#d8d0ec", outline="#3d2570")
    draw.line(roof_s + [roof_s[0]], fill="#3d2570", width=max(2, scale))
    for r in obs:
        oxy = project_ring(r)
        if len(oxy) >= 3:
            os_ = sc(oxy)
            draw.polygon(os_, fill="#f0c14b", outline="#c47a0b")
            draw.line(os_ + [os_[0]], fill="#c47a0b", width=max(1, scale))

    # North label
    try:
        draw.text((width * scale - 28 * scale // 2, 8 * scale), "N", fill="#888888")
    except Exception:
        pass

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()



def make_qr_png(url: str, box_size: int = 4, border: int = 2) -> Optional[bytes]:
    """PNG bytes for a QR code pointing at url."""
    if not url:
        return None
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=border,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#3d2570", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def estimate_live_url(location_id, base_url: Optional[str] = None) -> str:
    """Canonical live estimate URL for a saved location."""
    base = (base_url or "").rstrip("/")
    if not base:
        base = ""
    path = f"/report/{int(location_id)}"
    return f"{base}{path}" if base else path
