"""Field data from the public LPIS WFS (eAGRI / MZe).

Coordinates in the service are S-JTSK Krovak East North (EPSG:5514).
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import httpx
from pyproj import Transformer
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon, mapping
from shapely.ops import transform, unary_union

WFS = "https://mze.gov.cz/public/app/wms/plpis_wfs.fcgi"
TO_WGS = Transformer.from_crs("EPSG:5514", "EPSG:4326", always_xy=True)
TO_JTSK = Transformer.from_crs("EPSG:4326", "EPSG:5514", always_xy=True)

ZABAGED = "https://ags.cuzk.gov.cz/arcgis/rest/services/ZABAGED_POLOHOPIS/MapServer"
WATER_SEARCH_M = 60   # registry buffer distances go up to 50 m
BEE_RADIUS_M = 5000  # radius used to list registered apiaries near the field


class FieldNotFound(Exception):
    pass


def _get_features(typename: str, *, bbox=None, filter_xml: str | None = None, max_features: int = 500) -> list[dict]:
    params = {
        "SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
        "TYPENAME": f"ms:{typename}",
        "MAXFEATURES": str(max_features),
    }
    if bbox is not None:
        params["BBOX"] = ",".join(f"{v:.2f}" for v in bbox)
    if filter_xml is not None:
        params["FILTER"] = filter_xml
    last_err = None
    for attempt in range(4):  # the public WFS occasionally rejects or stalls a request
        try:
            r = httpx.get(WFS, params=params, timeout=60)
            r.raise_for_status()
            if r.content.lstrip().startswith(b"<html"):
                raise RuntimeError("WFS request rejected by the server")
            return _parse_gml(r.content)
        except (httpx.HTTPError, RuntimeError, ET.ParseError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LPIS WFS unavailable: {last_err}")


GML = "{http://www.opengis.net/gml}"
MS = "{http://mapserver.gis.umn.edu/mapserver}"


def _coords(text: str):
    v = [float(x) for x in text.split()]
    return list(zip(v[0::2], v[1::2]))


def _parse_gml(content: bytes) -> list[dict]:
    """Parse a MapServer GML 3.1.1 FeatureCollection into [{properties, geometry(shapely)}]."""
    root = ET.fromstring(content)
    if root.tag.endswith("ExceptionReport"):
        raise RuntimeError(content[:500].decode("utf-8", "replace"))
    feats = []
    for member in root.iter(f"{GML}featureMember"):
        for f in member:
            props = {}
            geom = None
            for ch in f:
                name = ch.tag.replace(MS, "")
                if name in ("GEOMETRY", "msGeometry"):
                    polys = []
                    for pg in ch.iter(f"{GML}Polygon"):
                        ext = pg.find(f"{GML}exterior//{GML}posList")
                        holes = [_coords(h.text) for h in pg.findall(f"{GML}interior//{GML}posList")]
                        if ext is not None:
                            polys.append(Polygon(_coords(ext.text), holes))
                    if polys:
                        geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
                    else:
                        pts = [Point(_coords(p.text)[0]) for p in ch.iter(f"{GML}pos")]
                        if pts:
                            geom = pts[0] if len(pts) == 1 else MultiPoint(pts)
                elif len(ch) == 0:
                    props[name] = ch.text
            if geom is not None:
                feats.append({"properties": props, "geometry": geom})
    return feats


def _num(v):
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _field_from_feature(f: dict) -> dict:
    p = f["properties"]
    return {
        "id": p.get("IdDiluPudnihoBloku"),
        "code": (p.get("kodCtverec") or "").strip(),
        "square": (p.get("ctverec") or "").strip(),
        "short_code": (p.get("zkracenyKod") or "").strip(),
        "area_ha": _num(p.get("vymera")),
        "land_use": (p.get("kultura") or "").strip(),
        "erosion": (p.get("EROZE3G") or "").strip(),
        "farming_regime": (p.get("rezimEZ_PO") or "").strip(),
        "user": (p.get("uzivatel") or "").strip(),
        "ji": (p.get("JIuzivatele") or "").strip(),
        "region": (p.get("uzemniPrislusnost") or "").strip(),
        "geometry": f["geometry"].buffer(0),
    }


def field_by_code(square: str, short_code: str) -> dict:
    """Find a DPB by its square (e.g. 550-1150) and short code (e.g. 9701/26)."""
    code = f"{short_code.strip()} {square.strip()}"
    flt = ("<Filter><PropertyIsEqualTo><PropertyName>kodCtverec</PropertyName>"
           f"<Literal>{_xml_escape(code)}</Literal></PropertyIsEqualTo></Filter>")
    feats = _get_features("LPIS_DPB_UCINNE", filter_xml=flt, max_features=5)
    if not feats:
        raise FieldNotFound(code)
    return _field_from_feature(feats[0])


def fields_by_ji(ji: str) -> list[dict]:
    """All effective DPBs farmed by one user, found by the user's public LPIS identifier (ID JI)."""
    flt = ("<Filter><PropertyIsEqualTo><PropertyName>JIuzivatele</PropertyName>"
           f"<Literal>{_xml_escape(ji)}</Literal></PropertyIsEqualTo></Filter>")
    return [_field_from_feature(f) for f in _get_features("LPIS_DPB_UCINNE", filter_xml=flt, max_features=5000)]


