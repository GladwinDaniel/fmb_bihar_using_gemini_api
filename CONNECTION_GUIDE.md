# Frontend <-> Backend Connection Guide

This document explains exactly how the **frontend** (browser) and the **backend** (Flask server) are connected and communicate, designed for multi-team collaboration.

---

## High-Level Architecture

```
Browser (frontend/)                Flask API Server (backend/app.py)
===============================    ======================================
index.html  <->  app.js            app.py   -> BhuNaksha GIS Server
   UI Layer     Logic Layer            |        (bhunaksha.bihar.gov.in)
                    |                  |      -> Bihar State ArcGIS Server
                    | HTTP           models.py  (gisserver.bihar.gov.in)
                    | AJAX           SQLite DB -> Google Gemini API
                    |                  |
                    +--------------> Port 5001
```

- The **frontend** is pure HTML + JavaScript. It runs entirely in the browser.
- The **backend** is a Python Flask server. It runs as a local process on the same machine.
- They communicate over **HTTP** using **AJAX** (`$.ajax`, `$.post`) calls from the frontend to the backend API.
- The backend acts as a **proxy** -- it relays requests to government servers (BhuNaksha, Bihar ArcGIS) that would otherwise be blocked by browser CORS policies.

---

## Connection Configuration

### How the frontend knows the backend address

In **[`app.js`](frontend/app.js)** (lines 2-4):

```javascript
const API_BASE_URL = window.location.hostname === 'localhost'
    || window.location.hostname === '127.0.0.1'
    || window.location.protocol === 'file:'
    ? 'http://127.0.0.1:5001'  // Development: point to local Flask
    : '';                        // Production: use same origin (empty = relative URLs)
```

**What this means:**
- When opening the page from `file://` or `localhost`, all API calls go to `http://127.0.0.1:5001`.
- In a production deployment (same server hosting both frontend and backend), the URL is left empty, making calls relative (e.g., `/proxy/WMS` instead of `http://127.0.0.1:5001/proxy/WMS`).

### Flask CORS Configuration

In **[`app.py`](backend/app.py)** (lines 17-20):

```python
from flask_cors import CORS
CORS(app)
```

`flask-cors` adds `Access-Control-Allow-Origin: *` headers to all Flask responses, allowing the browser to make cross-origin requests from `file://` or `http://localhost:8080` to `http://127.0.0.1:5001`.

---

## Complete API Contract

All frontend -> backend calls follow this pattern:

| Who sends | Method | URL Pattern | Data Format | Response Format |
|---|---|---|---|---|
| Frontend | POST | `/proxy/...` | `application/x-www-form-urlencoded` (jQuery default) | JSON |
| Frontend | GET | `/proxy/WMS` | URL query parameters | `image/png` binary |
| Frontend | POST | `/api/parcel/...` | `application/json` | JSON |
| Frontend | GET | `/api/...` | URL query parameters | JSON or binary (PDF/GeoJSON) |

---

## Endpoint-by-Endpoint Reference

### 1. Administrative Dropdown Data

**Frontend calls:** `$.post(API_BASE_URL + "/proxy/Levels/ListsAfterLevel", {...})`
**Backend route:** `POST /proxy/Levels/ListsAfterLevel` -> `proxy_lists_after_level()`

| Request Field | Type | Example |
|---|---|---|
| `state` | string | `"10"` (Bihar state code) |
| `level` | string | `"0"` for districts, `"1"` for subdivisions... |
| `codes` | string | `"09,"` (comma-separated parent codes) |

**Response:** JSON array of `[{code, value}]` option objects.

**Cache:** Disk-persisted in `backend/dropdown_cache.json`. Subsequent requests are served instantly from cache without hitting the government server.

---

### 2. Sheet Extent / Bounding Box

**Frontend calls:** `$.post(API_BASE_URL + "/proxy/MapInfo/getVVVVExtentGeoref", {...})`
**Backend route:** `POST /proxy/MapInfo/getVVVVExtentGeoref` -> `proxy_extent()`

| Request Field | Type | Example |
|---|---|---|
| `state` | string | `"10"` |
| `gisLevels` | string | `"09,01,05,003,01,01,01,"` (full 7-level code) |
| `srs` | string | `"4326"` for GPS, `"0"` for UTM |

