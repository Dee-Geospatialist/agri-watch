"""
AGRI Watch – Kaduna Agricultural Drought Monitor (Streamlit dashboard)

Run:  streamlit run app.py
Needs the prepared files in data/web/  (run prepare_data.py first)
"""

import json
from pathlib import Path
from string import Template

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

WEB = Path(__file__).parent / "data" / "web"

st.set_page_config(page_title="AGRI Watch – Kaduna Drought", page_icon="🌾", layout="wide")

# ----------------------------------------------------------------------------
# Constants: classes, colours, themes
# ----------------------------------------------------------------------------
CLASSES = ["Normal", "Mild", "Moderate", "Severe", "Extreme"]
CLASS_COLOURS = {"Normal": "#8FCB8A", "Mild": "#FFD92F", "Moderate": "#F4A43A",
                 "Severe": "#D7301F", "Extreme": "#7F0000"}
ANOMALY_LEGENDS = {   # must match the ramps in prepare_data.py
    "Rainfall": [("Much drier", "#7f3b08"), ("Drier", "#e08214"), ("Normal", "#f4e3b5"),
                 ("Wetter", "#74add1"), ("Much wetter", "#313695")],
    "NDVI": [("Much less green", "#7f3b08"), ("Less green", "#e08214"), ("Normal", "#f4e3b5"),
             ("Greener", "#5aae61"), ("Much greener", "#00441b")],
    "NDMI": [("Much drier leaves", "#7f3b08"), ("Drier leaves", "#e08214"), ("Normal", "#f4e3b5"),
             ("Wetter leaves", "#5aae61"), ("Much wetter leaves", "#00441b")],
}
LAYER_NAMES = ["Drought", "Rainfall", "NDVI", "NDMI"]
ALL = "All Kaduna"
DROUGHT_LGA_THRESHOLD = 25  # % of cropland moderate+ for an LGA to count as "in drought"

THEMES = {
    "light": dict(
        bg="#F5F4EF", surface="#FFFFFF", border="#E3E1D8", text="#1F2328", muted="#5B6370",
        header="#1E3A2F", header_muted="#B9D3C4", header_sub="#D6E4DB",
        input_bg="#FFFFFF", input_text="#1F2328", input_border="#FFFFFF",
        track="#F0EEE7", axis="#9A988F", pos_bar="#9DB8C9", normal_bar="#C9D7C3",
        row_hi="rgba(244,164,58,0.18)", callout="#8A2A12", lga_line="#2B3138", overlay_opacity=1.0,
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    ),
    "dark": dict(
        bg="#12161A", surface="#1B2127", border="#2C343C", text="#E8EAED", muted="#A3ACB6",
        header="#0F2A20", header_muted="#9CC3AE", header_sub="#C9DDD1",
        input_bg="#1B2127", input_text="#E8EAED", input_border="#3A4550",
        track="#2A3138", axis="#5D6670", pos_bar="#6F93AB", normal_bar="#4F6B55",
        row_hi="rgba(244,164,58,0.16)", callout="#F4A43A", lga_line="#F0F2F4", overlay_opacity=0.92,
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    ),
}


def cdi_class(v):
    if pd.isna(v):
        return "Normal"
    if v <= -2.0:
        return "Extreme"
    if v <= -1.5:
        return "Severe"
    if v <= -1.0:
        return "Moderate"
    if v <= -0.5:
        return "Mild"
    return "Normal"


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt_km(v):
    return f"{v:,.0f} km²"


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
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


@st.cache_data
def load_data():
    stats = pd.read_csv(WEB / "lga_stats.csv")
    trend = pd.read_csv(WEB / "state_trend.csv")
    layers = json.loads((WEB / "layers.json").read_text())
    geo = normalize_geojson(json.loads((WEB / "lga_boundaries.geojson").read_text()))

    dry_cols = ["moderate_km2", "severe_km2", "extreme_km2"]
    class_cols = ["normal_km2", "mild_km2"] + dry_cols
    stats["classed_km2"] = stats[class_cols].sum(axis=1)
    stats["dry_km2"] = stats[dry_cols].sum(axis=1)
    stats["avg_class"] = stats["mean_cdi"].apply(cdi_class)
    stats = stats.sort_values("pct_drought", ascending=False).reset_index(drop=True)
    stats["rank"] = stats.index + 1
    return stats, trend, layers, geo