def field_outline(field: dict) -> dict:
    """Light-weight WGS84 outline of a field for drawing on the map (simplified to about 1 m)."""
    return _to_geojson(field["geometry"].simplify(1.0, preserve_topology=True))


def field_at(lat: float, lon: float) -> dict:
    x, y = TO_JTSK.transform(lon, lat)
    feats = _get_features("LPIS_DPB_UCINNE", bbox=(x - 5, y - 5, x + 5, y + 5), max_features=20)
    pt = Point(x, y)
    for f in feats:
        fld = _field_from_feature(f)
        if fld["geometry"].contains(pt):
            return fld
    raise FieldNotFound(f"{lat},{lon}")


def field_context(field: dict) -> dict:
    """Spatial checks for a field: slope-near-water zone, OPVZ I buffer, erosion detail, apiaries."""
    g = field["geometry"]
    minx, miny, maxx, maxy = g.bounds
    bb = (minx, miny, maxx, maxy)

    slope = [f["geometry"] for f in _get_features("SVAZITE_100_WFS", bbox=bb, max_features=5000)]
    slope_zone = unary_union([s.buffer(0) for s in slope if s.intersects(g)]).intersection(g) if slope else None
    slope_area = slope_zone.area if slope_zone is not None else 0.0

    opvz = [f["geometry"] for f in _get_features("OPVZ_1STUPEN_ZONA100_WFS", bbox=bb)]
    opvz_area = unary_union([o.buffer(0) for o in opvz]).intersection(g).area if opvz else 0.0

    eroze = None
    for f in _get_features("DPB_EROZE_WFS", bbox=bb, max_features=50):
        p = f["properties"]
        if (p.get("ZKOD") or "").strip() == field["short_code"] and (p.get("CTVEREC") or "").strip() == field["square"]:
            eroze = {
                "category": (p.get("EROZOHRO") or "").strip(),
                "seo_ha": _num(p.get("SEO_SUM")),
                "meo_ha": _num(p.get("SEOMEONR_SUM")),
                "neo_ha": _num(p.get("NEO_SUM")),
                "slope": (p.get("SKLONITOST") or "").strip(),
            }
            break

    r = BEE_RADIUS_M
    bees = []
    for typ in ("VCELY_BOD_WFS", "VCELY_POLYGON_WFS"):
        for f in _get_features(typ, bbox=(minx - r, miny - r, maxx + r, maxy + r), max_features=1000):
            d = f["geometry"].distance(g)
            if d <= r:
                p = f["properties"]
                bees.append({"id": p.get("ID_PROVOZOVNY"), "distance_m": round(d)})
    bees_unique = {}
    for b in bees:
        if b["id"] not in bees_unique or b["distance_m"] < bees_unique[b["id"]]["distance_m"]:
            bees_unique[b["id"]] = b
    bees = sorted(bees_unique.values(), key=lambda b: b["distance_m"])

    return {
        "slope_near_water": {
            "intersects": slope_area > 1.0,
            "area_ha": round(slope_area / 10000, 2),
            "share": round(slope_area / g.area, 3) if g.area else 0,
            "geometry": _to_geojson(slope_zone) if slope_area > 1.0 else None,
        },
        "opvz1_buffer": {"intersects": opvz_area > 1.0, "area_ha": round(opvz_area / 10000, 2)},
        "erosion": eroze,
        "apiaries": {"radius_m": r, "count": len(bees), "nearest_m": bees[0]["distance_m"] if bees else None},
        "water": water_context(g),
        "_field_geom": g,
    }


