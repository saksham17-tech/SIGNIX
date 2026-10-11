"""Normalized, renderer-facing rooftop reconstruction contract.

The user polygon is always authoritative. Provider polygons are clipped to it.
When prototype fixture mode is enabled, deterministic simulated roof planes are
derived from that polygon and are labelled as simulated provider data.
"""

from __future__ import annotations

import math
from copy import deepcopy


def _open_ring(geojson):
    if not isinstance(geojson, dict) or geojson.get("type") != "Polygon":
        return []
    coordinates = geojson.get("coordinates") or []
    ring = coordinates[0] if coordinates else []
    points = [[float(point[0]), float(point[1])] for point in ring if len(point) >= 2]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _closed(points):
    points = [[round(point[0], 8), round(point[1], 8)] for point in points]
    return points + [points[0]] if points and points[0] != points[-1] else points


def _signed_area(points):
    return sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    ) / 2


def _centroid(points):
    # Roof polygons are tiny relative to their WGS84 coordinate values. Doing
    # the shoelace calculation directly around longitude 77 / latitude 28 can
    # lose enough precision to move the result outside the selected building.
    # Translate close to zero first, then add the geographic origin back.
    origin_x, origin_y = points[0]
    translated = [[point[0] - origin_x, point[1] - origin_y] for point in points]
    area = _signed_area(translated)
    if abs(area) < 1e-16:
        return [sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)]
    cx = cy = 0.0
    for index, point in enumerate(translated):
        following = translated[(index + 1) % len(translated)]
        cross = point[0] * following[1] - following[0] * point[1]
        cx += (point[0] + following[0]) * cross
        cy += (point[1] + following[1]) * cross
    return [origin_x + cx / (6 * area), origin_y + cy / (6 * area)]


def _local(points, origin):
    metres_lat = 111_320.0
    metres_lng = metres_lat * math.cos(math.radians(origin[1]))
    return [[(p[0] - origin[0]) * metres_lng, (p[1] - origin[1]) * metres_lat] for p in points]


def _metric_area(points, origin):
    return abs(_signed_area(_local(points, origin)))


def _geographic(points, origin):
    metres_lat = 111_320.0
    metres_lng = metres_lat * math.cos(math.radians(origin[1]))
    return [
        [origin[0] + point[0] / metres_lng, origin[1] + point[1] / metres_lat]
        for point in points
    ]


def _clip_half_plane(points, normal, threshold, keep_greater):
    """Clip a local-metre polygon against one side of an infinite line."""
    output = []
    previous = points[-1]

    def value(point):
        return point[0] * normal[0] + point[1] * normal[1]

    def inside(point):
        coordinate = value(point)
        return coordinate >= threshold if keep_greater else coordinate <= threshold

    for current in points:
        previous_inside, current_inside = inside(previous), inside(current)
        if previous_inside != current_inside:
            delta = value(current) - value(previous)
            if abs(delta) > 1e-12:
                fraction = (threshold - value(previous)) / delta
                intersection = [
                    previous[0] + fraction * (current[0] - previous[0]),
                    previous[1] + fraction * (current[1] - previous[1]),
                ]
                output.append(intersection)
        if current_inside:
            output.append(current)
        previous = current
    return output


def _principal_axes(points):
    center = _centroid(points)
    xx = yy = xy = 0.0
    for point in points:
        dx, dy = point[0] - center[0], point[1] - center[1]
        xx += dx * dx
        yy += dy * dy
        xy += dx * dy
    angle = 0.5 * math.atan2(2 * xy, xx - yy)
    long_axis = [math.cos(angle), math.sin(angle)]
    short_axis = [-long_axis[1], long_axis[0]]
    long_values = [point[0] * long_axis[0] + point[1] * long_axis[1] for point in points]
    short_values = [point[0] * short_axis[0] + point[1] * short_axis[1] for point in points]
    if max(long_values) - min(long_values) < max(short_values) - min(short_values):
        long_axis, short_axis = short_axis, long_axis
        long_values, short_values = short_values, long_values
    return center, long_axis, short_axis, long_values, short_values


