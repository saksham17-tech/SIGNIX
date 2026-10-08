"""Coordinate-order helpers for SIGNIX rooftop geometry.

Leaflet and the existing solar calculation engine use ``[latitude, longitude]``
pairs. Persisted GeoJSON uses the GeoJSON-required ``[longitude, latitude]``
order. Keep conversions at those boundaries instead of relying on coordinate
range heuristics (both values are valid latitude values in much of India).
"""

from __future__ import annotations

from math import hypot


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
