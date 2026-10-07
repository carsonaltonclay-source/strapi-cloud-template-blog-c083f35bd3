"""Small pure-Python geometry helpers (WGS84 lon/lat, distances in meters)."""

import math

EARTH_RADIUS_M = 6371008.8
METERS_PER_MILE = 1609.344
METERS_PER_FOOT = 0.3048


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def miles_between(lat1, lon1, lat2, lon2):
    return haversine_m(lat1, lon1, lat2, lon2) / METERS_PER_MILE


def circle_polygon(lat, lon, radius_m, segments=36):
    """Closed ring of (lon, lat) approximating a circle."""
    ring = []
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        dlat = radius_m * math.cos(a) / 111320.0
        dlon = radius_m * math.sin(a) / (111320.0 * math.cos(math.radians(lat)))
        ring.append((lon + dlon, lat + dlat))
    return ring


def bbox_of_circle(lat, lon, radius_m):
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def _project(lat0, lon, lat):
    """Local equirectangular projection to meters around latitude lat0."""
    x = math.radians(lon) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = math.radians(lat) * EARTH_RADIUS_M
    return x, y


def point_in_ring(lon, lat, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygon(lon, lat, rings):
    """Esri polygon rings (even-odd rule handles holes)."""
    inside = False
    for ring in rings:
        if point_in_ring(lon, lat, ring):
            inside = not inside
    return inside


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_to_paths_m(lon, lat, paths):
    """Shortest distance (m) from a point to a set of polyline paths."""
    px, py = _project(lat, lon, lat)
    best = float("inf")
    for path in paths:
        pts = [_project(lat, p[0], p[1]) for p in path]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            best = min(best, _seg_dist(px, py, ax, ay, bx, by))
        if len(pts) == 1:
            best = min(best, math.hypot(px - pts[0][0], py - pts[0][1]))
    return best


def _segments_intersect(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def polygon_to_paths_m(rings, paths):
    """Shortest distance (m) between a polygon and polylines; 0 if they touch/cross
    or a line lies inside the polygon."""
    if not rings or not paths:
        return float("inf")
    lat0 = rings[0][0][1]
    prings = [[_project(lat0, p[0], p[1]) for p in r] for r in rings]
    ppaths = [[_project(lat0, p[0], p[1]) for p in path] for path in paths]

    for path, ppath in zip(paths, ppaths):
        if path and point_in_polygon(path[0][0], path[0][1], rings):
            return 0.0
        for ring in prings:
            for a, b in zip(ring, ring[1:]):
                for c, d in zip(ppath, ppath[1:]):
                    if _segments_intersect(a, b, c, d):
                        return 0.0

    best = float("inf")
    for ring in prings:
        for a, b in zip(ring, ring[1:]):
            for ppath in ppaths:
                for c, d in zip(ppath, ppath[1:]):
                    best = min(
                        best,
                        _seg_dist(a[0], a[1], c[0], c[1], d[0], d[1]),
                        _seg_dist(b[0], b[1], c[0], c[1], d[0], d[1]),
                        _seg_dist(c[0], c[1], a[0], a[1], b[0], b[1]),
                        _seg_dist(d[0], d[1], a[0], a[1], b[0], b[1]),
                    )
    return best


def polygon_area_acres(rings):
    """Planar area of Esri rings (outer clockwise positive, holes subtract)."""
    if not rings:
        return None
    lat0 = rings[0][0][1]
    total = 0.0
    for ring in rings:
        pts = [_project(lat0, p[0], p[1]) for p in ring]
        s = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            s += x1 * y2 - x2 * y1
        total += -s / 2.0  # Esri outer rings are clockwise
    return abs(total) / 4046.8564224


def polygon_centroid(rings):
    ring = max(rings, key=len)
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(ys) / len(ys), sum(xs) / len(xs)