def _azimuth_from_vector(east, north):
    return (math.degrees(math.atan2(east, north)) + 360) % 360


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if ((current[1] > y) != (previous[1] > y)
                and x < (previous[0] - current[0]) * (y - current[1])
                / ((previous[1] - current[1]) or 1e-15) + current[0]):
            inside = not inside
    return inside


def _distance_to_edges(point, ring):
    minimum = math.inf
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        fraction = 0.0 if not length_squared else max(
            0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared)
        )
        closest = [start[0] + fraction * dx, start[1] + fraction * dy]
        minimum = min(minimum, math.hypot(point[0] - closest[0], point[1] - closest[1]))
    return minimum


def _point_in_triangle(point, a, b, c):
    def cross(p1, p2, p3):
        return (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])
    values = [cross(a, b, point), cross(b, c, point), cross(c, a, point)]
    return not (any(v < -1e-14 for v in values) and any(v > 1e-14 for v in values))


def _triangulate(points):
    """Ear-clipping triangulation for a simple authoritative polygon."""
    if len(points) < 3:
        return []
    vertices = list(range(len(points)))
    if _signed_area(points) < 0:
        vertices.reverse()
    triangles = []
    guard = len(vertices) * len(vertices)
    while len(vertices) > 3 and guard:
        guard -= 1
        clipped = False
        for offset, current in enumerate(vertices):
            previous = vertices[offset - 1]
            following = vertices[(offset + 1) % len(vertices)]
            a, b, c = points[previous], points[current], points[following]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if cross <= 1e-14:
                continue
            if any(
                _point_in_triangle(points[index], a, b, c)
                for index in vertices
                if index not in {previous, current, following}
            ):
                continue
            triangles.append([a, b, c])
            del vertices[offset]
            clipped = True
            break
        if not clipped:
            break
    if len(vertices) == 3:
        triangles.append([points[index] for index in vertices])
    return triangles


def _clip_to_convex(subject, clipper):
    """Sutherland-Hodgman clipping; clipper is a CCW triangle."""
    output = list(subject)
    if _signed_area(clipper) < 0:
        clipper = list(reversed(clipper))
    for index, edge_start in enumerate(clipper):
        edge_end = clipper[(index + 1) % len(clipper)]
        source = output
        output = []
        if not source:
            break

        def inside(point):
            return ((edge_end[0] - edge_start[0]) * (point[1] - edge_start[1])
                    - (edge_end[1] - edge_start[1]) * (point[0] - edge_start[0])) >= -1e-14

        def intersection(a, b):
            dc = [edge_start[0] - edge_end[0], edge_start[1] - edge_end[1]]
            dp = [a[0] - b[0], a[1] - b[1]]
            n1 = edge_start[0] * edge_end[1] - edge_start[1] * edge_end[0]
            n2 = a[0] * b[1] - a[1] * b[0]
            denominator = dc[0] * dp[1] - dc[1] * dp[0]
            if abs(denominator) < 1e-18:
                return list(b)
            return [(n1 * dp[0] - n2 * dc[0]) / denominator,
                    (n1 * dp[1] - n2 * dc[1]) / denominator]

        previous = source[-1]
        for current in source:
            if inside(current):
                if not inside(previous):
                    output.append(intersection(previous, current))
                output.append(current)
            elif inside(previous):
                output.append(intersection(previous, current))
            previous = current
    return output


def clip_polygon_to_authoritative(subject, authoritative):
    fragments = []
    for triangle in _triangulate(authoritative):
        clipped = _clip_to_convex(subject, triangle)
        if len(clipped) >= 3 and abs(_signed_area(clipped)) > 1e-14:
            fragments.append(clipped)
    return fragments


