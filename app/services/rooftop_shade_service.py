"""Deterministic roof-cell classification for visualization and panel layout.

The authoritative user boundary is GeoJSON ``[longitude, latitude]``.  Grid
coordinates use the renderer's local frame: east is +x and north is -z.
This module classifies geometry; it does not change solar/financial estimates.
"""

from __future__ import annotations

import math


SCHEMA_VERSION = "1.0"
DEFAULT_RESOLUTION_METERS = 0.5
MAX_GRID_CELLS = 40_000


def _open_ring(geojson):
    if not isinstance(geojson, dict) or geojson.get("type") != "Polygon":
        return []
    coordinates = geojson.get("coordinates") or []
    ring = coordinates[0] if coordinates else []
    points = [[float(point[0]), float(point[1])] for point in ring if len(point) >= 2]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _world_ring(geojson, origin):
    metres_lat = 111_320.0
    metres_lng = metres_lat * math.cos(math.radians(origin[1]))
    return [
        [(lng - origin[0]) * metres_lng, -(lat - origin[1]) * metres_lat]
        for lng, lat in _open_ring(geojson)
    ]


def _point_in_ring(point, ring):
    if len(ring) < 3:
        return False
    x, z = point
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if ((current[1] > z) != (previous[1] > z)
                and x < (previous[0] - current[0]) * (z - current[1])
                / ((previous[1] - current[1]) or 1e-15) + current[0]):
            inside = not inside
    return inside


def _polygon_area(ring):
    return abs(sum(
        ring[index][0] * ring[(index + 1) % len(ring)][1]
        - ring[(index + 1) % len(ring)][0] * ring[index][1]
        for index in range(len(ring))
    ) / 2)


def _distance_to_edges(point, ring):
    minimum = math.inf
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        dx, dz = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dz * dz
        fraction = 0.0 if not length_squared else max(
            0.0,
            min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / length_squared),
        )
        closest = [start[0] + fraction * dx, start[1] + fraction * dz]
        minimum = min(minimum, math.hypot(point[0] - closest[0], point[1] - closest[1]))
    return minimum


def _sample_flux(grid, x, z):
    """Nearest-cell sampling avoids fabricating data across missing raster cells."""
    if not isinstance(grid, dict):
        return None
    xs, zs = grid.get("x") or [], grid.get("z") or []
    values = grid.get("relativeFlux") or []
    rows, cols = int(grid.get("rows") or 0), int(grid.get("cols") or 0)
    if rows < 1 or cols < 1 or len(xs) != cols or len(zs) != rows or len(values) < rows * cols:
        return None
    if x < min(xs) or x > max(xs) or z < min(zs) or z > max(zs):
        return None
    column = min(range(cols), key=lambda item: abs(float(xs[item]) - x))
    row = min(range(rows), key=lambda item: abs(float(zs[item]) - z))
    value = values[row * cols + column]
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _has_defensible_flux(grid):
    if not isinstance(grid, dict):
        return False
    rows, cols = int(grid.get("rows") or 0), int(grid.get("cols") or 0)
    xs, zs, values = grid.get("x") or [], grid.get("z") or [], grid.get("relativeFlux") or []
    if rows < 1 or cols < 1 or len(xs) != cols or len(zs) != rows or len(values) < rows * cols:
        return False
    for value in values:
        try:
            if math.isfinite(float(value)):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _convex_hull(points):
    ordered = sorted({(float(point[0]), float(point[1])) for point in points})
    if len(ordered) <= 2:
        return [list(point) for point in ordered]

    def cross(origin, first, second):
        return ((first[0] - origin[0]) * (second[1] - origin[1])
                - (first[1] - origin[1]) * (second[0] - origin[0]))

    lower = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return [list(point) for point in lower[:-1] + upper[:-1]]


