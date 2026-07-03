#  Bhu-Overlay  Technical Documentation -- Gemini API Edition

> **Version:** 1.0 | **State:** Bihar (Code: 10) | **Coordinate Systems:** WGS84 (EPSG:4326) + UTM Zone 45N (EPSG:32645)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Database Schema](#3-database-schema)
4. [Backend Modules](#4-backend-modules)
   - 4.1 [app.py  Flask Application Gateway](#41-apppy--flask-application-gateway)
   - 4.2 [subdivide.py  Polygon Splitting Engine](#42-subdividepy--polygon-splitting-engine)
   - 4.3 [llm_expert.py  AI Strategy Consultant](#43-llm_expertpy--ai-strategy-consultant)
   - 4.4 [gis_querier.py  Bihar GIS ArcGIS REST Client](#44-gis_querierpy--bihar-gis-arcgis-rest-client)
   - 4.5 [report_generator.py  PDF Kurra Report](#45-report_generatorpy--pdf-kurra-report)
   - 4.6 [pdf_parser.py  Official Area Extraction](#46-pdf_parserpy--official-area-extraction)
5. [Caching Architecture](#5-caching-architecture)
6. [Session & Authentication Management](#6-session--authentication-management)
7. [Coordinate System Handling](#7-coordinate-system-handling)
8. [API Contract (Full Reference)](#8-api-contract-full-reference)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Kurra Division  End-to-End Flow](#10-kurra-division--end-to-end-flow)
11. [Bihar GIS External Services](#11-bihar-gis-external-services)
12. [Known Issues & Edge Cases](#12-known-issues--edge-cases)

---

## 1. System Overview

Bhu-Overlay is a full-stack geospatial web application. Its primary role is to act as an **intelligent proxy and analysis layer** between the user and the Bihar Government's **BhuNaksha GIS system** (`bhunaksha.bihar.gov.in`).

### Core Problems Solved

| Problem | Solution |
|---------|----------|
| BhuNaksha cannot overlay on satellite maps | The backend proxies WMS tiles; the frontend composites them with ArcGIS satellite via OpenLayers |
| BhuNaksha has no offline/caching mode | SQLite-backed parcel geometry cache; JSON file caches for all API calls |
| Land division is manual and legally complex | AI-assisted Kurra engine generates 2-4 strategies and ranks them using UP Revenue Code rules via LLM |
| Government server sessions expire frequently | Persistent session manager with cookie disk-cache and exponential-backoff retry |
| BhuNaksha coordinates are in a local UTM system | Dynamic coordinate conversion using `pyproj` (EPSG:32645  EPSG:4326) |

---

## 2. Technology Stack

### Backend
| Library | Version | Purpose |
|---------|---------|---------|
| `Flask` | 2.x | HTTP API framework |
| `flask-cors` |  | Cross-Origin Resource Sharing for browser frontend |
| `flask-sqlalchemy` |  | ORM for SQLite database |
| `SQLite` | Built-in | Local persistent parcel geometry storage |
| `requests` |  | HTTP client for BhuNaksha and Bihar GIS proxying |
| `shapely` | 2.x | Computational geometry  polygon splitting, intersection, buffering |
| `pyproj` | 3.x | Coordinate reference system transformations |
| `numpy` |  | Numerical operations (used in report rendering) |
| `opencv-python` (cv2) |  | Map image rendering for PDF reports |
| `fpdf2` |  | PDF report generation |
| `pdfplumber` |  | Text extraction from BhuNaksha official plot PDFs |
| `beautifulsoup4` |  | HTML parsing of BhuNaksha ScalarDatahandler responses |
| `urllib3` |  | SSL warning suppression for self-signed government certs |
| `python-dotenv` |  | Optional `.env` file loading for Gemini API key config |

### Frontend
| Library | Version | Purpose |
|---------|---------|---------|
| `OpenLayers` | v6.15.1 | Map engine  tiles, vectors, interactions |
| `jQuery` |  | DOM manipulation, AJAX calls |
| `FontAwesome` | 4.7.0 | UI icons |

### External Services
| Service | URL | Purpose |
|---------|-----|---------|
| BhuNaksha GIS | `https://bhunaksha.bihar.gov.in` | Cadastral WMS tiles, plot geometry, metadata |
| Bihar Govt GIS (ArcGIS) | `https://gisserver.bihar.gov.in/arcgis` | Roads, rivers, administrative boundaries |
| ArcGIS Online | `services.arcgisonline.com` | Satellite and street basemap tiles |
| OpenStreetMap | `tile.openstreetmap.org` | Detailed road basemap |
| Google Gemini API | `https://generativelanguage.googleapis.com` | AI Kurra strategy ranking (requires GEMINI_API_KEY) |

---

## 3. Database Schema

The SQLite database (`instance/bhunaksha.db`) is managed by SQLAlchemy and auto-created on first run.

### `parcels` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment primary key |
| `plot_id` | VARCHAR(100) | Alphanumeric internal BhuNaksha ID |
| `plot_no` | VARCHAR(50) | Human-readable plot number (e.g., `1587`) |
| `khata_no` | VARCHAR(50) | Khata (register) number |
| `pniu` | VARCHAR(50) | 14-digit Parcel/Plot Identification Unit code |
| `area` | FLOAT | Area in square meters (from geometry) |
| `perimeter` | FLOAT | Perimeter in meters (from geometry) |
| `lat` | FLOAT | Centroid latitude (WGS84) |
| `lon` | FLOAT | Centroid longitude (WGS84) |
| `district` | VARCHAR(100) | Administrative district code |
| `subdivision` | VARCHAR(100) | Sub-division code |
| `circle` | VARCHAR(100) | Circle/Tehsil code |
| `mouza` | VARCHAR(100) | Mouza (village) code |
| `survey` | VARCHAR(50) | Survey type code |
| `mapinst` | VARCHAR(50) | Map instance code |
| `sheet_no` | VARCHAR(50) | Sheet number code |
| `owner_names` | TEXT | JSON-serialized list of owner names (Hindi text) |

### `parcel_vertices` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `parcel_id` | FK  parcels.id | Parent parcel reference |
| `x` | FLOAT | Native UTM Easting (EPSG:32645) |
| `y` | FLOAT | Native UTM Northing (EPSG:32645) |
| `lon` | FLOAT | WGS84 Longitude (EPSG:4326) |
| `lat` | FLOAT | WGS84 Latitude (EPSG:4326) |
| `sequence_order` | INTEGER | Vertex order in polygon ring |

### `boundary_segments` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `parcel_id` | FK  parcels.id | Parent parcel reference |
| `start_vertex_index` | INTEGER | Sequence index of start vertex |
| `end_vertex_index` | INTEGER | Sequence index of end vertex |
| `length_meters` | FLOAT | Euclidean distance between vertices |
| `bearing` | FLOAT | Compass bearing 0360 (North = 0) |

### `ldm_reports` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `parcel_id` | FK  parcels.id | Parent parcel reference |
| `report_url` | VARCHAR(255) | Local static file URL (e.g., `/static/reports/xxx.pdf`) |
| `filename` | VARCHAR(255) | Report filename |
| `created_at` | DATETIME | UTC timestamp of creation |

---

## 4. Backend Modules

### 4.1 `app.py`  Flask Application Gateway

The central module (1714 lines). Manages all HTTP routes, session lifecycle, caching, and orchestrates the other modules.

#### Session Management

```
init_session()
 Checks 60-second cooldown to prevent spam
 Attempts to load existing session cookies from disk (session_cookies.json)
 If no valid cookies: hits BhuNaksha index.jsp  indexmain.jsp to establish cookies
 Saves fresh cookies to disk for reuse across restarts
```

The `resilient_request()` wrapper adds:
- **Rate limiting**: Min 150ms between BhuNaksha requests
- **Automatic session recovery**: Detects HTML-redirect responses (sign of expired session) and re-initializes
- **Exponential backoff**: On HTTP 500/502/503/504 or connection errors, waits `(1s  2^attempt) + jitter` before retry (up to 3 retries)

#### Route Classification

**Proxy Routes** (forward to BhuNaksha with caching):
- `POST /proxy/Levels/ListsAfterLevel`  Dropdown hierarchy
- `POST /proxy/MapInfo/getVVVVExtentGeoref`  Sheet bounding boxes
- `POST /proxy/MapInfo/getPlotAtXY`  Plot at UTM coordinate
- `POST /proxy/MapInfo/getGisCode`  GIS code resolution
- `POST /proxy/MapInfo/getPointsfromPNIU`  Plot from PNIU
- `GET /proxy/WMS`  WMS tile proxy (disk PNG cache)

**Smart Proxy Routes** (with local DB fallback):
- `POST /proxy/MapInfo/getPlotAtGPS`  Converts GPSUTM, checks local DB first
- `POST /proxy/MapInfo/getPlotDetailsAndInspection`  Full parcel fetch, stores to DB

**API Routes** (pure backend logic):
- `GET /api/parcel/<n>/nearby`  ArcGIS road/river query
- `POST /api/parcel/<n>/subdivide`  AI Kurra division
- `POST /api/parcel/<n>/generate_report`  PDF generation
- `POST /api/sheet/scrape_batch`  Grid-based sheet scraping
- `GET /api/sheet/export_geojson`  Sheet-level GeoJSON export

**Export Routes**:
- `GET /proxy/Export/GeoJSON/<n>`  Single parcel GeoJSON
- `GET /proxy/Export/CSV/<n>`  Single parcel CSV with vertices

---

### 4.2 `subdivide.py`  Polygon Splitting Engine

Implements **binary searchbased polygon splitting** using the Shapely computational geometry library.

#### Key Functions

##### `split_polygon_by_ratio(poly, target_ratio, angle_deg)`

Splits a polygon into two parts such that `part1.area / total_area  target_ratio`.

**Algorithm:**
1. Translate the polygon to the origin (avoids large coordinate arithmetic errors)
2. Compute the polygon's bounding box
3. Create a base cut line at `angle_deg` passing through the centroid
4. Project all polygon vertices onto the **perpendicular (sweep) axis**
5. Run **50-iteration binary search** sweeping the cut line from `min_projection` to `max_projection`
6. At each step, split the polygon with `shapely.ops.split`, measure `part1.area`
7. Converge on the position where `part1.area  target_area` within 0.01% error
8. Translate result polygons back to original coordinates

**Edge cases handled:**
- `split()` raises an exception  `continue` (robustness against degenerate geometry)
- Only one polygon produced (line outside bounds)  shift search direction
- Multiple pieces from complex polygons  union smaller pieces

##### `split_parcel(poly, shares, frontage_coords=None)`

Iteratively splits a polygon into N parts by calling `split_polygon_by_ratio` in sequence. Each iteration splits the **remaining polygon** using the appropriate normalized sub-share ratio.

##### `generate_strategies(poly, shares, frontage_coords, river_frontage_coords)`

Generates all mathematically valid split strategies:

| Strategy | Cut Line Direction | Condition |
|----------|--------------------|-----------|
| Compact Cut | Parallel to shortest side of minimum rotated rectangle | Always generated |
| Longitudinal Cut | Parallel to longest side | Always generated |
| Road Access Cut | Perpendicular to road frontage line | Only when road geometry available |
| River Access Cut | Perpendicular to river frontage line | Only when river geometry available |

Strategies with angles within 5 (modulo 180) of each other are **deduplicated** to avoid presenting redundant options to the LLM.

---

### 4.3 `llm_expert.py`  AI Strategy Consultant

Interfaces with a local LLM (or any OpenAI-compatible API endpoint) to rank and explain Kurra subdivision strategies.

#### Configuration
| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `LLM_API_URL` | `http://localhost:1234/v1/chat/completions` | OpenAI-compatible completions endpoint |
| `LLM_MODEL_NAME` | `local-model` | Model name to pass in the request |

#### `consult_llm_for_division(parcel_info, strategies)`

**Input:**
```json
{
  "area_sqm": 1200.5,
  "shares": [50, 50],
  "has_frontage": true,
  "primary_road": "Village Road",
  "nearby_roads": [{"name": "...", "category": "NHRoads", "distance_meters": 0.0}],
  "nearby_river": false,
  "primary_river": "None",
  "features": [{"type": "tree", "x": 85.123, "y": 25.456}],
  "parcel_info": {"district": "Patna", "plot_no": "1587", ...},
  "user_preferences": "East plot should go to eldest son"
}
```

**System Prompt Encodes:**
- Rule 109(f): Road access proportionality
- Rule 109(b): Compactness
- Section 116(2): Trees/Wells
- Rule 109(g): Mutual consent precedence
- Road vs River priority hierarchy

**User Prompt Includes:**
- All parcel metadata
- For each strategy  sub-plot: area (sqm), perimeter (m), road frontage (m), river frontage (m), feature count
- User's custom instructions

**LLM Parameters:**
```json
{"temperature": 0.3, "max_tokens": 2000}
```

**Response Parsing:**
1. Tries to extract JSON from markdown code block (` ```json ... ``` `)
2. Falls back to finding first `{` and last `}` in raw text
3. Falls back to regex extraction of `recommended_strategy_index` and `explanation` fields
4. On any failure: returns `{"success": false, "error": "..."}`

**Output:**
```json
{
  "success": true,
  "recommended_index": 2,  // 0-indexed
  "explanation": "Strategy 3 (Road Access Cut) is recommended because..."
}
```

---

### 4.4 `gis_querier.py`  Bihar GIS ArcGIS REST Client

Queries Bihar Government's **ArcGIS REST MapServer** endpoints to retrieve real vector data for roads and rivers near a parcel.

#### Road Services (Priority-Ordered)
| Service Key | ArcGIS Layer | Priority |
|-------------|-------------|----------|
| `NHRoads` | `/RCD_DEPT/NHRoads/MapServer/0` | 1 (highest) |
| `SHRoads` | `/RCD_DEPT/SHRoads/MapServer/0` | 2 |
| `MDR` | `/RCD_DEPT/MDR/MapServer/0` | 3 |
| `ROAD_BIHAR` | `/RCD_DEPT/ROAD_BIHAR/MapServer/0` | 4 |
| `Village_Road` | `/RWD/Village_Road/MapServer/0` | 5 (lowest) |

#### Water Services
| Service Key | ArcGIS Layer |
|-------------|-------------|
| `Rivers` | `/WATER_IRRIGATION/Rivers/MapServer/0` |
| `Streams` | `/WATER_IRRIGATION/IrrigationStreams/MapServer/0` |

#### `get_nearby_vector_features(parcel_polygon_gps)`

1. Computes buffered bounding box: parcel bounds + 50m (0.0005)
2. Queries each service with `esriGeometryEnvelope` spatial filter
3. Builds Shapely `LineString` geometries from returned `paths` arrays
4. Computes approximate distance in meters: `dist_degrees  111000`
5. **Road sorting**: touching roads first (`distance_m  5m`), then by priority, then by distance
6. **River sorting**: strictly by distance

Returns `(roads_list, rivers_list)` where each item contains geometry, name, category, priority, and distance.

---

### 4.5 `report_generator.py`  PDF Kurra Report

Generates a multi-page PDF Kurra (land division) report using **FPDF2** and **OpenCV**.

#### Report Structure

**Page 1:**
- Header: "Kurra Division Report: Plot XXXX"
- Section 1: Land Details & LPM Details (District, Sub-Division, Circle, Mouza, Khata No., Plot No., Area, GPS coordinates)
- Section 2: Dashboard Details (number of partitions, share percentages, rendered map image with legend)

**Page 2:**
- Section 3: Segregation Details & Explainable AI Summary
  - Per sub-plot: area (acres + sqm), perimeter, road frontage extent, features inside
  - AI Strategy Recommendation block: strategy name + full LLM explanation paragraph
  - Disclaimer note about the AI generation

#### Map Rendering (`generate_kurra_report`)

1. Creates a white 800600 pixel canvas with NumPy/OpenCV
2. Computes a `deg2pix(lat, lon)` mapping function based on parcel bounds with 0.0005 buffer
3. Draws filled, semi-transparent colored polygons for each sub-division (30% opacity overlay)
4. Draws subdivision outlines with distinct colors
5. Draws the original parcel boundary in **red** (3px)
6. Draws road frontage in **yellow** (4px) if provided
7. Draws tree/well features as colored dots (green = tree, blue = well)
8. Saves to temp JPEG  embeds in PDF  deletes temp file

### 4.6 `pdf_parser.py`  Official Area Extraction

Extracts the official registered land area (in hectares) from BhuNaksha-generated plot PDFs.

```python
def extract_area_from_pdf_bytes(pdf_bytes) -> float | None
```

Uses `pdfplumber` to extract all text from the PDF, then searches for decimal numbers with 24 decimal places using regex `\b\d+\.\d{2,4}\b`. Returns the **last** such number found (which corresponds to the summary area field in BhuNaksha report format).

---

## 5. Caching Architecture

The application uses a **multi-layer cache** to maximize offline resilience and minimize government server load.

```
Request
  
  
Layer 1: In-Memory Python Dict (fastest)
    hits: JSON file cache already loaded into RAM
  
  
Layer 2: JSON File Cache (fast, persistent)
    Files: dropdown_cache.json, extent_cache.json, giscode_cache.json,
           pniu_cache.json, plot_at_xy_cache.json
    Cache keys: deterministic string from request parameters
  
  
Layer 3: SQLite Database (persistent, queryable)
    Tables: parcels, parcel_vertices, boundary_segments, ldm_reports
    Used for: full parcel geometry, ownership, spatial queries
  
  
Layer 4: Disk PNG Cache (persistent image tiles)
    Directory: static/wms_cache/
    Key: MD5 hash of sorted WMS parameters
    Used for: BhuNaksha cadastral WMS tiles
  
  
Layer 5: BhuNaksha / Bihar GIS Remote Server (slowest)
         Only reached on cache miss
```

### Cache Invalidation

There is **no automatic cache invalidation**. Caches are append-only. To refresh stale data:
- Delete the relevant JSON cache file
- Delete the parcel from SQLite (`DELETE FROM parcels WHERE plot_no = '...'`)
- Delete cached PNG tiles from `static/wms_cache/`

---

## 6. Session & Authentication Management

BhuNaksha uses server-side sessions tracked by HTTP cookies. The backend maintains a single persistent `requests.Session` object.

### Cookie Lifecycle

```
Application Start
   load_cookies()  Try to load session_cookies.json
        Success  Use existing session (no network request needed)
        Failure  init_session(force=False)
                       GET /10/index.jsp  (establishes JSESSIONID)
                       POST /10/indexmain.jsp (activates session)
                       save_cookies()  Write to session_cookies.json

During Requests (resilient_request)
   Detects HTML response or 401/403
        init_session(force=True)  Re-authenticates immediately
            Retry request with fresh session
```

### Rate Limiting

A global `enforce_rate_limit()` function ensures at least **150ms** between consecutive BhuNaksha requests using a `last_request_time` global. This prevents triggering government server rate limits or IP bans.

---

## 7. Coordinate System Handling

The application handles three coordinate representations:

| System | EPSG | Used For |
|--------|------|---------|
| WGS84 Geographic | 4326 | GPS, OpenLayers display, Bihar GIS queries |
| UTM Zone 45N | 32645 | BhuNaksha native coordinates, Shapely area/perimeter calculations |
| BhuNaksha Local | Internal | Some BhuNaksha endpoints use a local projection |

### Conversion Strategy

**Primary (when `pyproj` available):**
```python
transformer = pyproj.Transformer.from_crs("EPSG:32645", "EPSG:4326", always_xy=True)
lon, lat = transformer.transform(x_utm, y_utm)
```

**Fallback (linear interpolation using extent bbox):**
```python
pct_x = (x - u_xmin) / (u_xmax - u_xmin)
pct_y = (y - u_ymin) / (u_ymax - u_ymin)
lon = g_xmin + pct_x * (g_xmax - g_xmin)
lat = g_ymin + pct_y * (g_ymax - g_ymin)
```

The extent bounding boxes (GPS extent and UTM extent for the same village sheet) are fetched from `getVVVVExtentGeoref` and cached persistently.

---

## 8. API Contract (Full Reference)

### `POST /proxy/MapInfo/getPlotDetailsAndInspection`

**Request Form Data:**
```
state=10
giscode=<gis_code>
plot_no=<plot_number>
levels=<dist,subdiv,circle,mouza,survey,mapinst,sheet>
```

**Response (Success  from cache):**
```json
{
  "success": true,
  "cached": true,
  "parcel": {
    "id": 42,
    "plot_id": "ABCD123",
    "plot_no": "1587",
    "khata_no": "123",
    "pniu": "10234500012345",
    "area": 1200.5,
    "area_acres": 0.2966,
    "official_area_ha": 0.12,
    "perimeter": 148.3,
    "lat": 25.5612,
    "lon": 85.1234,
    "district": "01",
    "subdivision": "02",
    "circle": "03",
    "mouza": "04",
    "owner_names": [" ", " "],
    "num_vertices": 8
  },
  "vertices": [
    {"x": 281234.5, "y": 2824567.3, "lon": 85.1234, "lat": 25.5612, "sequence_order": 0},
    ...
  ],
  "segments": [
    {"start_vertex": 0, "end_vertex": 1, "length_meters": 23.4, "bearing": 92.3},
    ...
  ],
  "report": {"url": "/static/reports/GCODE_1587.pdf"}
}
```

---

### `POST /api/parcel/<plot_no>/subdivide`

**Request JSON:**
```json
{
  "shares": [50, 50],
  "features": [
    {"type": "tree", "x": 85.1235, "y": 25.5613}
  ],
  "parcel_info": {
    "district": "Patna",
    "circle": "Phulwarisharif",
    "mouza": "Naubatpur",
    "plot_no": "1587",
    "khata_no": "123"
  },
  "user_preferences": "East plot should go to eldest son"
}
```
Query param: `?parcel_id=42`

**Response (Success):**
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "sub_plot_id": 1,
        "share_percentage": 50,
        "area_sqm": 600.2,
        "perimeter_m": 98.7,
        "frontage_m": 12.3,
        "contained_features": [{"type": "tree", "x": 85.1235, "y": 25.5613}]
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[85.123, 25.561], ...]]
      }
    },
    ...
  ],
  "llm_explanation": "Strategy 3 (Road Access Cut) is recommended because...",
  "llm_failed": false,
  "strategy_name": "Road Access Cut (Perpendicular to Road)"
}
```

---

### `POST /api/sheet/scrape_batch`

**Request JSON:**
```json
{
  "state": "10",
  "giscode": "ABCD1234",
  "levels": "01,02,03,04,05,06,07",
  "batch_index": 0,
  "batch_size": 100,
  "grid_step": 35.0
}
```

**Algorithm:**
1. Fetch UTM extent for the sheet from cache
2. Generate a regular grid of UTM points with `grid_step` meter spacing
3. Skip points that fall inside already-known parcel polygons (from DB)
4. For remaining grid points: call `getPlotAtXY` to identify which plot each point is in
5. For newly discovered plots: invoke `getPlotDetailsAndInspection` to fetch and store geometry
6. Add new polygons to the in-memory `known_polygons` set for this batch run

**Response:**
```json
{
  "success": true,
  "batch_index": 0,
  "total_points": 1247,
  "scanned_points_in_batch": 100,
  "points_skipped_in_batch": 43,
  "points_queried_in_batch": 57,
  "new_plots_found": ["1234", "1235", "1588"],
  "total_plots_saved": 47,
  "is_done": false
}
```

---

## 9. Frontend Architecture

### State Management (`app.js`)

Key global state variables:

| Variable | Type | Description |
|----------|------|-------------|
| `currentParcel` | Object | Currently selected parcel data (from API response) |
| `osmFeaturesCache` | Object | Cached road/river GeoJSON from Bihar ArcGIS per parcel |
| `kurraFeatures` | Array | User-placed tree/well features [{type, x, y}] |
| `kurraSubdivisions` | Array | Last generated Kurra sub-polygon GeoJSON features |
| `kurraFrontage` | Array | Road frontage coordinates from last subdivision |

### Map Layers (OpenLayers)

| Layer | Type | Source | Toggle |
|-------|------|--------|--------|
| Satellite Basemap | XYZ Tile | ArcGIS World Imagery | Basemap radio |
| Street Map | XYZ Tile | ArcGIS World Street Map | Basemap radio |
| Cadastral WMS | Image WMS | Flask `/proxy/WMS` | Always on (when sheet selected) |
| Bihar GIS  Roads | ArcGIS REST | `gisserver.bihar.gov.in` (6 layers) | Checkbox per road type |
| Bihar GIS  Rivers | ArcGIS REST | `gisserver.bihar.gov.in` (Rivers) | Checkbox |
| Bihar GIS  Admin | ArcGIS REST | Panchayat, Village boundaries | Checkbox |
| Bihar GIS  Assets | ArcGIS REST | Bungalows, Offices, Worship, Police | Checkbox per type |
| Parcel Boundary | Vector | Generated from API vertex list | Auto-shown on click |
| Kurra Sub-Plots | Vector | Generated from `/subdivide` response | Auto-shown on division |
| Tree/Well Features | Vector | User-placed via map click | Kurra mode only |



## 10. Kurra Division  End-to-End Flow

```
User Sets Shares + Features + Instructions
              
              
[Frontend: app.js]
  Collects: parcel_id, shares[], features[], parcel_info, user_preferences
  POST /api/parcel/<plot_no>/subdivide
              
              
[Backend: app.py::subdivide_parcel()]
  1. Load parcel vertices from SQLite DB
  2. Build Shapely Polygon in UTM coordinates (accurate area)
  3. Build GPS polygon for distance checks
              
              
  4. Query Bihar ArcGIS [gis_querier.get_nearby_vector_features()]
      roads (sorted by priority + distance)
      rivers (sorted by distance)
              
              
  5. Identify primary road (road[0])  extract UTM frontage coords
     Identify primary river (river[0])  extract UTM river coords
              
              
[subdivide.generate_strategies(poly, shares, frontage_utm, river_utm)]
   Compact Cut (always)
   Longitudinal Cut (always)
   Road Access Cut (if roads found)
   River Access Cut (if rivers found)
   Deduplicate by angle similarity
              
              
  6. Pre-compute sub-plot stats for each strategy:
     - Road frontage intersection length per sub-plot (5m buffer)
     - River frontage intersection length per sub-plot
     - Feature (tree/well) containment per sub-plot
              
              
[llm_expert.consult_llm_for_division(payload, strategies)]
   Build system prompt (UP Revenue Code rules)
   Build user prompt (parcel + strategies + stats + preferences)
   POST to LLM API
   Parse JSON response
   Return recommended_index + explanation
              
              
  7. Apply best strategy sub-polygons
  8. Convert sub-polygon UTM coords  GPS coords
  9. Compute feature containment for response
  10. Return GeoJSON FeatureCollection + explanation

[Frontend]
   Render colored sub-plot polygons on map
   Display LLM explanation panel
   Enable "Download PDF Report" button
```

---

## 11. Bihar GIS External Services

All Bihar Government GIS services are accessed via **HTTPS with SSL verification disabled** (self-signed certificates). A read-only `GET` query is made to each service's `query` endpoint with these standard parameters:

```
geometryType=esriGeometryEnvelope
spatialRel=esriSpatialRelIntersects
inSR=4326
outSR=4326
f=json
outFields=*
returnGeometry=true
```

### GIS Layer Inventory (Frontend Toggle Checkboxes)

| Checkbox | ArcGIS Service Path |
|----------|-------------------|
| National Highways | `/RCD_DEPT/NHRoads/MapServer/0` |
| State Highways & MDR | `/RCD_DEPT/SHRoads/MapServer/0`, `/RCD_DEPT/MDR/MapServer/0` |
| Village & Local Roads | `/RWD/Village_Road/MapServer/0` |
| Bihar Road Network (Full) | `/RCD_DEPT/ROAD_BIHAR/MapServer/0` |
| Panchayat Boundaries | `/ADMINISTRATIVE_BOUNDARIES/Panchayat/MapServer/0` |
| Village Boundaries | `/ADMINISTRATIVE_BOUNDARIES/Village/MapServer/0` |
| Rivers & Canals | `/WATER_IRRIGATION/Rivers/MapServer/0` |
| Inspection Bungalows | `/PWD_DEPT/InspectionBungalows/MapServer/0` |
| Gov Offices & Residences | `/PWD_DEPT/GovtOffices/MapServer/0` |
| Places of Worship | `/SOCIAL_AMENITIES/PlacesOfWorship/MapServer/0` |
| Police Stations | `/HOME_DEPT/PoliceStations/MapServer/0` |

---

## 12. Known Issues & Edge Cases

### Geometry Issues
- **MultiPolygon Parcels**: `getPlotInfo` occasionally returns `MULTIPOLYGON` WKT. The code extracts the largest polygon by area, discarding smaller satellite polygons.
- **Invalid Polygons**: After splitting, sub-polygons are repaired with `poly.buffer(0)`  a Shapely convention for fixing self-intersections.
- **Degenerate Splits**: If binary search cannot converge (all 50 iterations exhaust without a valid split), the original polygon is returned as a single item. The frontend should handle `features.length < shares.length`.

### Coordinate Issues
- **UTM Centroid in DB**: Some older cached parcels may have centroid coordinates stored in raw UTM space. The code detects this with `abs(lat) > 180` and runs the linear interpolation fallback.
- **Projection Zone Boundary**: Bihar mostly lies in UTM Zone 45N. Parcels near zone boundaries may have small inaccuracies in the linear interpolation fallback.

### LLM Issues
- **Truncated JSON**: The LLM occasionally returns incomplete JSON if `max_tokens` is reached. The parser has three fallback layers (markdown extraction  bare JSON extraction  regex field extraction).
- **Strategy Index Out of Range**: If the LLM returns an index  N strategies (hallucination), it is silently clamped to index 0.
- **45-Second Timeout**: LLM calls timeout after 45 seconds. On timeout, `llm_failed = true` is set and the first strategy is used.

### Network Issues
- **BhuNaksha Server Downtime**: The Bihar government server is frequently unavailable. All proxy routes return `502` with a descriptive error message when this happens. The frontend shows an appropriate offline indicator.
- **Bihar ArcGIS Services**: GIS service queries have an 8-second timeout. If they fail, the subdivision proceeds with empty road/river context and the LLM is informed that no infrastructure data is available.

### Session Issues
- **Cookie Expiry**: BhuNaksha sessions typically expire within minutes of inactivity. The `resilient_request` function handles automatic re-authentication.
- **60-Second Cooldown**: `init_session()` has a 60-second cooldown to prevent hammering the government server during bulk operations. `force=True` bypasses this.