**Response:** `{gisCode, xmin, ymin, xmax, ymax}` bounding box.

**What happens next:** The frontend calls `map.getView().fit([xmin, ymin, xmax, ymax])` to zoom the OpenLayers map to the selected sheet.

---

### 3. WMS Cadastral Map Tiles

**Frontend:** The OpenLayers WMS source calls `GET /proxy/WMS?LAYERS=VILLAGE_MAP&gis_code=...` automatically as the map is panned/zoomed.
**Backend route:** `GET /proxy/WMS` -> `proxy_wms()`

This is a **tile proxy** -- the browser requests WMS tiles (PNG images) through the Flask backend. Flask:
1. Checks a disk cache (`backend/static/wms_cache/{md5_hash}.png`)
2. If not cached: fetches from `https://bhunaksha.bihar.gov.in/WMS` and saves
3. Returns the PNG image data to the browser

The frontend never talks to BhuNaksha directly -- everything goes through the Flask proxy.

---

### 4. Click-to-Select Parcel at GPS Coordinates

**Frontend calls:** `$.post(API_BASE_URL + "/proxy/MapInfo/getPlotAtGPS", {...})`
**Backend route:** `POST /proxy/MapInfo/getPlotAtGPS` -> `get_plot_at_gps()`

This is a **smart coordinate conversion** endpoint:
1. Frontend sends GPS [lon, lat] of the mouse click
2. Backend checks local SQLite DB for any cached parcel containing that point
3. If not found locally: fetches GPS and UTM extents, projects GPS -> UTM using `pyproj`, then calls `getPlotAtXY` on BhuNaksha
4. Returns `{kide: "1587"}` -- the plot number at that coordinate
5. Frontend then calls `selectPlotByNumber("1587")` to load full details

---

### 5. Full Plot Details & Geometry Fetch

**Frontend calls:** `$.post(API_BASE_URL + "/proxy/MapInfo/getPlotDetailsAndInspection", {...})`
**Backend route:** `POST /proxy/MapInfo/getPlotDetailsAndInspection` -> `get_plot_details_and_inspection()`

This is the **most complex** backend endpoint. The flow:

```
Frontend sends: {state, giscode, plot_no, levels}
                      |
Backend Step 1: Check SQLite (Parcel table) -- return cached data immediately if found
                      | (if not cached)
Backend Step 2: Call BhuNaksha /ScalarDatahandler -> parse HTML for owner names, khata
Backend Step 3: Call BhuNaksha /rest/MapInfo/getPlotInfo -> parse WKT geometry polygon
Backend Step 4: Convert UTM polygon vertices -> GPS coordinates using pyproj
Backend Step 5: Call BhuNaksha /rest/Reports/PlotReportPDF -> decode base64 -> save PDF
Backend Step 6: Persist everything to SQLite (Parcel, ParcelVertex, BoundarySegment, LdmReport)
                      |
Frontend receives: {success, parcel{...}, vertices[...], segments[...], report{url}}
                      |
Frontend: updateParcelSidebar() + drawPolygonOnMap() + zoomToParcel()
```

**Response structure:**
```json
{
  "success": true,
  "cached": false,
  "parcel": {
    "id": 42,
    "plot_no": "1587",
    "khata_no": "143",
    "pniu": "10XXXXXXXXXX0000",
    "area": 1234.56,
    "perimeter": 145.2,
    "lat": 25.5941,
    "lon": 85.1376,
    "owner_names": ["Ramesh Kumar", "Suresh Kumar"],
    "district": "09", "circle": "05"
  },
  "vertices": [{"x": 412345.0, "y": 2812345.0, "lon": 85.13, "lat": 25.59, "sequence_order": 0}],
  "segments": [{"start_vertex": 0, "end_vertex": 1, "length_meters": 34.5, "bearing": 92.3}],
  "report": {"url": "/static/reports/GXXXXXXXX_1587.pdf"}
}
```

---

### 6. PNIU Search

**Frontend calls:** `$.post(API_BASE_URL + "/proxy/MapInfo/getPointsfromPNIU", {...})`
**Backend route:** `POST /proxy/MapInfo/getPointsfromPNIU` -> `proxy_pniu_points()`