def _shadow_rings(structures, sun):
    """Return simple translated shadow footprints only for measured heights."""
    if not isinstance(sun, dict):
        return []
    try:
        elevation = float(sun["elevation_degrees"])
        azimuth = float(sun["azimuth_degrees"])
    except (KeyError, TypeError, ValueError):
        return []
    if not (0 < elevation < 90):
        return []
    direction = math.radians(azimuth)
    shadows = []
    for structure in structures:
        height = structure.get("height_meters")
        try:
            height = float(height)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(height) or height <= 0:
            continue
        ring = structure.get("_world_ring") or []
        length = min(height / math.tan(math.radians(elevation)), 100.0)
        # Azimuth is the direction toward the sun; the shadow points away.
        dx, dz = -math.sin(direction) * length, math.cos(direction) * length
        shadows.append(_convex_hull(
            ring + [[point[0] + dx, point[1] + dz] for point in ring]
        ))
    return shadows


def build_shade_analysis(boundary_geojson, projection_origin, structures=None,
                         provider_flux=None, edge_setback_meters=.32,
                         obstruction_setback_meters=.30,
                         resolution_meters=DEFAULT_RESOLUTION_METERS,
                         sun=None, demo_simulation=False):
    """Build a bounded, deterministic roof classification grid.

    Unknown shade is retained as ``unknown`` and conservatively excluded from
    panel placement.  It is never promoted to verified usable roof.
    """
    origin = [float(projection_origin[0]), float(projection_origin[1])]
    boundary = _world_ring(boundary_geojson, origin)
    if len(boundary) < 3:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "insufficient_data",
            "diagnostics": ["A valid authoritative roof polygon is required"],
        }

    prepared_structures = []
    for structure in structures or []:
        ring = _world_ring(structure.get("footprint_geojson"), origin)
        if len(ring) >= 3 and structure.get("is_obstruction", True):
            item = dict(structure)
            item["_world_ring"] = ring
            prepared_structures.append(item)

    min_x, max_x = min(p[0] for p in boundary), max(p[0] for p in boundary)
    min_z, max_z = min(p[1] for p in boundary), max(p[1] for p in boundary)
    requested_resolution = max(.20, min(2.0, float(resolution_meters)))
    resolution = requested_resolution
    rows = max(1, int(math.ceil((max_z - min_z) / resolution)))
    cols = max(1, int(math.ceil((max_x - min_x) / resolution)))
    if rows * cols > MAX_GRID_CELLS:
        resolution *= math.sqrt((rows * cols) / MAX_GRID_CELLS)
        rows = max(1, int(math.ceil((max_z - min_z) / resolution)))
        cols = max(1, int(math.ceil((max_x - min_x) / resolution)))
        while rows * cols > MAX_GRID_CELLS:
            resolution *= 1.005
            rows = max(1, int(math.ceil((max_z - min_z) / resolution)))
            cols = max(1, int(math.ceil((max_x - min_x) / resolution)))

    has_provider_flux = _has_defensible_flux(provider_flux)
    threshold = float((provider_flux or {}).get("threshold") or .65)
    shadows = _shadow_rings(prepared_structures, sun)
    if has_provider_flux:
        source, status, unknown_policy = "provider_annual_flux", "available", "exclude"
    elif shadows:
        source, status, unknown_policy = "geometric_known_height", "available", "exclude"
    elif demo_simulation:
        source, status, unknown_policy = "explicit_demo_simulation", "simulated", "exclude"
    else:
        source, status, unknown_policy = "none", "insufficient_data", "exclude"

    classes = []
    counts = {name: 0 for name in (
        "usable", "obstruction", "setback", "shaded", "unknown", "outside_roof"
    )}
    flux_values = []
    for row in range(rows):
        z = min_z + (row + .5) * resolution
        for column in range(cols):
            x = min_x + (column + .5) * resolution
            point = [x, z]
            relative_flux = None
            if not _point_in_ring(point, boundary):
                classification = "outside_roof"
            elif any(_point_in_ring(point, item["_world_ring"]) for item in prepared_structures):
                classification = "obstruction"
            elif _distance_to_edges(point, boundary) < edge_setback_meters or any(
                _distance_to_edges(point, item["_world_ring"]) < obstruction_setback_meters
                for item in prepared_structures
            ):
                classification = "setback"
            elif has_provider_flux:
                relative_flux = _sample_flux(provider_flux, x, z)
                classification = (
                    "unknown" if relative_flux is None
                    else "shaded" if relative_flux < threshold
                    else "usable"
                )
            elif shadows:
                classification = "shaded" if any(
                    _point_in_ring(point, shadow) for shadow in shadows
                ) else "usable"
            elif demo_simulation:
                classification = "shaded" if x < min_x + .22 * (max_x - min_x) else "usable"
            else:
                classification = "unknown"
            classes.append(classification)
            flux_values.append(round(relative_flux, 4) if relative_flux is not None else None)
            counts[classification] += 1

    roof_classes = ("usable", "obstruction", "setback", "shaded", "unknown")
    roof_sample_count = sum(counts[name] for name in roof_classes)
    authoritative_roof_area = _polygon_area(boundary)
    # Cell-center classification determines proportions. Scaling those
    # mutually-exclusive proportions to the exact polygon area keeps totals
    # bounded and prevents edge cells or overlapping obstructions from being
    # counted twice.
    areas = {
        name: round(
            authoritative_roof_area * counts[name] / roof_sample_count, 2
        ) if roof_sample_count else 0.0
        for name in roof_classes
    }
    areas["outside_roof"] = round(
        max(0.0, rows * cols * resolution * resolution - authoritative_roof_area), 2
    )
    rounded_total = sum(areas[name] for name in roof_classes)
    if roof_sample_count:
        largest = max(roof_classes, key=lambda name: counts[name])
        areas[largest] = round(
            max(0.0, areas[largest] + authoritative_roof_area - rounded_total), 2
        )
    classified_roof_area = sum(areas[name] for name in roof_classes)
    diagnostics = []
    if has_provider_flux and not (counts["usable"] or counts["shaded"]):
        status = "insufficient_data"
        diagnostics.append("Provider flux has no valid samples overlapping the authoritative roof")
    if status == "insufficient_data" and source == "none":
        diagnostics.append("Shade is unverified: no provider flux or known-height shadow input is available")
    if prepared_structures and not shadows and not provider_flux:
        diagnostics.append("Obstruction heights are unavailable; footprints are excluded but shadows were not inferred")
    if resolution > requested_resolution + 1e-9:
        diagnostics.append("Grid resolution was reduced to remain within the resource limit")

    provenance = dict((provider_flux or {}).get("provenance") or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source": {
            "type": source,
            "confidence": "provider" if has_provider_flux else "derived" if shadows else "simulated" if demo_simulation else "unavailable",
            **provenance,
        },
        "coordinate_frame": "local_east_x_north_negative_z_meters",
        "resolution_meters": round(resolution, 4),
        "origin_x": round(min_x, 4),
        "origin_z": round(min_z, 4),
        "rows": rows,
        "cols": cols,
        "classifications": classes,
        "relative_flux": flux_values,
        "threshold": threshold,
        "placement_policy": {
            # The classification itself stays conservative. The renderer may
            # explicitly select planning_preview, which relaxes only unknown.
            "default_mode": "planning_preview" if status == "insufficient_data" else "conservative",
            "unknown_cells": unknown_policy,
            "modes": {
                "planning_preview": {
                    "unknown_cells": "allow_unverified",
                    "shade_verified": False,
                },
                "conservative": {
                    "unknown_cells": "exclude",
                    "shade_verified": True,
                },
            },
        },
        "classification_precedence": [
            "outside_roof", "obstruction", "setback", "shaded", "unknown", "usable"
        ],
        "area_method": "exclusive_cell_proportions_scaled_to_authoritative_polygon_area",
        "areas_sqm": {
            **areas,
            "classified_roof": round(classified_roof_area, 2),
            "authoritative_roof": round(authoritative_roof_area, 2),
            "geometrically_usable": round(areas["usable"] + areas["shaded"] + areas["unknown"], 2),
        },
        "counts": counts,
        "diagnostics": diagnostics,
        "limitations": [
            "Cell areas approximate the clipped polygon at the stated grid resolution",
            "This classification does not modify saved generation or financial estimates",
        ],
    }
