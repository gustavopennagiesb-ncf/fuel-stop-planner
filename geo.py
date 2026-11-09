"""Geometry helpers: Google encoded polylines, distances and the corridor filter.

Tested code adapted from the research notes. Equirectangular projection is accurate to a
few meters at corridor scale, so no shapely dependency is needed.
"""
import math

M_PER_MILE = 1609.344


def decode_polyline(s: str, precision: int = 5) -> list:
    """Decode a Google encoded polyline into [(lat, lng), ...]."""
    coords, idx, lat, lng, f = [], 0, 0, 0, 10 ** precision
    while idx < len(s):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        coords.append((lat / f, lng / f))
    return coords


def encode_polyline(points: list, precision: int = 5) -> str:
    """Inverse of decode_polyline (used to build synthetic fixtures)."""
    f, out, plat, plng = 10 ** precision, [], 0, 0
    for lat, lng in points:
        ilat, ilng = round(lat * f), round(lng * f)
        for d in (ilat - plat, ilng - plng):
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plng = ilat, ilng
    return "".join(out)


def haversine_m(a: tuple, b: tuple) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def path_length_m(path: list) -> float:
    return sum(haversine_m(path[i], path[i + 1]) for i in range(len(path) - 1))


def dist_point_segment_m(p: tuple, a: tuple, b: tuple) -> float:
    """Point-to-segment distance using a local equirectangular projection."""
    k = math.cos(math.radians(p[0]))

    def xy(q):
        return ((q[1] - p[1]) * k * 111320.0, (q[0] - p[0]) * 110540.0)

    ax, ay = xy(a)
    bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def corridor_filter(stations: list, path: list, buffer_m: float = 1500) -> list:
    """stations: [{'id','lat','lng',...}]. Keep those within buffer_m of the path."""
    out = []
    for s in stations:
        p = (s["lat"], s["lng"])
        d = min(dist_point_segment_m(p, path[i], path[i + 1]) for i in range(len(path) - 1))
        if d <= buffer_m:
            out.append({**s, "off_route_m": round(d)})
    return sorted(out, key=lambda r: r["off_route_m"])


def sample_points(path: list, every_m: float) -> list:
    """Points spaced roughly every_m along the path (used to query Places near the route)."""
    pts, acc = [path[0]], 0.0
    for i in range(len(path) - 1):
        acc += haversine_m(path[i], path[i + 1])
        if acc >= every_m:
            pts.append(path[i + 1])
            acc = 0.0
    return pts