| Request Field | Type | Description |
|---|---|---|
| `state` | string | `"10"` |
| `pniu` | string | The 14-digit PNIU code |
| `gisCode` | string | Current sheet GIS code |

**Response:** Raw comma-delimited string. The 6th field (index 5) is the plot number. Frontend then calls `selectPlotByNumber(plotNo)`.

---

### 7. Kurra Division (AI Land Subdivision)

**Frontend calls:** `$.ajax({url: API_BASE_URL + /api/parcel/${plotNo}/subdivide?parcel_id=${parcelId}, type: "POST", contentType: "application/json", data: JSON.stringify(payload)})`
**Backend route:** `POST /api/parcel/<plot_no>/subdivide` -> `subdivide_parcel()`

**Request body (JSON):**
```json
{
  "shares": [50.0, 50.0],
  "features": [{"type": "tree", "x": 85.1376, "y": 25.5941}],
  "parcel_info": { "...parcel metadata..." }
}
```

**Backend process:**
1. Load parcel from SQLite by `plot_no` + `parcel_id`
2. Build UTM polygon from stored vertex coordinates
3. Call `gis_querier.get_nearby_vector_features()` -> query Bihar ArcGIS for roads and rivers
4. Call `subdivide.generate_strategies()` -> generate 2-4 geometric split strategies
5. Pre-calculate per-strategy stats (road frontage, river frontage, features per sub-plot)
6. Call `llm_expert.consult_llm_for_division()` -> send all strategy stats to Google Gemini API
7. Return best strategy polygons + LLM explanation

**Response:**
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"sub_plot_id": 1, "share_percentage": 50.0, "area_sqm": 617.28},
      "geometry": {"type": "Polygon", "coordinates": [[[...]]]}
    }
  ],
  "strategy_name": "Road Access Cut (Perpendicular to Road)",
  "llm_explanation": "Strategy 3 is recommended because...",
  "llm_failed": false,
  "frontage_coords": [[85.13, 25.59]]
}
```

---

### 8. PDF Kurra Report Generation

**Frontend calls:** `$.ajax({url: API_BASE_URL + /api/parcel/${plotNo}/generate_report?parcel_id=${parcelId}, type: "POST", xhrFields: {responseType: 'blob'}})`
**Backend route:** `POST /api/parcel/<plot_no>/generate_report` -> `generate_report_route()`

The frontend sends the Kurra sub-plot data (coordinates, stats, LLM explanation). The backend calls `report_generator.generate_kurra_report()` which:
1. Draws the parcel map using OpenCV (white background + polygon outlines + feature dots)
2. Generates a multi-page PDF using FPDF2 (Land Details, Dashboard, Segregation Details, AI Summary)
3. Returns raw PDF bytes with `Content-Disposition: attachment` header

The frontend receives the binary blob and triggers a browser download.

---

### 9. Data Exports

| Endpoint | Frontend trigger | Response |
|---|---|---|
| `GET /proxy/Export/GeoJSON/<plot_no>?parcel_id=X` | `window.open(url)` | RFC 7946 GeoJSON file download |
| `GET /proxy/Export/CSV/<plot_no>?parcel_id=X` | `window.open(url)` | CSV file with all vertex UTM + GPS coordinates |
| `GET /api/sheet/export_geojson?state=10&levels=...` | `window.location.href = url` | GeoJSON FeatureCollection of all cached parcels in a sheet |

---

### 10. Batch Sheet Scraper

**Frontend calls (repeated loop):** `$.ajax({url: API_BASE_URL + "/api/sheet/scrape_batch", type: "POST", contentType: "application/json", data: JSON.stringify({batch_index, batch_size, ...})})`
**Backend route:** `POST /api/sheet/scrape_batch` -> `scrape_batch()`

The frontend loops, sending batch requests (65 grid points at a time). The backend:
1. Generates a uniform grid of UTM coordinates over the sheet bounding box
2. For each grid point not already inside a known polygon: calls `getPlotAtXY` to discover the plot number
3. Calls `get_plot_details_and_inspection()` to fetch and store the full geometry
4. Returns progress info to frontend, which continues until `is_done: true`

---

## Data Flow Summary Diagram

```
User Action              Frontend (app.js)              Backend (app.py)              External
=======================  ===========================    ==========================   ===============
Select District          -> POST /proxy/Levels/...    -> BhuNaksha REST API (or cache)
Select Sheet             -> POST /proxy/.../getVVVVExtentGeoref -> BhuNaksha (or cache)
WMS loads automatically  <- GET /proxy/WMS            -> BhuNaksha WMS (or disk cache)
Click on map plot        -> POST /proxy/.../getPlotAtGPS -> pyproj -> BhuNaksha XY API
                         -> POST /proxy/.../getPlotDetailsAndInspection
                                                    -> ScalarDatahandler (owner data)
                                                    -> getPlotInfo (polygon WKT)
                                                    -> PlotReportPDF (base64 PDF)
                                                    -> SQLite (persist parcel)
