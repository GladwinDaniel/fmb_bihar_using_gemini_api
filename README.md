# Bhu-Overlay -- Bihar Cadastral Map & Satellite Dashboard (Gemini API Edition)

**Bhu-Overlay** is an AI-assisted land record and cadastral mapping platform for Bihar, India. It overlays the official **BhuNaksha** government cadastral (FMB sketch) maps directly on satellite imagery and provides an intelligent **Kurra (land subdivision) analysis engine** that uses the **Google Gemini API** to recommend the most legally compliant land division strategy under the UP Revenue Code, 2006.

> **This version uses Google's Gemini API** (`GEMINI_API_KEY`) instead of a local LLM. No LM Studio or local AI server is required.

---

## Key Features

| Feature | Description |
|---|---|
| **Satellite + Cadastral Overlay** | Fuse BhuNaksha WMS cadastral tiles over ArcGIS satellite imagery |
| **Parcel Click-to-Inspect** | Click any plot on the map to fetch ownership, area, PNIU, and boundary geometry |
| **AI Kurra Division** | Generate multiple land-split strategies with Gemini AI-ranked recommendation citing UP Revenue Code Sections 116/117 |
| **Real GIS Infrastructure** | Query Bihar Government GIS servers for live road & river vectors around a selected plot |
| **Offline-First Caching** | Fetched parcel geometries persist in a local SQLite DB -- works without internet after first load |
| **PDF Kurra Report** | Downloadable PDF with the division map, AI explanation, and per-sub-plot statistics |
| **GeoJSON / CSV Export** | Export any parcel boundary as GeoJSON or CSV (UTM + WGS84 coordinates) |
| **Batch Sheet Scraper** | Auto-discover and cache all plot geometries in a given cadastral sheet |
| **Bihar GIS Overlays** | Toggle National Highways, State Highways, Village Roads, Rivers, Panchayat Boundaries, Police Stations, and more |
| **PNIU Search** | Search plots by 14-digit PNIU code |
| **Boundary Measurements** | View area (sq m, acres, hectares), perimeter, vertex count, and per-side lengths |

---

## Architecture Overview

```
+-------------------------------------------------------------+
|                     Browser (Frontend)                      |
|                                                             |
|  index.html + style.css        app.js (Application Logic)  |
|  -----------------             --------------------------   |
|  Responsive sidebar UI         OpenLayers v6 Map Engine     |
|  Collapsible controls          Vector + Tile Layer Manager  |
|  Kurra wizard panel            AJAX calls to Flask backend  |
|  Tree/Well placement tools     State management (parcels,   |
|  GIS layer checkboxes          offsets, features cache)     |
+-------------------+-----------------------------------------+
                    |  HTTP (AJAX)
+-------------------v-----------------------------------------+
|              Flask Backend (backend/app.py)                  |
|                                                             |
|  +-------------+  +-----------+  +-----------------------+ |
|  | BhuNaksha   |  |   JSON    |  |     SQLite DB         | |
|  |  Proxy &    |  |   File    |  |   (bhunaksha.db)      | |
|  |  Session    |  |   Cache   |  |  Parcels, Vertices,   | |
|  |  Manager    |  |           |  |  Segments, Reports    | |
|  +-------------+  +-----------+  +-----------------------+ |
|                                                             |
|  +------------+ +-------------+ +----------+ +---------+   |
|  |subdivide.py| |llm_expert.py| |report_   | |gis_     |   |
|  | Polygon    | | Gemini API  | |generator | |querier  |   |
|  | Splitting  | | Prompt &    | |  .py     | |  .py    |   |
|  | (Shapely)  | |  Strategy   | |(FPDF+CV2)| |(ArcGIS) |   |
|  |            | |  Ranking    | |          | |         |   |
|  +------------+ +-------------+ +----------+ +---------+   |
+-------------------+-----------------------------------------+
                    |
        +-----------+-----------------+
        |           |                 |
+-------v---+ +-----v------+ +--------v----------+
| BhuNaksha | |Bihar Govt  | |  Google Gemini    |
| GIS Server| |GIS ArcGIS  | |      API          |
|(bhunaksha | |REST (Roads,| | (GEMINI_API_KEY)  |
|.bihar.gov)| |Rivers...)  | |                   |
+-----------+ +------------+ +-------------------+
```