def _surface(surface_id, points, origin, pitch, azimuth, source, confidence,
             parent_id=None, reference_height=None, reference_point=None):
    projected_area = _metric_area(points, origin)
    pitch_value = float(pitch or 0)
    azimuth_value = float(azimuth or 0) % 360
    valid = (
        len(points) >= 3
        and projected_area >= .02
        and math.isfinite(pitch_value)
        and 0 <= pitch_value < 89
        and math.isfinite(azimuth_value)
    )
    slope_factor = 1 / max(math.cos(math.radians(pitch_value)), .01)
    reference = reference_point or _centroid(points)
    normal = [
        round(math.sin(math.radians(pitch_value)) * math.sin(math.radians(azimuth_value)), 8),
        round(math.sin(math.radians(pitch_value)) * math.cos(math.radians(azimuth_value)), 8),
        round(math.cos(math.radians(pitch_value)), 8),
    ]
    return {
        "id": surface_id,
        "parent_surface_id": parent_id or surface_id,
        "polygon_geojson": {"type": "Polygon", "coordinates": [_closed(points)]},
        "projected_area_sqm": round(projected_area, 2),
        "area_sqm": round(projected_area * slope_factor, 2),
        "usable_area_sqm": 0.0,
        "pitch_degrees": round(pitch_value, 2),
        "azimuth_degrees": round(azimuth_value, 2),
        "plane_normal_enu": normal,
        "reference_point": {"longitude": reference[0], "latitude": reference[1]},
        "reference_height_meters": (
            round(float(reference_height), 3) if reference_height is not None else None
        ),
        "source": source,
        "confidence": confidence,
        "panel_count": None,
        "is_valid": valid,
        "validation_errors": [] if valid else ["Surface geometry or plane metadata is invalid"],
    }


def _fixture_surfaces(boundary, metadata, origin):
    explicit_type = (metadata or {}).get("roof_type")
    pitch_value = (metadata or {}).get("pitch_degrees")
    azimuth_value = (metadata or {}).get("azimuth_degrees")
    reference_height = (metadata or {}).get("height_meters")
    if reference_height is None:
        reference_height = (metadata or {}).get("elevation_meters")

    # A footprint describes plan geometry only. Never infer pitch or roof type
    # from its shape. Pitched fixture geometry is allowed only when the saved
    # normalized metadata supplies the complete plane description.
    reliable_pitched_metadata = (
        explicit_type in {"gable", "shed"}
        and pitch_value is not None
        and azimuth_value is not None
        and reference_height is not None
    )
    if explicit_type == "flat" or not reliable_pitched_metadata:
        return [_surface(
            "surface-1", boundary, origin, 0, 0,
            "prototype_fixture", "simulated", reference_height=reference_height,
        )], "flat"

    local = _local(boundary, origin)
    roof_type = explicit_type
    pitch = float(pitch_value)
    azimuth = float(azimuth_value)
    if roof_type == "shed":
        return [_surface(
            "surface-1", boundary, origin, pitch, azimuth,
            "prototype_fixture", "simulated", reference_height=reference_height,
        )], roof_type

    if roof_type == "gable":
        center, _, short_axis, _, short_values = _principal_axes(local)
        ridge = (max(short_values) + min(short_values)) / 2
        halves = [
            _clip_half_plane(local, short_axis, ridge, False),
            _clip_half_plane(local, short_axis, ridge, True),
        ]
        azimuths = [
            _azimuth_from_vector(-short_axis[0], -short_axis[1]),
            _azimuth_from_vector(short_axis[0], short_axis[1]),
        ]
        center_projection = center[0] * short_axis[0] + center[1] * short_axis[1]
        ridge_reference_local = [
            center[0] + (ridge - center_projection) * short_axis[0],
            center[1] + (ridge - center_projection) * short_axis[1],
        ]
        ridge_reference = _geographic([ridge_reference_local], origin)[0]
        surfaces = []
        for index, half in enumerate(halves):
            if len(half) < 3:
                continue
            surfaces.append(_surface(
                f"surface-{index + 1}", _geographic(half, origin), origin,
                pitch, azimuths[index], "prototype_fixture", "simulated",
                reference_height=reference_height,
                reference_point=ridge_reference,
            ))
        if surfaces:
            return surfaces, roof_type
    return [_surface(
        "surface-1", boundary, origin, 0, 0,
        "prototype_fixture", "simulated", reference_height=reference_height,
    )], "flat"


