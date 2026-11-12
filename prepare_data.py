"""
AGRI Watch – prepare GEE exports for the Streamlit dashboard (run once).

Reads   data/raw/   (the files from the Drive folder AGRI_Watch_Kaduna_web)
Writes  data/web/   (coloured PNG map layers + layers.json + clean tables)

Usage:
    python prepare_data.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from rasterio.warp import transform_bounds

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
WEB = ROOT / "data" / "web"

# Same colours as the dashboard design
CLASS_COLOURS = {          # drought class code -> hex
    1: "#8FCB8A",          # Normal
    2: "#FFD92F",          # Mild
    3: "#F4A43A",          # Moderate
    4: "#D7301F",          # Severe
    5: "#7F0000",          # Extreme
}
RAIN_RAMP = ["#7f3b08", "#b35806", "#e08214", "#f4e3b5", "#74add1", "#4575b4", "#313695"]  # brown -> blue
VEG_RAMP = ["#7f3b08", "#b35806", "#e08214", "#f4e3b5", "#5aae61", "#1b7837", "#00441b"]   # brown -> green

LAYERS = {
    # name in app : (tif file, kind, ramp)
    "Drought":  ("drought_class.tif", "class", None),
    "Rainfall": ("rain_anomaly.tif", "anomaly", RAIN_RAMP),
    "NDVI":     ("ndvi_anomaly.tif", "anomaly", VEG_RAMP),
    "NDMI":     ("ndmi_anomaly.tif", "anomaly", VEG_RAMP),
}

TABLES = ["lga_stats.csv", "state_trend.csv", "lga_boundaries.geojson"]

STATS_COLUMNS = ["lga", "period", "mean_cdi", "crop_km2", "normal_km2", "mild_km2",
                 "moderate_km2", "severe_km2", "extreme_km2", "pct_drought", "pct_severe"]


def hex_to_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def colour_classes(arr, valid):
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for code, colour in CLASS_COLOURS.items():
        m = valid & (arr == code)
        rgba[m, :3] = hex_to_rgb(colour).astype(np.uint8)
        rgba[m, 3] = 255
    return rgba


def colour_anomaly(arr, valid, ramp, vmin=-2.5, vmax=2.5):
    stops = np.stack([hex_to_rgb(c) for c in ramp])            # (n, 3)
    pos = np.linspace(0, 1, len(ramp))
    t = np.clip(np.nan_to_num((arr - vmin) / (vmax - vmin), nan=0.5), 0, 1)  # no-data pixels stay transparent
    rgb = np.stack([np.interp(t, pos, stops[:, i]) for i in range(3)], axis=-1)
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    rgba[..., :3] = rgb.astype(np.uint8)
    rgba[..., 3] = np.where(valid, 255, 0)
    return rgba


def make_layer(name, tif, kind, ramp):
    path = RAW / tif
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        nodata = src.nodata
        valid = np.isfinite(arr)
        if nodata is not None:
            valid &= arr != nodata
        valid &= arr != -9999
        if kind == "class":
            valid &= arr > 0
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)

    rgba = colour_classes(arr, valid) if kind == "class" else colour_anomaly(arr, valid, ramp)
    out_png = WEB / f"{tif.replace('.tif', '')}.png"
    Image.fromarray(rgba, mode="RGBA").save(out_png, optimize=True)
    print(f"  {name:<9} -> {out_png.name}  ({arr.shape[1]}x{arr.shape[0]} px, {valid.mean():.0%} valid)")
    return {"png": out_png.name, "bounds": [[south, west], [north, east]], "kind": kind}


def normalize_geometry(g):
    """Turn GEE GeometryCollections into (Multi)Polygons; drop stray lines/points."""
    if not g:
        return None
    t = g.get("type")
    if t in ("Polygon", "MultiPolygon"):
        return g
    if t == "GeometryCollection":
        polys = []
        for sub in g.get("geometries", []):
            sub = normalize_geometry(sub)
            if sub is None:
                continue
            if sub["type"] == "Polygon":
                polys.append(sub["coordinates"])
            else:
                polys.extend(sub["coordinates"])
        if not polys:
            return None
        return {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else \
               {"type": "MultiPolygon", "coordinates": polys}
    return None


def normalize_geojson(gj):
    feats = []
    for f in gj.get("features", []):
        geom = normalize_geometry(f.get("geometry"))
        if geom is not None:
            feats.append({"type": "Feature", "properties": f.get("properties", {}), "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}

def main():
    WEB.mkdir(parents=True, exist_ok=True)

    missing = [f for f in [v[0] for v in LAYERS.values()] + TABLES if not (RAW / f).exists()]
    if missing:
        raise SystemExit("Missing in data/raw/: " + ", ".join(missing))

    print("Map layers:")
    layers = {name: make_layer(name, *spec) for name, spec in LAYERS.items()}
    (WEB / "layers.json").write_text(json.dumps(layers, indent=2))

    print("Tables:")
    stats = pd.read_csv(RAW / "lga_stats.csv")
    absent = [c for c in STATS_COLUMNS if c not in stats.columns]
    if absent:
        raise SystemExit(f"lga_stats.csv is missing columns: {absent} (re-run GEE script v2)")
    stats["lga"] = stats["lga"].astype(str).str.strip()
    stats[STATS_COLUMNS[2:]] = stats[STATS_COLUMNS[2:]].apply(pd.to_numeric, errors="coerce").fillna(0)
    stats[STATS_COLUMNS].to_csv(WEB / "lga_stats.csv", index=False)
    print(f"  lga_stats.csv    {len(stats)} LGAs")

    trend = pd.read_csv(RAW / "state_trend.csv").dropna().sort_values("year")
    trend["year"] = trend["year"].astype(int)
    trend.to_csv(WEB / "state_trend.csv", index=False)
    print(f"  state_trend.csv  {trend['year'].min()}–{trend['year'].max()}")

    gj = normalize_geojson(json.loads((RAW / "lga_boundaries.geojson").read_text()))
    (WEB / "lga_boundaries.geojson").write_text(json.dumps(gj))
    names_geo = {f["properties"]["lga"].strip() for f in gj["features"]}
    unmatched = sorted(names_geo ^ set(stats["lga"]))
    print(f"  lga_boundaries   {len(gj['features'])} features")
    if unmatched:
        print("  WARNING – names not matching between boundaries and stats:", unmatched)

    print("Done. Now run:  streamlit run app.py")


if __name__ == "__main__":
    main()