---

## Prerequisites

- **Python 3.9+**
- **pip** (Python package manager)
- **Google Gemini API Key** -- get one free at [Google AI Studio](https://aistudio.google.com/)
- Internet access to Bihar government servers (optional -- data is cached locally after first fetch)

> **No LM Studio or local AI server required.** The AI Kurra division feature connects directly to Google's Gemini API in the cloud.

---

## Quick Start

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/GladwinDaniel/fmb_bihar_using_gemini_api.git
cd fmb_bihar_using_gemini_api
pip install -r requirements.txt
```

### 2. Set the Gemini API Key (Required for AI Kurra Division)

Obtain a free API key from [Google AI Studio](https://aistudio.google.com/apikey) and set it as an environment variable:

**Windows (Command Prompt):**
```cmd
set GEMINI_API_KEY=your_api_key_here
```

**Windows (PowerShell):**
```powershell
$env:GEMINI_API_KEY="your_api_key_here"
```

**macOS / Linux:**
```bash
export GEMINI_API_KEY="your_api_key_here"
```

**Or use a `.env` file** inside the `backend/` directory:
```env
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

> **Note:** If `GEMINI_API_KEY` is not set, the system automatically falls back to the first algorithmic subdivision strategy (Compact Cut).

### Optional: Use Grok Instead of Gemini

The backend supports both providers via environment variables:

`LLM_PROVIDER=gemini` (default): use `GEMINI_API_KEY`

`LLM_PROVIDER=grok`: use `GROK_API_KEY` (or `XAI_API_KEY`)

PowerShell example:

```powershell
$env:LLM_PROVIDER="grok"
$env:GROK_API_KEY="your_grok_api_key_here"
```

Optional Grok settings:
- `GROK_MODEL` (default: `grok-2-latest`)
- `GROK_API_URL` (default: `https://api.x.ai/v1/chat/completions`)

### 3. Start the Backend

```bash
cd backend
python app.py
```

The server starts at **http://127.0.0.1:5001**

### 4. Open the Frontend

Open a **new terminal**, navigate back to the project root, and serve the frontend:

```bash
# From the project root
cd ..
python -m http.server 8080
# Then open: http://localhost:8080/frontend/index.html
```

### 5. New PC Quick Commands (Copy-Paste)

Windows PowerShell:

```powershell
git clone https://github.com/GladwinDaniel/fmb_bihar_using_gemini_api.git
cd fmb_bihar_using_gemini_api
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GEMINI_API_KEY="your_api_key_here"
cd backend
python app.py
```

Open a second terminal:

```powershell
cd fmb_bihar_using_gemini_api
python -m http.server 8080
```

Then browse to: `http://localhost:8080/frontend/index.html`

### 6. Provider Setup Command Packs

Use one of the following command packs on a fresh Windows PowerShell terminal.

Gemini:

```powershell
git clone https://github.com/GladwinDaniel/fmb_bihar_using_gemini_api.git
cd fmb_bihar_using_gemini_api
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:LLM_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_gemini_api_key_here"
cd backend
python app.py
```

Grok:

```powershell
git clone https://github.com/GladwinDaniel/fmb_bihar_using_gemini_api.git
cd fmb_bihar_using_gemini_api
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:LLM_PROVIDER="grok"
$env:GROK_API_KEY="your_grok_api_key_here"
# Optional:
# $env:GROK_MODEL="grok-2-latest"
# $env:GROK_API_URL="https://api.x.ai/v1/chat/completions"
cd backend
python app.py
```

Frontend (second terminal for either provider):

```powershell
cd fmb_bihar_using_gemini_api
python -m http.server 8080
```

---

## How to Use

### Step 1 -- Select a Location
Use the **cascading dropdowns** in the sidebar to navigate the administrative hierarchy:
> **District -> Sub-Division -> Circle -> Mouza (Village) -> Survey Type -> Map Instance -> Sheet Number**

The BhuNaksha WMS cadastral layer loads automatically once a sheet is selected.

### Step 2 -- Click a Plot
Click directly on any plot boundary on the map. The application will:
- Query the BhuNaksha government server (or local SQLite cache) for plot geometry
- Display **Plot No., Khata No., PNIU, owner names, area, and perimeter** in the sidebar
- Render the **blue boundary polygon** on the map

### Step 3 -- Inspect & Measure
The **Boundary & Measurements** panel displays:
- Area in Sq Meters, Acres, and Hectares
- Perimeter in meters
- Total vertex count
- Per-segment side lengths and bearing angles

### Step 4 -- Kurra Division (Land Subdivision)
1. With a plot selected, click **"Kurra Division"** in the bottom bar
2. In the Kurra panel:
   - Set the **number of co-sharers** and **percentage splits** (must total 100%)
   - Optionally **place trees/wells** by clicking on the map
   - Enter **custom instructions** (e.g., mutual consent -- overrides all rules under Rule 109(g))
3. Click **"Generate Kurra Division"** -- the backend will:
   - Query Bihar GIS for nearby roads and rivers
   - Generate 2-4 geometric split strategies
   - Send all strategy stats to **Google Gemini** for legal ranking
4. Sub-plots render on the map with color-coded fills and the AI recommendation box appears
5. Click **"Download PDF Report"** for the full Kurra document

### Step 5 -- Export Data
- **GeoJSON**: Download the parcel boundary as a standard `.geojson` file
- **CSV**: Download all vertex coordinates (UTM + WGS84) and segment side lengths as `.csv`
- **Sheet Clone**: Download all cached parcels in the selected sheet as a GeoJSON FeatureCollection

---

## Project Structure

```
Fmb_gemini/
+-- README.md                    <- This file
+-- INSTALL.md                   <- Step-by-step installation guide
+-- TECHNICAL_DOCS.md            <- Deep technical documentation
+-- CONNECTION_GUIDE.md          <- How frontend & backend communicate
+-- requirements.txt             <- Python package dependencies
+-- backend_architecture.md      <- Backend architecture diagram
+-- frontend_architecture.md     <- Frontend architecture diagram
|
+-- backend/
|   +-- app.py                   <- Main Flask application -- API gateway + all routes
|   +-- models.py                <- SQLAlchemy DB models (Parcel, Vertex, Segment, Report)
|   +-- subdivide.py             <- Polygon splitting algorithms using Shapely binary search
|   +-- llm_expert.py            <- Prompt construction and strategy ranking using Google Gemini API
|   +-- gis_querier.py           <- Bihar GIS ArcGIS REST queries (roads, rivers, streams)
|   +-- report_generator.py      <- PDF Kurra report generator (FPDF2 + OpenCV)
|   +-- pdf_parser.py            <- BhuNaksha PDF area extraction (pdfplumber)
|   +-- dropdown_cache.json      <- Cached administrative hierarchy data (~1.2 MB)
|   +-- extent_cache.json        <- Cached village sheet extents
|   +-- session_cookies.json     <- Persisted BhuNaksha session cookies
|   +-- instance/
|       +-- bhunaksha.db         <- SQLite database (auto-created on first run)
|
+-- frontend/
    +-- index.html               <- Single-page application HTML
    +-- app.js                   <- All application logic -- OpenLayers, AJAX, UI
    +-- style.css                <- Responsive dark-theme CSS
```

---

## API Endpoints Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Health check -- returns service status |
| `POST` | `/proxy/Levels/ListsAfterLevel` | Cascading dropdown data (persistent JSON cache) |
| `POST` | `/proxy/MapInfo/getVVVVExtentGeoref` | Sheet bounding box in GPS or UTM (persistent cache) |
| `POST` | `/proxy/MapInfo/getPlotAtXY` | Plot No. at given UTM coordinate (persistent cache) |
| `POST` | `/proxy/MapInfo/getPlotAtGPS` | Plot No. at GPS coord -- converts GPS->UTM, checks local DB first |
| `POST` | `/proxy/MapInfo/getPointsfromPNIU` | Plot geometry from 14-digit PNIU code (persistent cache) |
| `POST` | `/proxy/MapInfo/getGisCode` | GIS code for selected administrative levels (persistent cache) |
| `GET` | `/proxy/WMS` | WMS map tile proxy -- cached as PNG files on disk (MD5-keyed) |
| `POST` | `/proxy/MapInfo/getPlotDetailsAndInspection` | Full parcel data fetch -- geometry, owners, PDF report + DB persist |
| `GET` | `/proxy/Export/GeoJSON/<plot_no>` | Export parcel as RFC 7946 GeoJSON Feature |
| `GET` | `/proxy/Export/CSV/<plot_no>` | Export parcel vertices and segments as CSV |
| `POST` | `/api/parcel/<plot_no>/subdivide` | AI Kurra division -- generates strategies + Gemini ranking |
| `POST` | `/api/parcel/<plot_no>/generate_report` | Generate and download PDF Kurra report |
| `POST` | `/api/sheet/scrape_batch` | Batch-scrape all plots in a cadastral sheet (grid sampling) |
| `GET` | `/api/sheet/export_geojson` | All cached plots in a sheet as GeoJSON FeatureCollection |

---

## AI / LLM Integration

The AI engine uses **Google Gemini API** (`gemini-2.5-flash` by default) on every Kurra division request. It evaluates **all generated strategies** and picks the most legally sound one.

### Legal Rules Evaluated
| Rule | Description |
|------|-------------|
| **Rule 109(f)** | Road access / commercial value -- landlocked plots are heavily penalized |
| **Rule 109(b)** | Compactness -- elongated or irregular shapes are penalized |
| **Section 116(2)** | Trees, wells & improvements -- fair distribution or compensation |
| **Rule 109(g)** | Mutual consent -- user-supplied instructions take **absolute precedence** |

### Strategy Types Generated
1. **Compact Cut** -- Parallel to the shortest side of the minimum rotated rectangle
2. **Longitudinal Cut** -- Parallel to the longest side
3. **Road Access Cut** -- Perpendicular to the nearest road (when road data is available)
4. **River Access Cut** -- Perpendicular to the nearest river (when river data is available)

Duplicate or near-identical strategies (within 5 degrees of angle difference) are automatically removed.

---

## Caching Strategy

| Cache | Storage | Contents |
|-------|---------|----------|
| `dropdown_cache.json` | JSON file | Administrative hierarchy (Districts -> Sheets) |
| `extent_cache.json` | JSON file | Sheet bounding boxes (GPS & UTM for all zoom levels) |
| `giscode_cache.json` | JSON file | GIS codes for each level combination |
| `pniu_cache.json` | JSON file | PNIU-to-geometry lookups |
| `plot_at_xy_cache.json` | JSON file | XY coordinate -> plot number mappings |
| `static/wms_cache/*.png` | PNG files | WMS map tiles (MD5-keyed, disk-cached) |
| `instance/bhunaksha.db` | SQLite | Full parcel geometries, vertices, segment lengths, report links |

---

## Known Limitations

- **BhuNaksha Server Dependency**: Data comes from the Bihar Government's BhuNaksha service. When the government server is offline, only previously cached parcels are available.
- **Gemini API Key Required**: The AI Kurra feature requires a valid `GEMINI_API_KEY`. Without it, the system falls back to the first algorithmic strategy automatically.
- **Internet Required for AI**: Unlike the local LLM version, the Gemini API requires an internet connection for every AI Kurra request.
- **API Rate Limits**: Free Gemini API keys have rate limits. For high-volume use, consider upgrading your Google AI plan.
- **SSL Verification Disabled**: Required for Bihar government servers with self-signed certificates.
- **State Hardcoded**: Currently configured for Bihar (`state=10`).
- **Coordinate System**: Handles conversion between WGS84 (EPSG:4326) and UTM Zone 45N (EPSG:32645) using `pyproj`.

---

## Legal Reference

> The Kurra division engine is designed around the **Uttar Pradesh Revenue Code, 2006** which remains applicable in Bihar for land record procedures:
> - **Section 116**: Partition of a holding
> - **Section 117**: Partition orders
> - **Rule 109(b)**: Each portion shall be as compact as possible
> - **Rule 109(f)**: Plots with road/commercial adjacency shall have access distributed proportionally
> - **Rule 109(g)**: Mutual consent of co-tenure holders supersedes all other rules

---

## License

This project is for research and educational use. All cadastral data is sourced from the Bihar Government's public BhuNaksha service (`bhunaksha.bihar.gov.in`).