def geo_bounds(feature):
    """[[south, west], [north, east]] of a GeoJSON feature."""
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for part in c:
                walk(part)

    walk(feature["geometry"]["coordinates"])
    return [[min(ys), min(xs)], [max(ys), max(xs)]]


if not (WEB / "layers.json").exists():
    st.error("No prepared data found. Put the GEE exports in data/raw/ and run `python prepare_data.py`.")
    st.stop()

stats, trend, layers, geo = load_data()
lga_names = sorted(stats["lga"].tolist())
n_lgas = len(stats)
period = str(stats["period"].iloc[0])                  # e.g. 2025_May-Oct
target_year = int(period[:4])
season = period[5:].replace("-", "–")
period_label = f"{season} {target_year}"

# ----------------------------------------------------------------------------
# Session state: theme, selected area, map layer
# ----------------------------------------------------------------------------
st.session_state.setdefault("theme", "light")
st.session_state.setdefault("area", ALL)
st.session_state.setdefault("layer", "Drought")


def toggle_theme():
    st.session_state.theme = "dark" if st.session_state.theme == "light" else "light"


def reset_area():
    st.session_state.area = ALL


T = THEMES[st.session_state.theme]
dark = st.session_state.theme == "dark"

# ----------------------------------------------------------------------------
# Styling (theme applied with CSS so the header button can switch it)
# ----------------------------------------------------------------------------
CSS = Template("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, .stApp, .stApp * { font-family: Inter, system-ui, sans-serif; }
[data-testid="stIconMaterial"] { font-family: 'Material Symbols Rounded' !important; }
.stApp { background: $bg; color: $text; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.2rem; max-width: 1240px; }
.stApp p, .stApp span, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3 { color: $text; }
.muted { color: $muted !important; }

/* Header */
.st-key-hdr { background: $header; border-radius: 14px; padding: 18px 24px; }
.st-key-hdr p, .st-key-hdr span, .st-key-hdr h1 { color: #FFFFFF; }
.st-key-hdr label p { color: $header_muted !important; font-size: 13px; }
.hdr-eyebrow { font-size: 12px; font-weight: 600; letter-spacing: .12em; color: $header_muted !important; }
.hdr-title { font-size: 26px; font-weight: 700; margin: 2px 0; color: #FFFFFF !important; line-height: 1.25; }
.hdr-title span { font-weight: 400; color: $header_muted !important; }
.hdr-sub { font-size: 14px; color: $header_sub !important; }
.st-key-hdr button { background: transparent; border: 1px solid rgba(255,255,255,.4); color: #FFFFFF; min-height: 42px; }
.st-key-hdr button p { color: #FFFFFF !important; }
.st-key-hdr button:hover { border-color: #FFFFFF; color: #FFFFFF; }

/* Inputs (selectbox) and its dropdown menu */
[data-testid="stSelectbox"] div[role="group"], div[data-baseweb="select"] > div {
  background: $input_bg !important; border: 1px solid $input_border !important; border-radius: 8px !important; min-height: 42px; }
[data-testid="stSelectbox"] input { color: $input_text !important; background: transparent !important; }
[data-testid="stSelectbox"] svg { fill: $input_text !important; color: $input_text !important; }
[role="listbox"] { background: $surface !important; border: 1px solid $border !important; }
[role="listbox"] [role="option"], [role="listbox"] [role="option"] * { color: $text !important; background: transparent; }
[role="listbox"] [role="option"][data-focused="true"], [role="listbox"] [role="option"]:hover { background: $track !important; }

/* Cards */
.st-key-card_map, .st-key-card_rank, .st-key-card_trend, .st-key-card_donut {
  background: $surface; border: 1px solid $border; border-radius: 12px; padding: 18px 20px;
}
.card-title { font-size: 17px !important; font-weight: 600; margin: 0 !important; color: $text !important; }
.card-sub { font-size: 13px !important; line-height: 1.4 !important; color: $muted !important; margin: 2px 0 0 !important; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin: 6px 0 4px; }
.kpi { background: $surface; border: 1px solid $border; border-radius: 12px; padding: 18px 22px; }
.kpi-value { font-size: 38px; font-weight: 700; line-height: 1.1; color: $text !important; display: flex; align-items: center; gap: 10px; }
.kpi-dot { width: 18px; height: 18px; border-radius: 4px; display: inline-block; }
.kpi-label { font-size: 15px; font-weight: 500; margin-top: 4px; color: $text !important; }
.kpi-note { font-size: 13px; color: $muted !important; margin-top: 2px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; font-size: 13px; }
.legend i { width: 14px; height: 14px; border-radius: 3px; display: inline-block; margin-right: 6px; vertical-align: -2px; border: 1px solid rgba(0,0,0,.12); }
.legend .note { margin-left: auto; color: $muted !important; }
.callout { font-size: 13px !important; font-weight: 600; color: $callout !important; margin: 0 !important; }

/* Segmented control (map layers) */
button[data-variant="segmented_control"] { background: transparent !important; border-color: $border !important; }
button[data-variant="segmented_control"] p { color: $text !important; font-size: 13px !important; }
button[data-variant="segmented_control"][data-selected="true"] { background: $header !important; border-color: $header !important; }
button[data-variant="segmented_control"][data-selected="true"] p { color: #FFFFFF !important; font-weight: 600; }

/* Expanders, table, download button */
[data-testid="stExpander"] details { background: $surface !important; border: 1px solid $border !important; border-radius: 12px; }
[data-testid="stExpander"] summary svg { fill: $text; color: $text; }
[data-testid="stRadio"] label p { color: $text !important; }
[data-testid="stExpander"] summary p { font-weight: 600; font-size: 15px; }
.lga-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.lga-table th { text-align: left; color: $muted; font-weight: 600; border-bottom: 1px solid $border; padding: 8px 6px; }
.lga-table td { border-bottom: 1px solid $border; padding: 7px 6px; color: $text; }
.lga-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar { background: $track; border-radius: 4px; height: 10px; width: 120px; display: inline-block; vertical-align: middle; }
.bar b { display: block; height: 10px; border-radius: 4px; }
.stDownloadButton button { background: $surface; color: $text; border: 1px solid $border; }
.footer { font-size: 12px; color: $muted !important; display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; border-top: 1px solid $border; padding-top: 12px; margin-top: 10px; }
</style>
""").substitute(T)
st.markdown(CSS, unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Header: title, Area dropdown, reset, theme button
# ----------------------------------------------------------------------------
area = st.session_state.area
sel = None if area == ALL else stats.loc[stats["lga"] == area].iloc[0]
place_title = "Kaduna State" if sel is None else f"{area} LGA"

with st.container(key="hdr"):
    c_title, c_area, c_reset, c_theme = st.columns([3.2, 1.6, 0.9, 0.9], vertical_alignment="bottom")
    with c_title:
        st.markdown(
            f'<div class="hdr-eyebrow">AGRI WATCH · AGRICULTURAL DROUGHT MONITOR</div>'
            f'<div class="hdr-title">{place_title} <span>· {period_label}</span></div>'
            f'<div class="hdr-sub">How dry was the farming season, and where?</div>',
            unsafe_allow_html=True,
        )
    with c_area:
        st.selectbox("Area", [ALL] + lga_names, key="area")
    with c_reset:
        if sel is not None:
            st.button("✕ All Kaduna", on_click=reset_area, width="stretch")
    with c_theme:
        st.button("Light" if dark else "Dark", on_click=toggle_theme, width="stretch",
                  icon=":material/light_mode:" if dark else ":material/dark_mode:",
                  help="Switch to light mode" if dark else "Switch to dark mode")

# ----------------------------------------------------------------------------
# KPI cards
# ----------------------------------------------------------------------------
if sel is None:
    classed = stats["classed_km2"].sum()
    dry = stats["dry_km2"].sum()
    pct = 100 * dry / classed if classed else 0
    n_dry = int((stats["pct_drought"] >= DROUGHT_LGA_THRESHOLD).sum())
    kpis = [
        (f"{pct:.0f}%", "of Kaduna's cropland in drought", "Moderate or worse", None),
        (fmt_km(dry), "of cropland affected", f"Out of {fmt_km(classed)} of cropland", None),
        (f"{n_dry} of {n_lgas}", "LGAs in drought",
         f"{DROUGHT_LGA_THRESHOLD}% or more of their cropland affected", None),
    ]
else:
    kpis = [
        (f"{sel.pct_drought:.0f}%", f"of {area}'s cropland in drought", "Moderate or worse", None),
        (fmt_km(sel.dry_km2), f"of cropland affected in {area}",
         f"Out of {fmt_km(sel.classed_km2)} of cropland", None),
        (sel.avg_class, "average drought class",
         f"Ranked {sel['rank']} of {n_lgas} LGAs by % in drought", CLASS_COLOURS[sel.avg_class]),
    ]

kpi_html = '<div class="kpis">'
for value, label, note, dot in kpis:
    dot_html = f'<span class="kpi-dot" style="background:{dot}"></span>' if dot else ""
    kpi_html += (f'<div class="kpi"><div class="kpi-value">{dot_html}{value}</div>'
                 f'<div class="kpi-label">{label}</div><div class="kpi-note">{note}</div></div>')
kpi_html += "</div>"
st.markdown(kpi_html, unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Row 1: map + ranking
# ----------------------------------------------------------------------------
PLOTLY_CFG = {"displayModeBar": False}


def base_layout(fig, height):
    fig.update_layout(
        height=height, margin=dict(l=4, r=8, t=6, b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color=T["text"], size=12),
        hoverlabel=dict(bgcolor=T["surface"], font_color=T["text"], bordercolor=T["border"]),
        showlegend=False,
    )
    return fig


col_map, col_rank = st.columns([2.2, 1], gap="medium")

with col_map:
    with st.container(key="card_map"):
        h1, h2 = st.columns([1, 1.25], vertical_alignment="center")
        h1.markdown('<p class="card-title">Where is the farmland dry?</p>', unsafe_allow_html=True)
        with h2:
            st.segmented_control("Map layer", LAYER_NAMES, key="layer", label_visibility="collapsed")
        layer = st.session_state.layer or "Drought"

        # Base map
        m = folium.Map(tiles=None, control_scale=True, zoom_snap=0.25)
        folium.TileLayer(tiles=T["tiles"], attr="Basemap © Esri", name="Basemap").add_to(m)
        info = layers[layer]
        folium.raster_layers.ImageOverlay(
            image=str(WEB / info["png"]), bounds=info["bounds"], opacity=T["overlay_opacity"], zindex=2,
        ).add_to(m)

        # LGA outlines with hover tooltips (transparent fill so hovering works anywhere inside)
        tip_geo = json.loads(json.dumps(geo))
        lookup = stats.set_index("lga")
        for f in tip_geo["features"]:
            name = f["properties"]["lga"].strip()
            if name in lookup.index:
                r = lookup.loc[name]
                f["properties"].update({
                    "pct": f"{r.pct_drought:.0f}% of cropland in drought",
                    "cls": f"Average class: {r.avg_class}",
                    "crop": f"Cropland: {fmt_km(r.classed_km2)}",
                })
            else:
                f["properties"].update({"pct": "No data", "cls": "", "crop": ""})
        folium.GeoJson(
            tip_geo,
            style_function=lambda _: {"color": T["lga_line"], "weight": 1.4, "opacity": 0.85, "fillOpacity": 0},
            highlight_function=lambda _: {"weight": 3, "color": T["lga_line"], "opacity": 1},
            tooltip=folium.GeoJsonTooltip(fields=["lga", "pct", "cls", "crop"], labels=False,
                                          sticky=True),
        ).add_to(m)

        # Zoom: whole state, or the selected LGA
        if sel is None:
            m.fit_bounds(info["bounds"])
        else:
            feat = next((f for f in geo["features"] if f["properties"]["lga"].strip() == area), None)
            m.fit_bounds(geo_bounds(feat) if feat else info["bounds"])

        st_folium(m, height=470, use_container_width=True, returned_objects=[],
                  key=f"map-{area}-{layer}-{st.session_state.theme}")

        if layer == "Drought":
            items = "".join(f'<span><i style="background:{CLASS_COLOURS[c]}"></i>{c}</span>' for c in CLASSES)
            note = "Cropland only · hover an LGA for details"
        else:
            items = "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c in ANOMALY_LEGENDS[layer])
            note = f"{layer} anomaly vs 2001–{target_year} · cropland only"
        st.markdown(f'<div class="legend">{items}<span class="note">{note}</span></div>',
                    unsafe_allow_html=True)

with col_rank:
    with st.container(key="card_rank"):
        st.markdown('<p class="card-title">Which LGAs are hit hardest?</p>'
                    '<p class="card-sub">% of cropland in moderate or worse drought</p>',
                    unsafe_allow_html=True)
        top = stats.head(10).copy()
        if sel is not None and sel["rank"] > 10:
            top = pd.concat([top, stats.loc[stats["lga"] == area]])
        top = top.iloc[::-1]  # highest at the top of the chart
        is_sel = top["lga"] == area
        labels = [f"{'<b>' if s else ''}{r}. {n}{'</b>' if s else ''}"
                  for r, n, s in zip(top["rank"], top["lga"], is_sel)]
        fig = go.Figure(go.Bar(
            x=top["pct_drought"], y=labels, orientation="h",
            marker=dict(color=[CLASS_COLOURS[c] for c in top["avg_class"]],
                        line=dict(color=[T["text"] if s else "rgba(0,0,0,0)" for s in is_sel], width=2)),
            text=[f"{v:.0f}%" for v in top["pct_drought"]], textposition="outside",
            textfont=dict(color=T["text"]),
            customdata=top[["classed_km2", "avg_class"]],
            hovertemplate="%{y}<br>%{x:.0f}% in drought<br>Cropland %{customdata[0]:,.0f} km²"
                          "<br>Average class: %{customdata[1]}<extra></extra>",
        ))
        base_layout(fig, 470)
        fig.update_xaxes(visible=False, range=[0, max(100, top["pct_drought"].max() * 1.15)])
        fig.update_yaxes(tickfont=dict(color=T["text"], size=12))
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CFG)
        st.markdown(f'<p class="card-sub">Top 10 of {n_lgas} LGAs · bar colour = LGA\'s average drought class</p>',
                    unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Row 2: state trend + donut
# ----------------------------------------------------------------------------
col_trend, col_donut = st.columns([2.2, 1], gap="medium")

with col_trend:
    with st.container(key="card_trend"):
        ranked = trend.sort_values("mean_cdi").reset_index(drop=True)
        hits = ranked.index[ranked["year"] == target_year]
        callout = ""
        if len(hits):
            pos = int(hits[0]) + 1
            first = int(trend["year"].min())
            callout = (f"{target_year}: driest season since {first}" if pos == 1
                       else f"{target_year}: {ordinal(pos)} driest season since {first}")
        a, b = st.columns([2, 1], vertical_alignment="bottom")
        a.markdown(f'<p class="card-title">How does {target_year} compare?</p>'
                   f'<p class="card-sub">Kaduna State drought index for cropland, {season}, '
                   f'{int(trend["year"].min())}–{int(trend["year"].max())} · below zero = drier than normal</p>',
                   unsafe_allow_html=True)
        b.markdown(f'<p class="callout" style="text-align:right">{callout}</p>', unsafe_allow_html=True)

        colours = [T["pos_bar"] if v >= 0 else
                   (T["normal_bar"] if cdi_class(v) == "Normal" else CLASS_COLOURS[cdi_class(v)])
                   for v in trend["mean_cdi"]]
        is_target = trend["year"] == target_year
        fig = go.Figure(go.Bar(
            x=trend["year"], y=trend["mean_cdi"],
            marker=dict(color=colours,
                        line=dict(color=[T["text"] if t else "rgba(0,0,0,0)" for t in is_target], width=2)),
            customdata=[cdi_class(v) for v in trend["mean_cdi"]],
            hovertemplate="%{x}<br>Index %{y:.2f}<br>%{customdata}<extra></extra>",
        ))
        base_layout(fig, 260)
        fig.add_hline(y=0, line_color=T["axis"], line_width=1)
        fig.update_xaxes(tickmode="linear", dtick=2, tickfont=dict(color=T["muted"], size=11),
                         showgrid=False)
        fig.update_yaxes(tickfont=dict(color=T["muted"], size=11), gridcolor=T["track"],
                         zeroline=False, title=None)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CFG)

with col_donut:
    with st.container(key="card_donut"):
        place = "Kaduna's" if sel is None else f"{area}'s"
        st.markdown('<p class="card-title">How is the cropland split?</p>'
                    f'<p class="card-sub">Share of {place} cropland by drought class</p>',
                    unsafe_allow_html=True)
        src = stats if sel is None else stats.loc[stats["lga"] == area]
        areas = [src[f"{c.lower()}_km2"].sum() for c in CLASSES]
        total = sum(areas) or 1
        dry_pct = 100 * sum(areas[2:]) / total
        fig = go.Figure(go.Pie(
            labels=CLASSES, values=areas, hole=0.62, sort=False, direction="clockwise",
            marker=dict(colors=[CLASS_COLOURS[c] for c in CLASSES], line=dict(color=T["surface"], width=2)),
            textinfo="none",
            hovertemplate="%{label}<br>%{value:,.0f} km² (%{percent})<extra></extra>",
        ))
        base_layout(fig, 200)
        fig.add_annotation(text=f"<b>{dry_pct:.0f}%</b><br><span style='font-size:11px'>in drought</span>",
                           showarrow=False, font=dict(size=22, color=T["text"]))
        d1, d2 = st.columns([1, 1], vertical_alignment="center")
        with d1:
            st.plotly_chart(fig, width="stretch", config=PLOTLY_CFG)
        with d2:
            rows = "".join(
                f'<div style="display:flex;justify-content:space-between;gap:8px;margin:6px 0;font-size:13px">'
                f'<span><i style="background:{CLASS_COLOURS[c]};width:12px;height:12px;border-radius:3px;'
                f'display:inline-block;margin-right:8px;vertical-align:-1px"></i>{c}</span>'
                f'<b>{100 * v / total:.0f}%</b></div>'
                for c, v in zip(CLASSES, areas))
            st.markdown(rows, unsafe_allow_html=True)
        st.markdown('<p class="card-sub">Moderate + Severe + Extreme = "in drought"</p>',
                    unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Folded sections: LGA table, about
# ----------------------------------------------------------------------------
with st.expander(f"LGA table — all {n_lgas} LGAs"):
    sort_by = st.radio("Sort by", ["% in drought", "Name", "Cropland area"], horizontal=True)
    table = stats.copy()
    if sort_by == "Name":
        table = table.sort_values("lga")
    elif sort_by == "Cropland area":
        table = table.sort_values("classed_km2", ascending=False)
    rows = ""
    for _, r in table.iterrows():
        hi = f' style="background:{T["row_hi"]}"' if r.lga == area else ""
        rows += (f"<tr{hi}><td>{r['rank']}</td><td>{r.lga}</td>"
                 f'<td><span class="bar"><b style="width:{min(r.pct_drought, 100):.0f}%;'
                 f'background:{CLASS_COLOURS[r.avg_class]}"></b></span> {r.pct_drought:.0f}%</td>'
                 f'<td class="num">{r.pct_severe:.0f}%</td><td>{r.avg_class}</td>'
                 f'<td class="num">{r.mean_cdi:.2f}</td><td class="num">{r.classed_km2:,.0f}</td>'
                 f'<td class="num">{r.dry_km2:,.0f}</td></tr>')
    st.markdown(
        '<div style="overflow-x:auto"><table class="lga-table"><thead><tr>'
        "<th>Rank</th><th>LGA</th><th>% in drought</th><th>% severe+</th><th>Avg class</th>"
        "<th>Mean index</th><th>Cropland km²</th><th>Affected km²</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>",
        unsafe_allow_html=True)
    export = stats[["rank", "lga", "pct_drought", "pct_severe", "avg_class", "mean_cdi",
                    "classed_km2", "dry_km2", "normal_km2", "mild_km2", "moderate_km2",
                    "severe_km2", "extreme_km2"]]
    st.download_button("Download CSV", export.to_csv(index=False).encode(),
                       file_name=f"kaduna_lga_drought_{period}.csv", mime="text/csv")

with st.expander("About the data and method"):
    st.markdown(f"""
**What the map shows.** Agricultural drought on cropland in Kaduna State for {period_label}, from a
composite drought index (CDI) that combines three standardized anomalies:

- **Rainfall** – CHIRPS rainfall total for the season (weight 0.4)
- **NDVI** – how green the crops are, from MODIS Terra MOD09A1 (weight 0.3)
- **NDMI** – how much water is in the crop canopy, from the same MODIS data (weight 0.3)

Each anomaly is a z-score against the same season in 2001–{int(trend['year'].max())}. The weighted sum is
re-standardized and classified: **Normal** > −0.5 · **Mild** −0.5 to −1.0 · **Moderate** −1.0 to −1.5 ·
**Severe** −1.5 to −2.0 · **Extreme** ≤ −2.0. "In drought" means moderate or worse. An LGA counts as
"in drought" when {DROUGHT_LGA_THRESHOLD}% or more of its cropland is in drought.

**Cropland** is ESA WorldCover 2021 (class 40). **Boundaries** are FAO GAUL 2015.

**Resolution.** Vegetation data is about 500 m and rainfall about 5.5 km. Map layers are smoothed for display.
Results should be checked against field reports (e.g. FEWS NET) before operational use.
""")

st.markdown(
    '<div class="footer"><span>Data: CHIRPS rainfall · MODIS Terra MOD09A1 · ESA WorldCover 2021 · '
    'FAO GAUL boundaries</span><span>Processed in Google Earth Engine · AGRI Watch</span></div>',
    unsafe_allow_html=True)