def _provider_surfaces(provider_payload, boundary, origin):
    surfaces = []
    triangles = _triangulate(boundary)
    for segment_index, segment in enumerate(((provider_payload or {}).get("roof") or {}).get("segments") or []):
        footprint = segment.get("footprint") or []
        if len(footprint) < 3:
            continue
        for fragment_index, triangle in enumerate(triangles):
            clipped = _clip_to_convex(footprint, triangle)
            if len(clipped) < 3 or _metric_area(clipped, origin) < .02:
                continue
            segment_id = f"provider-surface-{segment.get('segmentIndex', segment_index + 1)}"
            center = segment.get("center") or {}
            reference_point = None
            if center.get("longitude") is not None and center.get("latitude") is not None:
                reference_point = [float(center["longitude"]), float(center["latitude"])]
            surfaces.append(_surface(
                f"{segment_id}-fragment-{fragment_index + 1}", clipped, origin,
                segment.get("pitchDegrees"), segment.get("azimuthDegrees"),
                provider_payload.get("source") or "provider", "provider",
                parent_id=segment_id,
                reference_height=segment.get("planeHeightAtCenterMeters"),
                reference_point=reference_point,
            ))
    return surfaces


def _structures(design_data, boundary, origin):
    result = []
    authoritative = (design_data or {}).get("authoritative") or {}
    collections = [
        ("manual", authoritative.get("manual_obstructions") or {}, "Manual obstruction", "user", "authoritative"),
        ("accepted", authoritative.get("accepted_obstructions") or {}, "Accepted structure", "accepted_advisory", "accepted"),
    ]
    for prefix, collection, label, source, confidence in collections:
        for index, polygon in enumerate(collection.get("coordinates") or []):
            ring = polygon[0] if polygon else []
            for fragment_index, clipped in enumerate(clip_polygon_to_authoritative(ring, boundary)):
                result.append({
                    "id": f"{prefix}-structure-{index + 1}-{fragment_index + 1}",
                    "type": label,
                    "footprint_geojson": {"type": "Polygon", "coordinates": [_closed(clipped)]},
                    "height_meters": None,
                    # Visual-only conservative extrusion. The measured height
                    # remains explicitly unavailable in the public contract.
                    "render_height_meters": .35,
                    "source": source,
                    "confidence": confidence,
                    "is_obstruction": True,
                    "footprint_area_sqm": round(_metric_area(clipped, origin), 2),
                })
    return result


def _apply_usable_areas(surfaces, boundary, structures, origin, setback=.32):
    """Estimate usable sloped area after boundary setbacks and obstructions.

    A deterministic local-metre sampling grid is used because the project does
    not depend on a polygon-offset library. The resolution is capped relative
    to roof size, keeping the value stable without turning it into a financial
    or panel-count calculation.
    """
    boundary_local = _local(boundary, origin)
    obstruction_rings = [
        _local(_open_ring(item.get("footprint_geojson")), origin)
        for item in structures
        if item.get("is_obstruction")
    ]
    for surface in surfaces:
        if not surface.get("is_valid"):
            surface["usable_area_sqm"] = 0.0
            continue
        ring = _local(_open_ring(surface.get("polygon_geojson")), origin)
        min_x, max_x = min(p[0] for p in ring), max(p[0] for p in ring)
        min_y, max_y = min(p[1] for p in ring), max(p[1] for p in ring)
        span = max(max_x - min_x, max_y - min_y)
        step = max(.10, span / 180)
        usable_cells = 0
        rows = max(1, int(math.ceil((max_y - min_y) / step)))
        cols = max(1, int(math.ceil((max_x - min_x) / step)))
        for row in range(rows):
            y = min_y + (row + .5) * step
            for column in range(cols):
                x = min_x + (column + .5) * step
                point = [x, y]
                if not _point_in_ring(point, ring) or not _point_in_ring(point, boundary_local):
                    continue
                if (_distance_to_edges(point, ring) < setback
                        or _distance_to_edges(point, boundary_local) < setback):
                    continue
                if any(_point_in_ring(point, obstruction) for obstruction in obstruction_rings):
                    continue
                usable_cells += 1
        projected_usable = usable_cells * step * step
        slope_factor = 1 / max(
            math.cos(math.radians(float(surface.get("pitch_degrees") or 0))), .01
        )
        surface["usable_area_sqm"] = round(
            min(float(surface["area_sqm"]), projected_usable * slope_factor), 2
        )


