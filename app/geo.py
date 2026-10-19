"""Coordinate-order helpers for SIGNIX rooftop geometry.

Leaflet and the existing solar calculation engine use ``[latitude, longitude]``
pairs. Persisted GeoJSON uses the GeoJSON-required ``[longitude, latitude]``
order. Keep conversions at those boundaries instead of relying on coordinate
range heuristics (both values are valid latitude values in much of India).
"""

from __future__ import annotations

from math import cos, hypot, isfinite, radians


class GeometryValidationError(ValueError):
    """Raised when persisted or submitted rooftop geometry is not usable."""


def leaflet_ring_to_geojson(ring):
    """Convert a Leaflet/solar-engine ``[lat, lng]`` ring to GeoJSON positions."""
    points = [
        [float(point[1]), float(point[0])]
        for point in ring or []
        if point and len(point) >= 2
    ]
    if points and points[0] != points[-1]:
        points.append(points[0].copy())
    return points


def geojson_ring_to_latlng(ring):
    """Convert a canonical GeoJSON ``[lng, lat]`` ring to ``[lat, lng]``."""
    return [
        [float(point[1]), float(point[0])]
        for point in ring or []
        if point and len(point) >= 2
    ]


def canonicalize_stored_ring(ring, reference_latitude=None, reference_longitude=None):
    """Return a stored ring in canonical GeoJSON order.

    New records are always canonical. The reference point is used only to read
    legacy SIGNIX rows that stored Leaflet ``[lat, lng]`` pairs in a field named
    ``polygon_geojson``. Comparing both interpretations with the saved location
    avoids the ambiguous and incorrect ``abs(value) > 90`` heuristic.
    """
    points = [
        [float(point[0]), float(point[1])]
        for point in ring or []
        if point and len(point) >= 2
    ]
    if not points:
        return points
    if reference_latitude is None or reference_longitude is None:
        if points[0] != points[-1]:
            points.append(points[0].copy())
        return points

    ref_lat = float(reference_latitude)
    ref_lng = float(reference_longitude)
    mean_first = sum(point[0] for point in points) / len(points)
    mean_second = sum(point[1] for point in points) / len(points)
    canonical_distance = hypot(mean_second - ref_lat, mean_first - ref_lng)
    legacy_distance = hypot(mean_first - ref_lat, mean_second - ref_lng)
    if legacy_distance < canonical_distance:
        points = [[second, first] for first, second in points]
    if points and points[0] != points[-1]:
        points.append(points[0].copy())
    return points


def _orientation(a, b, c, tolerance=1e-14):
    value = ((b[0] - a[0]) * (c[1] - a[1])
             - (b[1] - a[1]) * (c[0] - a[0]))
    if abs(value) <= tolerance:
        return 0
    return 1 if value > 0 else -1


def _on_segment(a, b, point, tolerance=1e-9):
    return (
        min(a[0], b[0]) - tolerance <= point[0] <= max(a[0], b[0]) + tolerance
        and min(a[1], b[1]) - tolerance <= point[1] <= max(a[1], b[1]) + tolerance
    )


def _segments_intersect(a, b, c, d):
    first = _orientation(a, b, c)
    second = _orientation(a, b, d)
    third = _orientation(c, d, a)
    fourth = _orientation(c, d, b)
    if first != second and third != fourth:
        return True
    return (
        (first == 0 and _on_segment(a, b, c))
        or (second == 0 and _on_segment(a, b, d))
        or (third == 0 and _on_segment(c, d, a))
        or (fourth == 0 and _on_segment(c, d, b))
    )


def validate_geojson_ring(ring):
    """Validate and close a simple WGS84 GeoJSON ``[lng, lat]`` ring.

    The active reconstruction and packing pipeline supports one simple outer
    ring. Rejecting degenerate or self-intersecting input here prevents a
    plausible-looking estimate from being persisted for invalid geometry.
    """
    if not isinstance(ring, (list, tuple)):
        raise GeometryValidationError("polygon ring must be an array")
    points = []
    for position in ring:
        if (
            not isinstance(position, (list, tuple))
            or len(position) < 2
            or isinstance(position[0], bool)
            or isinstance(position[1], bool)
        ):
            raise GeometryValidationError("polygon positions must be numeric [longitude, latitude] pairs")
        try:
            longitude, latitude = float(position[0]), float(position[1])
        except (TypeError, ValueError) as exc:
            raise GeometryValidationError(
                "polygon positions must be numeric [longitude, latitude] pairs"
            ) from exc
        if not (isfinite(longitude) and isfinite(latitude)):
            raise GeometryValidationError("polygon coordinates must be finite")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise GeometryValidationError("polygon coordinates are outside WGS84 bounds")
        points.append([longitude, latitude])

    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len({tuple(point) for point in points}) < 3:
        raise GeometryValidationError("polygon ring must contain at least three distinct positions")
    if any(points[index] == points[(index + 1) % len(points)] for index in range(len(points))):
        raise GeometryValidationError("polygon ring contains a zero-length edge")

    for first_index in range(len(points)):
        first_next = (first_index + 1) % len(points)
        for second_index in range(first_index + 1, len(points)):
            second_next = (second_index + 1) % len(points)
            if (
                first_index == second_index
                or first_next == second_index
                or second_next == first_index
            ):
                continue
            if _segments_intersect(
                points[first_index], points[first_next],
                points[second_index], points[second_next],
            ):
                raise GeometryValidationError("polygon ring must not self-intersect")

    latitude_origin = sum(point[1] for point in points) / len(points)
    longitude_origin = sum(point[0] for point in points) / len(points)
    metres_latitude = 111_320.0
    metres_longitude = metres_latitude * cos(radians(latitude_origin))
    local = [
        [
            (point[0] - longitude_origin) * metres_longitude,
            (point[1] - latitude_origin) * metres_latitude,
        ]
        for point in points
    ]
    twice_area = abs(sum(
        local[index][0] * local[(index + 1) % len(local)][1]
        - local[(index + 1) % len(local)][0] * local[index][1]
        for index in range(len(local))
    ))
    if twice_area <= 0.02:
        raise GeometryValidationError("polygon ring area is too small or degenerate")
    return points + [points[0].copy()]