Kurra Division click     -> POST /api/parcel/.../subdivide
                                                    -> ArcGIS REST (roads, rivers)
                                                    -> subdivide.py (Shapely math)
                                                    -> Google Gemini API
Download PDF Report      -> POST /api/parcel/.../generate_report
                                                    -> OpenCV + FPDF2 -> PDF bytes
Export GeoJSON           -> GET /proxy/Export/GeoJSON/... -> SQLite -> GeoJSON response
```

---

## Testing the Connection

You can test individual backend API endpoints directly using curl or a browser:

**Health check:**
```bash
curl http://127.0.0.1:5001/
```
Expected: `{"service": "Bihar Cadastral Map & Satellite Dashboard (Bhu-Overlay) API", "status": "online"}`

**Test dropdown (requires internet to Bihar server or cache):**
```bash
curl -X POST http://127.0.0.1:5001/proxy/Levels/ListsAfterLevel \
  -d "state=10&level=0&codes=&hasmap=true"
```

**Test plot details:**
```bash
curl -X POST http://127.0.0.1:5001/proxy/MapInfo/getPlotDetailsAndInspection \
  -d "state=10&giscode=XXXXX&plot_no=1587&levels=09,01,05,003,01,01,01,"
```

---

## Sequence Diagram -- Full Plot Selection Flow

```
Browser              app.js              Flask backend         BhuNaksha server
  |                    |                      |                      |
  |-- map click -----> |                      |                      |
  |                    |-- POST getPlotAtGPS->|                      |
  |                    |                      |-- check SQLite ----> DB
  |                    |                      |    (cache miss)      |
  |                    |                      |-- POST getVVVVExtent->
  |                    |                      |<---- GPS extent -----|
  |                    |                      |-- POST getPlotAtXY ->
  |                    |                      |<--- plot_no "1587" --|
  |                    |<-- {kide:"1587"} ----|                      |
  |                    |-- POST getPlotDetails>|                     |
  |                    |                      |-- ScalarDatahandler->
  |                    |                      |<- owner names HTML --|
  |                    |                      |-- getPlotInfo ------>
  |                    |                      |<-- WKT polygon ------|
  |                    |                      |-- PlotReportPDF ---->
  |                    |                      |<-- base64 PDF -------|
  |                    |                      |-- save to SQLite --> DB
  |                    |<-- {parcel,          |
  |                    |    vertices,         |                      |
  |                    |    segments, report} |                      |
  |<- update sidebar --|                      |                      |
  |<- draw polygon ----|                      |                      |
  |<- zoom to parcel --|                      |                      |
```

---

## Configuration Summary

| Setting | Location | Default | Description |
|---|---|---|---|
| Backend port | `backend/app.py` last line | `5001` | Flask server listen port |
| Backend URL | `frontend/app.js` lines 2-4 | `http://127.0.0.1:5001` | Frontend API target |
| BhuNaksha URL | `backend/app.py` line 28 | `https://bhunaksha.bihar.gov.in` | Government GIS server |
| LLM URL | `backend/llm_expert.py` | Gemini API endpoint | Google Gemini API URL |
| LLM model | `backend/llm_expert.py` | `gemini-2.5-flash` | Gemini model name (set via `GEMINI_MODEL` env var) |
| Gemini API Key | `GEMINI_API_KEY` env var | (none -- required) | Must be set to enable AI Kurra division |
| State code | `frontend/app.js` | `"10"` | Bihar state code (fixed) |
| SQLite DB | `backend/app.py` | `sqlite:///bhunaksha.db` | Parcel persistence |