def _zabaged_query(layer: int, bbox) -> list[dict]:
    """Query a ZABAGED (ČÚZK) ArcGIS REST layer within a bbox in EPSG:5514."""
    params = {
        "geometry": ",".join(f"{v:.2f}" for v in bbox), "geometryType": "esriGeometryEnvelope",
        "inSR": "5514", "outSR": "5514", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*", "returnGeometry": "true", "f": "json",
    }
    last_err = None
    for attempt in range(3):
        try:
            r = httpx.get(f"{ZABAGED}/{layer}/query", params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data.get("features", [])
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ZABAGED unavailable: {last_err}")


def water_context(g) -> dict:
    """Surface water (ZABAGED watercourses + water areas) within WATER_SEARCH_M of the field.

    Underground (piped) watercourses are skipped; intermittent ones are kept.
    """
    minx, miny, maxx, maxy = g.bounds
    d = WATER_SEARCH_M
    bb = (minx - d, miny - d, maxx + d, maxy + d)
    items = []
    for f in _zabaged_query(93, bb):
        a = f["attributes"]
        if (a.get("typtoku_p") or "").startswith("podzem"):
            continue
        for path in f["geometry"].get("paths", []):
            items.append((LineString(path), a.get("jmeno") or "bezejmenný tok", a.get("vydattok_p") or ""))
    for f in _zabaged_query(132, bb):
        a = f["attributes"]
        rings = f["geometry"].get("rings", [])
        if rings:
            poly = Polygon(rings[0], rings[1:]).buffer(0)
            items.append((poly, a.get("jmeno") or "vodní plocha", "vodní plocha"))
    near = [(geom, name, kind, geom.distance(g)) for geom, name, kind in items]
    near = [x for x in near if x[3] <= d]
    if not near:
        return {"found": False, "min_distance_m": None, "nearest": None, "geometry": None, "_union": None}
    near.sort(key=lambda x: x[3])
    union = unary_union([x[0] for x in near])
    return {
        "found": True,
        "min_distance_m": round(near[0][3], 1),
        "nearest": {"name": near[0][1], "kind": near[0][2]},
        "names": sorted({x[1] for x in near}),
        "geometry": _to_geojson(union),
        "_union": union,
    }


def area_within(field_geom, water_union, distance_m: float) -> float:
    """Area (ha) of the field closer than distance_m to surface water."""
    if water_union is None or distance_m <= 0:
        return 0.0
    return field_geom.intersection(water_union.buffer(distance_m)).area / 10000


def public_context(ctx: dict) -> dict:
    """Drop internal (shapely) keys before sending the context to the browser."""
    out = {}
    for k, v in ctx.items():
        if k.startswith("_"):
            continue
        out[k] = public_context(v) if isinstance(v, dict) else v
    return out


def _to_geojson(geom):
    return mapping(transform(TO_WGS.transform, geom))


def field_public(field: dict) -> dict:
    out = {k: v for k, v in field.items() if k != "geometry"}
    out["geometry"] = _to_geojson(field["geometry"])
    return out


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
