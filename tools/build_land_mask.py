"""Clip Natural Earth 1:50m land to the region around Finland, for land/sea path segments.

Usage: python tools/build_land_mask.py <ne_50m_land.geojson> data/masks/land_near_finland.json

The source is read as plain JSON from the path given. Lakes are not cut out, because the land
layer has none. Clipping can leave edges along the box; they do not affect a point inside the box.
"""
import hashlib
import json
import sys
from pathlib import Path

SOURCE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_land.geojson"
SOURCE_SHA256 = "e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b"
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 52.0, 75.0, 3.0, 48.0


def _clip(ring: list[list[float]], axis: int, limit: float, keep_above: bool) -> list[list[float]]:
    """One Sutherland-Hodgman pass against an axis-aligned line."""

    def inside(p):
        return p[axis] >= limit if keep_above else p[axis] <= limit

    out = []
    for i, current in enumerate(ring):
        previous = ring[i - 1]
        if inside(current) != inside(previous):
            t = (limit - previous[axis]) / (current[axis] - previous[axis])
            out.append([previous[0] + t * (current[0] - previous[0]), previous[1] + t * (current[1] - previous[1])])
        if inside(current):
            out.append(current)
    return out


def clip_to_box(ring: list[list[float]]) -> list[list[float]]:
    for axis, limit, keep_above in ((0, LON_MIN, True), (0, LON_MAX, False), (1, LAT_MIN, True), (1, LAT_MAX, False)):
        if not ring:
            return []
        ring = _clip(ring, axis, limit, keep_above)
    return ring


def main(source: Path, target: Path) -> None:
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(f"Unexpected source hash {digest}")
    rings = []
    for feature in json.loads(raw)["features"]:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            clipped = clip_to_box(polygon[0][:-1])  # the last vertex repeats the first
            if len(clipped) >= 3:
                rings.append([[round(lon, 5), round(lat, 5)] for lon, lat in clipped])
    target.write_text(json.dumps({
        "description": "Land around Finland, for land/sea path segments. Screening geography, not a legal or survey coastline.",
        "source": "Natural Earth 1:50m Land v5.1.2, outer rings clipped to the box below",
        "url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "license": "Public domain",
        "built_by": "tools/build_land_mask.py",
        "coordinate_order": "lon, lat (WGS84)",
        "box": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lon_min": LON_MIN, "lon_max": LON_MAX},
        "limitations": ["Lakes count as land, including Ladoga and Onega.", "Resolution is 1:50m, so narrow straits and small islands are missing or generalised."],
        "rings": rings,
    }, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
