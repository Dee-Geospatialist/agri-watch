# AGRI Watch – Kaduna Agricultural Drought Monitor

Streamlit dashboard showing agricultural drought on cropland in Kaduna State, built from
Google Earth Engine exports (rainfall, NDVI and NDMI anomalies → composite drought index).

## 1. Set up the environment (Miniconda, Python 3.11)

```bash
conda create -n agriwatch python=3.11 -y
conda activate agriwatch
conda install -c conda-forge rasterio -y        # easiest way to get rasterio on Windows
pip install -r requirements.txt -r requirements-prep.txt
```

## 2. Add the GEE exports

From Google Drive, download everything in the folder **AGRI_Watch_Kaduna_web** into `data/raw/`:

```
data/raw/
├── drought_class.tif
├── rain_anomaly.tif
├── ndvi_anomaly.tif
├── ndmi_anomaly.tif
├── lga_stats.csv
├── state_trend.csv
└── lga_boundaries.geojson
```

(Drive sometimes adds a suffix such as `lga_stats (1).csv` – rename to the names above.)

## 3. Prepare the data (once, and again whenever you re-export)

```bash
python prepare_data.py
```

This writes coloured PNG map layers and clean tables to `data/web/`. Check the output for a
"names not matching" warning.

## 4. Run the dashboard

```bash
streamlit run app.py
```

Opens at http://localhost:8501.

## 5. Deploy (Streamlit Community Cloud, free)

1. Create a GitHub repo and push this folder. `data/raw/` is ignored; **`data/web/` must be committed**.
2. Go to https://share.streamlit.io → New app → pick the repo, branch `main`, file `app.py`.
3. Python version: 3.11. It installs `requirements.txt` automatically.

## Project layout

| Path | Purpose |
|---|---|
| `app.py` | The dashboard |
| `prepare_data.py` | GeoTIFF → PNG layers, table checks |
| `.streamlit/config.toml` | Base theme colours |
| `data/raw/` | GEE exports (not committed) |
| `data/web/` | Prepared files the app reads (committed) |
| `requirements.txt` | App dependencies (pinned Streamlit version – the custom styling relies on it) |
| `requirements-prep.txt` | Extra dependencies for `prepare_data.py` |

## Data and method

CHIRPS rainfall, MODIS Terra MOD09A1 (NDVI, NDMI), ESA WorldCover 2021 cropland, FAO GAUL 2015
boundaries. Anomalies are z-scores against the same season in 2001–2025, combined
0.4 / 0.3 / 0.3, re-standardized, and classified into Normal, Mild, Moderate, Severe, Extreme.