def build_reconstruction(location, design_data=None, provider_payload=None,
                         prototype_fixture=True):
    authoritative_geojson = ((design_data or {}).get("authoritative") or {}).get("roof_boundary") or location.polygon_geojson
    boundary = _open_ring(authoritative_geojson)
    if len(boundary) < 3:
        return {"available": False, "mode": "invalid", "error": "A valid authoritative rooftop boundary is required"}
    origin = _centroid(boundary)
    metadata = ((design_data or {}).get("authoritative") or {}).get("roof_metadata") or {}
    provider_surfaces = _provider_surfaces(provider_payload, boundary, origin) if (provider_payload or {}).get("available") else []
    invalid_provider_surfaces = sum(not surface.get("is_valid", False) for surface in provider_surfaces)
    surfaces = provider_surfaces
    if surfaces and any(surface.get("is_valid") for surface in surfaces):
        mode, status, roof_type = "provider", {
            "label": "Location-specific rooftop data",
            "detail": "Provider roof surfaces clipped to the user-drawn boundary",
            "tone": "provider",
        }, metadata.get("roof_type") or "mixed"
    elif provider_payload is not None:
        surfaces = [_surface("user-footprint", boundary, origin, 0, metadata.get("azimuth_degrees") or 0, "user_boundary", "authoritative")]
        mode, roof_type, status = "user_fallback", "flat", {
            "label": "Using the user-drawn roof fallback",
            "detail": "Location-specific data unavailable",
            "tone": "fallback",
        }
    elif prototype_fixture:
        surfaces, roof_type = _fixture_surfaces(boundary, metadata, origin)
        mode, status = "prototype_fixture", {
            "label": "Prototype",
            "detail": "simulated roof geometry; roof pitch not verified",
            "tone": "prototype",
        }
    else:
        surfaces = [_surface("user-footprint", boundary, origin, 0, metadata.get("azimuth_degrees") or 0, "user_boundary", "authoritative")]
        mode, roof_type, status = "user_fallback", "flat", {
            "label": "Using the user-drawn roof fallback",
            "detail": "Location-specific data unavailable",
            "tone": "fallback",
        }
    structures = _structures(design_data, boundary, origin)
    _apply_usable_areas(surfaces, boundary, structures, origin)
    return {
        "available": True,
        "schema_version": "1.0",
        "mode": mode,
        "roof_type": roof_type,
        "status_badge": status,
        "projection_origin": {"longitude": origin[0], "latitude": origin[1]},
        "authoritative_boundary_geojson": deepcopy(authoritative_geojson),
        "surfaces": surfaces,
        "structures": structures,
        "imagery": {"available": False, "source": None},
        "shade": (provider_payload or {}).get("shade"),
        "dsmGrid": (provider_payload or {}).get("dsmGrid"),
        "constraints": {
            "boundary_authoritative": True,
            "provider_geometry_clipped": True,
            "panel_width_meters": 1.134,
            "panel_height_meters": 1.762,
            "edge_setback_meters": .32,
            "panel_gap_meters": .07,
        },
        "diagnostics": {
            "provider_status": (provider_payload or {}).get("status", "not_requested"),
            "provider_error": (provider_payload or {}).get("error"),
            "surface_fragments": len(surfaces),
            "invalid_provider_surfaces": invalid_provider_surfaces,
            "projected_boundary_area_sqm": round(_metric_area(boundary, origin), 2),
            "projected_surface_area_sqm": round(
                sum(float(surface.get("projected_area_sqm") or 0) for surface in surfaces), 2
            ),
        },
    }