def validate_leaflet_ring(ring):
    """Validate a Leaflet ``[lat, lng]`` ring and return an open float ring."""
    if not isinstance(ring, (list, tuple)):
        raise GeometryValidationError("polygon must be an array of [latitude, longitude] pairs")
    geojson = []
    for position in ring:
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            raise GeometryValidationError("polygon positions must be numeric [latitude, longitude] pairs")
        geojson.append([position[1], position[0]])
    validated = validate_geojson_ring(geojson)
    return geojson_ring_to_latlng(validated[:-1])


def point_in_ring(point, ring):
    """Return whether a point is strictly inside a simple coordinate ring."""
    x, y = point
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if _orientation(previous, current, point) == 0 and _on_segment(
            previous, current, point
        ):
            return False
        if (
            (current[1] > y) != (previous[1] > y)
            and x
            < (previous[0] - current[0]) * (y - current[1])
            / ((previous[1] - current[1]) or 1e-15)
            + current[0]
        ):
            inside = not inside
    return inside


def _open_ring(ring):
    points = list(ring or [])
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _segments_properly_intersect(a, b, c, d):
    """Return True only when segment interiors cross, not when they just touch."""
    first = _orientation(a, b, c)
    second = _orientation(a, b, d)
    third = _orientation(c, d, a)
    fourth = _orientation(c, d, b)
    return first * second < 0 and third * fourth < 0


def _interior_point(ring):
    """Find a deterministic point strictly inside a validated simple ring."""
    points = _open_ring(ring)
    if not points:
        return None
    candidates = [
        (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    ]
    candidates.extend(
        (
            (points[index - 1][0] + points[index][0] + points[(index + 1) % len(points)][0]) / 3,
            (points[index - 1][1] + points[index][1] + points[(index + 1) % len(points)][1]) / 3,
        )
        for index in range(len(points))
    )
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    candidates.extend(
        (min_x + (max_x - min_x) * x / 10, min_y + (max_y - min_y) * y / 10)
        for x in range(1, 10)
        for y in range(1, 10)
    )
    return next((candidate for candidate in candidates if point_in_ring(candidate, points)), None)


def _collinear_edges_share_interior(first, second):
    """Detect aligned edge overlap whose adjacent polygon interiors coincide."""
    for first_index, first_start in enumerate(first):
        first_end = first[(first_index + 1) % len(first)]
        for second_index, second_start in enumerate(second):
            second_end = second[(second_index + 1) % len(second)]
            if not (
                _orientation(first_start, first_end, second_start) == 0
                and _orientation(first_start, first_end, second_end) == 0
            ):
                continue
            shared = []
            for point in (first_start, first_end, second_start, second_end):
                if (
                    _on_segment(first_start, first_end, point)
                    and _on_segment(second_start, second_end, point)
                    and point not in shared
                ):
                    shared.append(point)
            if len(shared) < 2:
                continue
            start, end = max(
                (
                    (a, b)
                    for index, a in enumerate(shared)
                    for b in shared[index + 1:]
                ),
                key=lambda pair: hypot(
                    pair[1][0] - pair[0][0], pair[1][1] - pair[0][1]
                ),
            )
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = hypot(dx, dy)
            if length <= 1e-12:
                continue
            midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            epsilon = max(length * 1e-4, 1e-10)
            for direction in (-1, 1):
                candidate = (
                    midpoint[0] + direction * (-dy / length) * epsilon,
                    midpoint[1] + direction * (dx / length) * epsilon,
                )
                if point_in_ring(candidate, first) and point_in_ring(candidate, second):
                    return True
    return False


def rings_have_positive_area_overlap(first, second):
    """Return whether two simple rings share interior area.

    A shared vertex or collinear boundary segment has zero area and is therefore
    not overlap for the obstruction-area model.
    """
    first = _open_ring(first)
    second = _open_ring(second)
    if any(
        _segments_properly_intersect(
            first[first_index], first[(first_index + 1) % len(first)],
            second[second_index], second[(second_index + 1) % len(second)],
        )
        for first_index in range(len(first))
        for second_index in range(len(second))
    ):
        return True
    if any(point_in_ring(point, second) for point in first):
        return True
    if any(point_in_ring(point, first) for point in second):
        return True
    if _collinear_edges_share_interior(first, second):
        return True
    first_interior = _interior_point(first)
    second_interior = _interior_point(second)
    return bool(
        (first_interior and point_in_ring(first_interior, second))
        or (second_interior and point_in_ring(second_interior, first))
    )


def ring_strictly_contains(outer, inner):
    """Return whether an inner ring is wholly inside and clear of an outer ring."""
    return all(point_in_ring(point, outer) for point in inner) and not any(
        _segments_intersect(
            outer[outer_index], outer[(outer_index + 1) % len(outer)],
            inner[inner_index], inner[(inner_index + 1) % len(inner)],
        )
        for outer_index in range(len(outer))
        for inner_index in range(len(inner))
    )
