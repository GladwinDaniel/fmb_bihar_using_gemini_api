# =============================================================================
# BHUOVERLAY  Bihar Cadastral Map & Satellite Dashboard
# Backend Flask API Server (app.py) -- Gemini API Edition
# =============================================================================
#
# ROLE: This file is the central backend API gateway. It:
#   1. Acts as a PROXY to the Bihar Government's BhuNaksha GIS server
#      (government servers block direct browser cross-origin calls  CORS)
#   2. Manages persistent HTTP sessions with BhuNaksha (cookie-based auth)
#   3. Caches all responses on disk and in SQLite to work offline
#   4. Provides REST API endpoints for the frontend (see ENDPOINT SUMMARY below)
#
# HOW TO RUN:
#   cd backend && python app.py
#   Server starts at http://127.0.0.1:5001
#
# ENDPOINT SUMMARY:
#   POST /proxy/Levels/ListsAfterLevel         Cascading dropdown data
#   POST /proxy/MapInfo/getVVVVExtentGeoref    Sheet bounding box (GPS/UTM)
#   POST /proxy/MapInfo/getPlotAtXY            Plot No. from UTM coordinate
#   POST /proxy/MapInfo/getPlotAtGPS           Plot No. from GPS coordinate (GPSUTM)
#   POST /proxy/MapInfo/getPointsfromPNIU      Plot from 14-digit PNIU code
#   POST /proxy/MapInfo/getGisCode             GIS code for admin. levels
#   GET  /proxy/WMS                            WMS map tile proxy (cached PNG)
#   POST /proxy/MapInfo/getPlotDetailsAndInspection  Full parcel data + DB persist
#   GET  /proxy/Export/GeoJSON/<plot_no>       Export parcel as GeoJSON
#   GET  /proxy/Export/CSV/<plot_no>           Export parcel as CSV
#   POST /api/parcel/<plot_no>/subdivide       AI Kurra land division (Google Gemini API)
#   POST /api/parcel/<plot_no>/generate_report  Generate PDF Kurra report
#   POST /api/sheet/scrape_batch               Batch-scrape all plots in a sheet
#   GET  /api/sheet/export_geojson             Export all cached plots in a sheet
#
# DEPENDENCIES (see requirements.txt):
#   flask, flask-cors, flask-sqlalchemy  Web framework and ORM
#   requests, urllib3                    HTTP client
#   beautifulsoup4                       HTML parsing for BhuNaksha responses
#   shapely, pyproj, numpy               Geospatial computations
#   fpdf2, opencv-python                 PDF report generation
#   pdfplumber                           PDF area extraction
#
# CONNECTED TO (see CONNECTION_GUIDE.md for full API contract):
#   Frontend:            frontend/app.js (jQuery AJAX)
#   BhuNaksha Server:    https://bhunaksha.bihar.gov.in
#   Bihar ArcGIS Server: https://gisserver.bihar.gov.in
#   Google Gemini API:   https://generativelanguage.googleapis.com (GEMINI_API_KEY required)
# =============================================================================

import sys
import os
import json
import math
import requests
import urllib3
import shapely.wkt
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, Response, jsonify
from shapely.geometry import Polygon, Point
from models import db, Parcel, ParcelVertex, BoundarySegment, LdmReport
import subdivide

# Disable insecure request warnings for self-signed certificates
# Bihar government servers use self-signed TLS certificates  this suppresses
# the InsecureRequestWarning that would otherwise flood the console.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from flask_cors import CORS

# --- Flask App Initialization ---
# CORS(app) allows the browser frontend (file://, localhost:8080) to call
# this API running on a different port (5001) without CORS errors.
# The SQLite database is stored at backend/instance/bhunaksha.db and is
# automatically created on first run (db.create_all() below).
app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bhunaksha.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# PROCESS: Create all SQLite tables on startup if they don't exist yet.
# Tables created: parcels, parcel_vertices, boundary_segments, ldm_reports
# Schema defined in models.py
with app.app_context():
    db.create_all()

BHUNAKSHA_URL = "https://bhunaksha.bihar.gov.in"

# --- Session Management ---
# All BhuNaksha API calls must be made with a valid server-side session.
# BhuNaksha uses cookie-based authentication (JSESSIONID). The session object
# persists cookies across all requests made through this backend.
# Session cookies are saved to disk (session_cookies.json) so they survive
# backend restarts without needing to re-initialize.
session = requests.Session()
session.verify = False  # Disable SSL verification for self-signed government certs

# =============================================================================
# CACHING LAYER
# =============================================================================
# WHY: BhuNaksha is a government server that can be slow or offline. All API
# responses are cached to JSON files on disk. Cached responses are served
# instantly on subsequent requests, making the app work fully offline after
# the first data load.
#
# CACHE FILE LOCATIONS (inside backend/ directory):
#   dropdown_cache.json      Administrative hierarchy (DistrictsSheets) ~1.2MB
#   extent_cache.json        Sheet bounding boxes (GPS & UTM extents)
#   giscode_cache.json       GIS code for each admin level combination
#   pniu_cache.json          PNIU-to-plot-number lookups
#   plot_at_xy_cache.json    UTM coordinateplot number lookups
#   static/wms_cache/*.png   WMS tile PNGs (MD5-keyed filenames)
#   instance/bhunaksha.db    SQLite: full parcel geometries + metadata
# =============================================================================
DROPDOWN_CACHE_FILE = "dropdown_cache.json"
EXTENT_CACHE_FILE = "extent_cache.json"

def load_json_cache(filename):
    """Load a JSON cache file from disk. Returns empty dict if file not found."""
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading cache file {filename}: {e}")
    return {}

def save_json_cache(filename, data):
    """Persist a dict to a JSON cache file on disk."""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving cache file {filename}: {e}")

# Load all JSON caches into memory at startup for O(1) in-memory lookup
lists_after_level_cache = load_json_cache(DROPDOWN_CACHE_FILE)
vvvv_extent_cache = load_json_cache(EXTENT_CACHE_FILE)

GISCODE_CACHE_FILE = "giscode_cache.json"
PNIU_CACHE_FILE = "pniu_cache.json"
PLOT_AT_XY_CACHE_FILE = "plot_at_xy_cache.json"

giscode_cache = load_json_cache(GISCODE_CACHE_FILE)
pniu_cache = load_json_cache(PNIU_CACHE_FILE)
plot_at_xy_cache = load_json_cache(PLOT_AT_XY_CACHE_FILE)

# =============================================================================
# SESSION INITIALIZATION & RESILIENCE SYSTEM
# =============================================================================
# HOW BhuNaksha authentication works:
#   1. We GET bhunaksha.bihar.gov.in/10/index.jsp to establish a JSESSIONID cookie
#   2. We POST to indexmain.jsp to mark our session as active for Bihar (state=10)
#   3. All subsequent API calls carry these cookies in the session object
#   4. If a response comes back as HTML (instead of JSON), the session has expired
#      and must be re-initialized (init_session(force=True))
#   5. Cookies are saved to disk so the backend can restart without re-authing
#
# OFFLINE RESILIENCE:
#   - server_offline_until: tracks when BhuNaksha last went down and suppresses
#     retry attempts for 30 seconds (SERVER_OFFLINE_COOLDOWN)
#   - enforce_rate_limit(): enforces 150ms minimum between BhuNaksha requests
#     to avoid triggering server-side rate limiting
#   - resilient_request(): wraps all BhuNaksha calls with exponential backoff retry
# =============================================================================

# Disk-based cookie persistence for BhuNaksha sessions
COOKIE_FILE = "session_cookies.json"

def save_cookies(sess):
    try:
        with open(COOKIE_FILE, "w") as f:
            json.dump(requests.utils.dict_from_cookiejar(sess.cookies), f)
        print("Session cookies saved to disk.")
    except Exception as e:
        print("Error saving cookies:", e)

def load_cookies(sess):
    try:
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)
                sess.cookies.update(requests.utils.cookiejar_from_dict(cookies))
                print("Session cookies loaded from disk.")
                return True
    except Exception as e:
        print("Error loading cookies:", e)
    return False

# Initialize session with cooldown
last_session_init_time = 0.0
last_force_init_time = 0.0   # Separate cooldown even for forced re-inits
session_init_success = False
server_offline_until = 0.0   # Timestamp until which we know the server is unreachable

SERVER_OFFLINE_COOLDOWN = 30.0  # Don't retry a known-down server for 30 seconds
FORCE_INIT_COOLDOWN = 30.0      # Minimum gap between forced session re-inits

def mark_server_offline():
    """Called when a connection to BhuNaksha times out or fails hard."""
    global server_offline_until
    import time
    server_offline_until = time.time() + SERVER_OFFLINE_COOLDOWN
    print(f"BhuNaksha server marked offline for {SERVER_OFFLINE_COOLDOWN:.0f}s.")

def is_server_offline():
    import time
    return time.time() < server_offline_until

def init_session(force=False):
    """Establishes the session cookies with BhuNaksha, using disk cache if available."""
    global session, last_session_init_time, last_force_init_time, session_init_success
    import time

    current_time = time.time()

    if force:
        # Even forced re-inits get a 30s cooldown to stop hammering a dead server
        if current_time - last_force_init_time < FORCE_INIT_COOLDOWN:
            print(f"Skipping forced session re-init: {FORCE_INIT_COOLDOWN:.0f}s cooldown active ({current_time - last_force_init_time:.1f}s ago).")
            return session_init_success
        last_force_init_time = current_time
    else:
        # Apply normal cooldown of 60 seconds
        if current_time - last_session_init_time < 60:
            print("Skipping session initialization due to cooldown.")
            return session_init_success

    last_session_init_time = current_time
    
    # Initialize fresh session object
    session = requests.Session()
    session.verify = False
    
    # Try to load existing cookies from disk first
    if not force and load_cookies(session):
        session_init_success = True
        return True
        
    try:
        print("Initializing new session cookies...")
        # Get landing page to establish cookies (using index.jsp for speed and reliability)
        url_jsp = f"{BHUNAKSHA_URL}/10/index.jsp"
        session.get(url_jsp, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }, timeout=8)
        
        # Load main page to make sure session state is fully active
        session.post(f"{BHUNAKSHA_URL}/10/indexmain.jsp", data={"state": "10"}, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": url_jsp
        }, timeout=8)
        
        print("Session cookies established:", session.cookies.get_dict())
        save_cookies(session)
        session_init_success = True
        return True
    except Exception as e:
        print("Error establishing session:", e)
        session_init_success = False
        # If it's a connectivity failure, mark server offline so we stop hammering it
        if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                          requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout)):
            mark_server_offline()
        return False

# Initialize on startup
init_session()

# Global rate limiting tracker (enforces polite intervals to prevent rate limits)
last_request_time = 0.0

def enforce_rate_limit():
    global last_request_time
    import time
    current_time = time.time()
    elapsed = current_time - last_request_time
    min_interval = 0.150  # 150ms delay between remote server hits
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    last_request_time = time.time()

def resilient_request(method, url, max_retries=3, **kwargs):
    """
    Performs requests with:
      - Rate limiting (spacing remote requests by at least 150ms)
      - Fast-fail when BhuNaksha is known offline (server_offline_until)
      - Session recovery on HTML/401/403 responses (with force cooldown)
      - Exponential backoff with random jitter on retries
    """
    import time
    import random

    is_bhunaksha = BHUNAKSHA_URL in url

    # Fast-fail immediately if we know the server is down  no point waiting 12s
    if is_bhunaksha and is_server_offline():
        remaining = server_offline_until - time.time()
        raise requests.exceptions.ConnectionError(
            f"BhuNaksha server known offline for another {remaining:.0f}s  skipping request."
        )

    # Enforce global rate limit to avoid trigger-happy server defenses
    if is_bhunaksha:
        enforce_rate_limit()

    base_delay = 1.0  # start with 1 second delay

    for attempt in range(1, max_retries + 1):
        try:
            # Set default timeout of 12 seconds if not provided
            if 'timeout' not in kwargs:
                kwargs['timeout'] = 12

            if method.upper() == 'POST':
                r = session.post(url, **kwargs)
            else:
                r = session.get(url, **kwargs)

            # Detect expired/redirected sessions (HTML instead of JSON)
            is_html_error = False
            if is_bhunaksha:
                content_type = r.headers.get("Content-Type", "")
                is_html_error = content_type.startswith("text/html") or r.text.strip().startswith("<")

            # If session is invalid (and we're not requesting WMS tiles)
            if is_bhunaksha and (r.status_code in [401, 403] or is_html_error) and "WMS" not in url:
                print(f"Auth/session error on {url} (HTTP {r.status_code}/HTML response). Attempting session re-init...")
                # init_session(force=True) has its own 30s cooldown  will skip if recently tried
                if init_session(force=True):
                    # Update referer header if present
                    if 'headers' in kwargs and 'Referer' in kwargs['headers']:
                        kwargs['headers']['Referer'] = f"{BHUNAKSHA_URL}/10/indexmain.jsp"
                    # Retry immediately with fresh session (don't count as a retry attempt)
                    if method.upper() == 'POST':
                        r = session.post(url, **kwargs)
                    else:
                        r = session.get(url, **kwargs)
                else:
                    # Session re-init failed (server offline)  stop immediately, don't retry
                    raise requests.exceptions.ConnectionError(
                        f"Session re-init failed for {url}  server unreachable."
                    )

            # If server has an internal error (500-504)  worth retrying
            if r.status_code in [500, 502, 503, 504]:
                raise requests.exceptions.HTTPError(f"HTTP {r.status_code}", response=r)

            return r

        except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout) as e:
            print(f"Request timed out ({url}): {e} (Attempt {attempt} of {max_retries})")
            mark_server_offline()
            raise e  # Don't retry timeouts  it means server is down, bail immediately

        except (requests.exceptions.RequestException, ConnectionError, Exception) as e:
            print(f"Request failed ({url}): {e} (Attempt {attempt} of {max_retries})")

            # Bail out immediately if we know the server is offline or session recovery failed
            err_str = str(e)
            if "known offline" in err_str or "re-init failed" in err_str or "recovery failed" in err_str:
                raise e

            if attempt == max_retries:
                raise e

            # Calculate exponential backoff delay with random jitter
            delay = (base_delay * 2 ** attempt) + random.uniform(0.1, 1.0)
            print(f"Sleeping for {delay:.2f}s before retrying...")
            time.sleep(delay)


def safe_post(url, data, headers, referer_path="/10/indexmain.jsp"):
    """Performs a POST request utilizing the resilient requester wrapper."""
    return resilient_request('POST', url, data=data, headers=headers)

def safe_get(url, params, headers):
    """Performs a GET request utilizing the resilient requester wrapper."""
    return resilient_request('GET', url, params=params, headers=headers)

def get_proxy_headers(referer_path="/10/indexmain.jsp", content_type=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{BHUNAKSHA_URL}{referer_path}",
        "X-Requested-With": "XMLHttpRequest"
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers

# =============================================================================
# API ROUTES
# =============================================================================

# --- ROUTE 1: Health Check ---
# Called by: browser/monitoring tools
# Returns a simple JSON status to confirm the backend is running.
@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Bihar Cadastral Map & Satellite Dashboard (Bhu-Overlay) API"
    })

# --- ROUTE 2: Administrative Dropdown Data ---
# Called by: frontend app.js initDropdowns() and fetchDropdown()
# PROCESS:
#   1. Receive {state, level, codes} from frontend dropdown cascade
#   2. Check in-memory cache (loaded from dropdown_cache.json)  serve immediately if found
#   3. If not cached: POST to BhuNaksha /rest/Levels/ListsAfterLevel
#   4. Parse JSON response, save to cache, return to frontend
# NOTE: The cache file (~1.2MB) is pre-populated and rarely needs server hits.
@app.route("/proxy/Levels/ListsAfterLevel", methods=["POST"])
def proxy_lists_after_level():
    data = request.form.to_dict()
    state = data.get("state", "10")
    level = data.get("level", "0")
    codes = data.get("codes", "")
    
    # Check cache
    cache_key = f"{state}_{level}_{codes}"
    if cache_key in lists_after_level_cache:
        # Serve immediately from cache
        return jsonify(lists_after_level_cache[cache_key])
        
    url = f"{BHUNAKSHA_URL}/rest/Levels/ListsAfterLevel"
    headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
    try:
        r = safe_post(url, data=data, headers=headers)
        if r.status_code == 200:
            try:
                json_data = r.json()
                lists_after_level_cache[cache_key] = json_data
                save_json_cache(DROPDOWN_CACHE_FILE, lists_after_level_cache)
                return jsonify(json_data)
            except:
                pass
        return Response(r.text, status=r.status_code, content_type=r.headers.get("Content-Type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ROUTE 3: Sheet Bounding Box ---
# Called by: frontend app.js loadVillageSheet()
# PROCESS:
#   1. Receive {state, gisLevels, srs}  srs="4326" for GPS, srs="0" for UTM
#   2. Check extent_cache.json  serve from cache if available
#   3. If not cached: POST to BhuNaksha /rest/MapInfo/getVVVVExtentGeoref
#   4. Return {gisCode, xmin, ymin, xmax, ymax}
# NOTE: Both GPS (4326) and UTM (0) extents are cached since both are needed
#       for coordinate conversions in getPlotAtGPS and getPlotDetailsAndInspection.
@app.route("/proxy/MapInfo/getVVVVExtentGeoref", methods=["POST"])
def proxy_extent():
    data = request.form.to_dict()
    state = data.get("state", "10")
    gis_levels = data.get("gisLevels", "")
    srs = data.get("srs", "4326")
    
    # Check cache
    cache_key = f"{state}_{gis_levels}_{srs}"
    if cache_key in vvvv_extent_cache:
        return jsonify(vvvv_extent_cache[cache_key])
        
    url = f"{BHUNAKSHA_URL}/rest/MapInfo/getVVVVExtentGeoref"
    headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
    try:
        r = safe_post(url, data=data, headers=headers)
        if r.status_code == 200:
            try:
                json_data = r.json()
                vvvv_extent_cache[cache_key] = json_data
                save_json_cache(EXTENT_CACHE_FILE, vvvv_extent_cache)
                return jsonify(json_data)
            except:
                pass
        return Response(r.text, status=r.status_code, content_type=r.headers.get("Content-Type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ROUTE 4: Plot Number from UTM Coordinate ---
# Called by: scrape_batch() internally during grid sampling
# PROCESS:
#   1. Receive {state, giscode, x (UTM Easting), y (UTM Northing)}
#   2. Check plot_at_xy_cache.json  serve from cache if found (rounded to 2 decimal places)
#   3. If not cached: POST to BhuNaksha /rest/MapInfo/getPlotAtXY
#   4. Return the plot number string (e.g., "1587")
@app.route("/proxy/MapInfo/getPlotAtXY", methods=["POST"])
def proxy_plot_at_xy():
    data = request.form.to_dict()
    state = data.get("state", "10")
    giscode = data.get("giscode", "")
    try:
        x_rounded = round(float(data.get("x", 0)), 2)
        y_rounded = round(float(data.get("y", 0)), 2)
    except (ValueError, TypeError):
        x_rounded = data.get("x", "")
        y_rounded = data.get("y", "")
        
    cache_key = f"{state}_{giscode}_{x_rounded}_{y_rounded}"
    if cache_key in plot_at_xy_cache:
        return jsonify(plot_at_xy_cache[cache_key])
        
    url = f"{BHUNAKSHA_URL}/rest/MapInfo/getPlotAtXY"
    headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
    try:
        r = safe_post(url, data=data, headers=headers)
        if r.status_code == 200:
            try:
                json_data = r.json()
                plot_at_xy_cache[cache_key] = json_data
                save_json_cache(PLOT_AT_XY_CACHE_FILE, plot_at_xy_cache)
                return jsonify(json_data)
            except:
                pass
        return Response(r.text, status=r.status_code, content_type=r.headers.get("Content-Type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ROUTE 5: Plot Number from GPS Coordinate (Click-to-Select) ---
# Called by: frontend app.js map.on("singleclick") handler
# PROCESS:
#   STEP 1  Check SQLite database: Does any cached parcel polygon contain this GPS point?
#              If yes: return plot_no immediately (works offline)
#   STEP 2  Fetch GPS extent (srs=4326) and UTM extent (srs=0) from cache or BhuNaksha
#   STEP 3  Convert GPS [lon, lat] to UTM [x, y] using pyproj (EPSG:4326  EPSG:32645)
#             Fallback: bounding box linear interpolation if pyproj fails
#   STEP 4  POST to BhuNaksha /rest/MapInfo/getPlotAtXY with UTM coordinates
#   STEP 5  Return {kide: "plot_no"} to frontend
# NOTE: BhuNaksha only understands UTM coordinates in its native coordinate system.
#       GPS  UTM conversion is the critical step that makes map clicks work.
@app.route("/proxy/MapInfo/getPlotAtGPS", methods=["POST"])
def get_plot_at_gps():
    """Converts input GPS coordinate [lon, lat] to local village UTM coordinates using 
       bounding box interpolation, then calls the getPlotAtXY REST endpoint."""
    data = request.form.to_dict()
    state = data.get("state", "10")
    giscode = data.get("giscode")
    levels = data.get("levels")
    
    try:
        lon = float(data.get("lon"))
        lat = float(data.get("lat"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinate inputs"}), 400
        
    # Check if the coordinate falls inside any cached parcel in the database
    if levels:
        levels_parts = [p.strip() for p in levels.split(",") if p.strip()]
        if len(levels_parts) >= 7:
            dist_code = levels_parts[0]
            subdiv_code = levels_parts[1]
            circle_code = levels_parts[2]
            mouza_code = levels_parts[3]
            survey_code = levels_parts[4]
            mapinst_code = levels_parts[5]
            sheet_code = levels_parts[6]
            
            try:
                db_parcels = Parcel.query.filter_by(
                    district=dist_code,
                    subdivision=subdiv_code,
                    circle=circle_code,
                    mouza=mouza_code,
                    survey=survey_code,
                    mapinst=mapinst_code,
                    sheet_no=sheet_code
                ).all()
                
                from shapely.geometry import Point, Polygon
                pt = Point(lon, lat)
                for p in db_parcels:
                    verts = sorted(p.vertices, key=lambda x: x.sequence_order)
                    if len(verts) >= 3:
                        poly = Polygon([(v.lon, v.lat) for v in verts])
                        if poly.contains(pt) or poly.distance(pt) < 1e-6:
                            # Found locally! Return it immediately to avoid hitting the offline server.
                            return jsonify({"kide": p.plot_no})
            except Exception as local_db_err:
                print("Local database spatial check error:", local_db_err)

    headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
    
    try:
        # Check cache for extent maps
        gps_cache_key = f"{state}_{levels}_4326"
        utm_cache_key = f"{state}_{levels}_0"
        
        # 1. Fetch GPS extent (srs: 4326)
        if gps_cache_key in vvvv_extent_cache:
            g_data = vvvv_extent_cache[gps_cache_key]
        else:
            r_gps = safe_post(f"{BHUNAKSHA_URL}/rest/MapInfo/getVVVVExtentGeoref", data={
                "state": state,
                "gisLevels": levels,
                "srs": "4326"
            }, headers=headers)
            g_data = r_gps.json() if r_gps.status_code == 200 else {}
            if g_data:
                vvvv_extent_cache[gps_cache_key] = g_data
                save_json_cache(EXTENT_CACHE_FILE, vvvv_extent_cache)
        
        # 2. Fetch UTM extent (srs: 0)
        if utm_cache_key in vvvv_extent_cache:
            u_data = vvvv_extent_cache[utm_cache_key]
        else:
            r_utm = safe_post(f"{BHUNAKSHA_URL}/rest/MapInfo/getVVVVExtentGeoref", data={
                "state": state,
                "gisLevels": levels,
                "srs": "0"
            }, headers=headers)
            u_data = r_utm.json() if r_utm.status_code == 200 else {}
            if u_data:
                vvvv_extent_cache[utm_cache_key] = u_data
                save_json_cache(EXTENT_CACHE_FILE, vvvv_extent_cache)
        
        if g_data.get("xmin") is not None and u_data.get("xmin") is not None:
            g_xmin, g_xmax = g_data["xmin"], g_data["xmax"]
            g_ymin, g_ymax = g_data["ymin"], g_data["ymax"]
            
            u_xmin, u_xmax = u_data["xmin"], u_data["xmax"]
            u_ymin, u_ymax = u_data["ymin"], u_data["ymax"]
            
            # Check for standard valid coordinate box sizes to avoid divide by zero
            if abs(g_xmax - g_xmin) > 1e-6 and abs(g_ymax - g_ymin) > 1e-6:
                try:
                    import pyproj
                    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645", always_xy=True)
                    x_utm, y_utm = transformer.transform(lon, lat)
                except Exception:
                    pct_x = (lon - g_xmin) / (g_xmax - g_xmin)
                    pct_y = (lat - g_ymin) / (g_ymax - g_ymin)
                    x_utm = u_xmin + pct_x * (u_xmax - u_xmin)
                    y_utm = u_ymin + pct_y * (u_ymax - u_ymin)
                
                # 3. Call getPlotAtXY with native UTM coordinates
                url_plot = f"{BHUNAKSHA_URL}/rest/MapInfo/getPlotAtXY"
                try:
                    r_plot = safe_post(url_plot, data={
                        "state": state,
                        "giscode": giscode,
                        "x": str(x_utm),
                        "y": str(y_utm)
                    }, headers=headers)
                    
                    if r_plot.status_code == 200:
                        return Response(r_plot.text, status=r_plot.status_code, content_type=r_plot.headers.get("Content-Type"))
                    else:
                        return jsonify({"error": f"BhuNaksha API returned error status {r_plot.status_code}"}), 502
                except Exception:
                    return jsonify({"error": "BhuNaksha government server is currently offline or unreachable. Clicked coordinates cannot be queried unless the parcel is cached in the local database."}), 502
                    
        return jsonify({"error": "BhuNaksha server is offline or the village sheet boundary is not cached locally. Clicked coordinates cannot be mapped."}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ROUTE 6: PNIU Search ---
# Called by: frontend app.js executePniuSearch()
# PROCESS:
#   1. Receive {state, giscode, pniu}  PNIU is a 14-digit unique plot identifier
#   2. Check pniu_cache.json for cached result
#   3. If not cached: POST to BhuNaksha /rest/MapInfo/getPointsfromPNIU
#   4. Return the raw comma-delimited string. Frontend extracts plot_no from field index 5.
@app.route("/proxy/MapInfo/getPointsfromPNIU", methods=["POST"])
def proxy_pniu_points():
    data = request.form.to_dict()
    state = data.get("state", "10")
    giscode = data.get("giscode", "")
    pniu = data.get("pniu", "")
    
    cache_key = f"{state}_{giscode}_{pniu}"
    if cache_key in pniu_cache:
        return jsonify(pniu_cache[cache_key])
        
    url = f"{BHUNAKSHA_URL}/rest/MapInfo/getPointsfromPNIU"
    headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
    try:
        r = safe_post(url, data=data, headers=headers)
        if r.status_code == 200:
            try:
                json_data = r.json()
                pniu_cache[cache_key] = json_data
                save_json_cache(PNIU_CACHE_FILE, pniu_cache)
                return jsonify(json_data)
            except Exception as e:
                print("Failed to parse PNIU response JSON:", e)
        return Response(r.text, status=r.status_code, content_type=r.headers.get("Content-Type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ROUTE 7: GIS Code Lookup ---
# Called by: internal use
# Returns the alphanumeric GIS code for a given set of admin level codes.
# Cached in giscode_cache.json.
@app.route("/proxy/MapInfo/getGisCode", methods=["POST"])
def proxy_giscode():
    data = request.form.to_dict()
    state = data.get("state", "10")
    levels = data.get("levels", "")
    
    cache_key = f"{state}_{levels}"
    if cache_key in giscode_cache:
        return jsonify(giscode_cache[cache_key])
        
    url = f"{BHUNAKSHA_URL}/rest/MapInfo/getGisCode"
    headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
    try:
        r = safe_post(url, data=data, headers=headers)
        if r.status_code == 200:
            try:
                json_data = r.json()
                giscode_cache[cache_key] = json_data
                save_json_cache(GISCODE_CACHE_FILE, giscode_cache)
                return jsonify(json_data)
            except Exception as e:
                print("Failed to parse giscode response JSON:", e)
        return Response(r.text, status=r.status_code, content_type=r.headers.get("Content-Type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ROUTE 8: WMS Map Tile Proxy ---
# Called by: OpenLayers TileWMS source in frontend (automatically as map is panned/zoomed)
# PROCESS:
#   1. Receive WMS GetMap parameters (LAYERS, gis_code, BBOX, WIDTH, HEIGHT, etc.)
#   2. Compute MD5 hash of all parameters as cache key
#   3. Check disk cache at static/wms_cache/{md5}.png  serve instantly if found
#   4. If not cached: GET from BhuNaksha /WMS endpoint
#   5. Validate response is actually an image (not an HTML session-expired page)
#   6. Save PNG to disk cache, return image bytes to browser
#   7. On any failure: return a 1x1 transparent PNG so the map doesn't break
# NOTE: This proxy is necessary because browser CORS policy blocks direct calls
#       to bhunaksha.bihar.gov.in from JavaScript.
@app.route("/proxy/WMS", methods=["GET"])
def proxy_wms():
    import hashlib
    import base64
    from pathlib import Path
    
    params = request.args.to_dict()

    def _hash_from_params(p):
        sorted_keys = sorted(p.keys())
        param_str = "&".join(f"{k}={p[k]}" for k in sorted_keys)
        return hashlib.md5(param_str.encode("utf-8")).hexdigest()

    # Primary legacy hash (exact params as received)
    legacy_hash = _hash_from_params(params)

    # Normalized params hash to avoid misses from key/value casing differences
    normalized = {}
    for k, v in params.items():
        nk = k.strip().upper()
        nv = (v or "").strip()
        if nk == "TRANSPARENT":
            nv = nv.upper()
        normalized[nk] = nv
    normalized_hash = _hash_from_params(normalized)

    # Fallback hash with only essential WMS keys to maximize offline cache hits
    essential_keys = [
        "SERVICE", "VERSION", "REQUEST", "LAYERS", "STYLES", "FORMAT",
        "TRANSPARENT", "SRS", "CRS", "GEO_CODE", "GIS_CODE", "STATE",
        "WIDTH", "HEIGHT", "BBOX"
    ]
    reduced = {k: normalized[k] for k in essential_keys if k in normalized}
    reduced_hash = _hash_from_params(reduced) if reduced else normalized_hash

    # Look in both possible cache roots (supports running app from backend/ or repo root)
    backend_cache_dir = Path(__file__).resolve().parent / "static" / "wms_cache"
    root_cache_dir = Path(__file__).resolve().parent.parent / "static" / "wms_cache"
    cache_dirs = [backend_cache_dir, root_cache_dir]

    # 2. Return from disk cache if exists for any compatible hash key
    candidate_hashes = [legacy_hash, normalized_hash, reduced_hash]
    for cdir in cache_dirs:
        for h in candidate_hashes:
            cpath = cdir / f"{h}.png"
            if cpath.exists():
                try:
                    with open(cpath, "rb") as f:
                        return Response(f.read(), status=200, content_type="image/png")
                except Exception as e:
                    print("Error reading from WMS cache:", e)
            
    # 1x1 transparent PNG fallback if remote request times out or fails
    transparent_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    transparent_png = base64.b64decode(transparent_png_base64)
    
    # 3. Fetch from remote server
    url = f"{BHUNAKSHA_URL}/WMS"
    headers = get_proxy_headers()
    try:
        # Check offline state before calling resilient_request to fail fast
        if is_server_offline():
            raise requests.exceptions.ConnectionError("BhuNaksha server known offline  skipping WMS request.")

        # Request using resilient_request with max_retries=1 to fail fast and respect rate limits
        r = resilient_request('GET', url, max_retries=1, params=params, headers=headers, timeout=10)
        
        # Check if we got an actual image back or an HTML redirect/session-expired page
        content_type = r.headers.get("Content-Type", "")
        is_html_error = content_type.startswith("text/html") or (r.text and r.text.strip().startswith("<"))
        
        if (r.status_code in [401, 403] or is_html_error):
            print("WMS request detected expired/missing session. Re-initializing session...")
            if init_session(force=True):
                # Retry with resilient_request
                r = resilient_request('GET', url, max_retries=1, params=params, headers=headers, timeout=10)
                content_type = r.headers.get("Content-Type", "")
        
        if r.status_code == 200 and content_type.startswith("image/"):
            try:
                # Write under both legacy and normalized names in both cache roots.
                # This keeps backward compatibility and improves future offline hits.
                for cdir in cache_dirs:
                    os.makedirs(str(cdir), exist_ok=True)
                    for h in set(candidate_hashes):
                        cpath = cdir / f"{h}.png"
                        with open(cpath, "wb") as f:
                            f.write(r.content)
            except Exception as e:
                print("Error saving to WMS cache:", e)
            return Response(r.content, status=r.status_code, content_type=content_type)
        else:
            print(f"WMS error response: HTTP {r.status_code}, content-type: {content_type}")
            return Response(transparent_png, status=200, content_type="image/png")
    except Exception as e:
        print(f"WMS request exception: {e}. Returning transparent PNG.")
        return Response(transparent_png, status=200, content_type="image/png")

# --- ROUTE 9: Full Parcel Details + Geometry Fetch ---
# Called by: frontend app.js selectPlotByNumber()
# This is the MOST COMPLEX endpoint  it is the core data pipeline.
#
# PROCESS FLOW:
#   STEP 1  Check SQLite (Parcel table) by {district, circle, mouza, sheet, plot_no}
#              If found in DB: serve cached data immediately (fast path)
#              If report PDF is missing from disk: re-download it from BhuNaksha
#              Convert cached UTM centroid to GPS if needed
#              Extract official area from PDF using pdf_parser.py
#              Return {success, cached:true, parcel, vertices, segments, report}
#
#   STEP 2 (if not in DB)  Fetch from BhuNaksha APIs (slow path):
#     A. ScalarDatahandler (GET): Returns owner names (HTML table) + khata_no + PNIU
#         BeautifulSoup parses the HTML to extract    (owner names)
#     B. getPlotInfo (POST): Returns WKT polygon geometry ("POLYGON ((x1 y1, x2 y2, ...))"
#         shapely.wkt.loads() parses the WKT
#         Polygon area and perimeter calculated in UTM (square meters)
#         Each UTM vertex converted to GPS (lon, lat) via pyproj or bbox interpolation
#     C. PlotReportPDF (POST): Returns the official LPM report as base64-encoded PDF
#         Decode base64  validate PDF header  save to static/reports/{giscode}_{plot_no}.pdf
#
#   STEP 3  Persist to SQLite:
#      Parcel row + ParcelVertex rows + BoundarySegment rows + LdmReport row
#      All future requests for this plot are served from STEP 1 (cache hit)
#
#   STEP 4  Extract official area from PDF using pdf_parser.py
#
#   STEP 5  Return complete parcel data to frontend:
#     {success, cached:false, parcel{...}, vertices[...], segments[...], report{url}}
#
# RESPONSE used by frontend to:
#    Populate the sidebar (plot_no, khata_no, owner_names, area, perimeter)
#    Draw the polygon boundary on the OpenLayers map (vectorSource)
#    Zoom the map to the parcel bounding box
#    Enable Export (GeoJSON, CSV) and Kurra Division buttons
@app.route("/proxy/MapInfo/getPlotDetailsAndInspection", methods=["POST"])
def get_plot_details_and_inspection():
    data = request.form.to_dict()
    state = data.get("state", "10")
    giscode = data.get("giscode")
    plot_no = data.get("plot_no")
    levels = data.get("levels")
    
    if not giscode or not plot_no or not levels:
        return jsonify({"success": False, "error": "Missing parameters"}), 400
        
    levels_parts = [p.strip() for p in levels.split(",") if p.strip()]
    if len(levels_parts) < 7:
        return jsonify({"success": False, "error": "Invalid levels format"}), 400
        
    dist_code = levels_parts[0]
    subdiv_code = levels_parts[1]
    circle_code = levels_parts[2]
    mouza_code = levels_parts[3]
    survey_code = levels_parts[4]
    mapinst_code = levels_parts[5]
    sheet_code = levels_parts[6]
    
    # Check SQLite DB first
    try:
        parcel = Parcel.query.filter_by(
            district=dist_code,
            subdivision=subdiv_code,
            circle=circle_code,
            mouza=mouza_code,
            survey=survey_code,
            mapinst=mapinst_code,
            sheet_no=sheet_code,
            plot_no=plot_no
        ).first()
        
        if parcel:
            vertices_list = [{
                "x": v.x, "y": v.y, "lon": v.lon, "lat": v.lat, "sequence_order": v.sequence_order
            } for v in sorted(parcel.vertices, key=lambda x: x.sequence_order)]
            
            segments_list = [{
                "start_vertex": s.start_vertex_index,
                "end_vertex": s.end_vertex_index,
                "length_meters": s.length_meters,
                "bearing": s.bearing
            } for s in parcel.segments]
            
            report_url = parcel.report.report_url if parcel.report else ""
            if report_url:
                rel_path = report_url.lstrip("/")
                if not os.path.exists(rel_path):
                    try:
                        import base64
                        pdf_api_url = f"{BHUNAKSHA_URL}/rest/Reports/PlotReportPDF"
                        pdf_payload = {
                            "state": state,
                            "giscode": giscode,
                            "plotno": plot_no,
                            "sameownerplotreport": "false",
                            "derivedlayerids": "-1",
                            "selectedlayerids": "-1",
                            "scaletextfield": "0"
                        }
                        pdf_r = meta_post(pdf_api_url, data=pdf_payload)
                        if pdf_r and pdf_r.status_code == 200 and pdf_r.text:
                            text_data = pdf_r.text.strip()
                            if not text_data.startswith("<") and len(text_data) > 1000:
                                try:
                                    pdf_bytes = base64.b64decode(text_data)
                                    if b"%PDF" in pdf_bytes[:20]:
                                        os.makedirs("static/reports", exist_ok=True)
                                        with open(rel_path, "wb") as f_pdf:
                                            f_pdf.write(pdf_bytes)
                                except Exception as b64_err:
                                    print("Base64 decode error for fallback PDF:", b64_err)
                    except Exception as redownload_err:
                        print("Failed to redownload missing PDF:", redownload_err)
            
            p_lat = parcel.lat
            p_lon = parcel.lon
            if p_lat is not None and p_lon is not None:
                if abs(p_lat) > 180 or abs(p_lon) > 180:
                    try:
                        gps_cache_key = f"{state}_{levels}_4326"
                        utm_cache_key = f"{state}_{levels}_0"
                        gps_extent = vvvv_extent_cache.get(gps_cache_key)
                        utm_extent = vvvv_extent_cache.get(utm_cache_key)
                        if gps_extent and utm_extent:
                            g_xmin = gps_extent.get("xmin")
                            g_xmax = gps_extent.get("xmax")
                            g_ymin = gps_extent.get("ymin")
                            g_ymax = gps_extent.get("ymax")
                            u_xmin = utm_extent.get("xmin")
                            u_xmax = utm_extent.get("xmax")
                            u_ymin = utm_extent.get("ymin")
                            u_ymax = utm_extent.get("ymax")
                            
                            if None not in (g_xmin, g_xmax, g_ymin, g_ymax, u_xmin, u_xmax, u_ymin, u_ymax):
                                pct_x = (p_lon - u_xmin) / (u_xmax - u_xmin) if (u_xmax - u_xmin) > 0 else 0.5
                                pct_y = (p_lat - u_ymin) / (u_ymax - u_ymin) if (u_ymax - u_ymin) > 0 else 0.5
                                p_lon = g_xmin + pct_x * (g_xmax - g_xmin)
                                p_lat = g_ymin + pct_y * (g_ymax - g_ymin)
                    except Exception as coord_err:
                        print("Error converting cached centroid UTM to GPS:", coord_err)

            # Extract area from PDF if possible
            official_area_ha = None
            if report_url:
                pdf_path = os.path.join(app.root_path, report_url.lstrip("/"))
                if os.path.exists(pdf_path):
                    try:
                        import pdf_parser
                        with open(pdf_path, "rb") as f_pdf:
                            official_area_ha = pdf_parser.extract_area_from_pdf_bytes(f_pdf.read())
                    except Exception as e:
                        pass

            return jsonify({
                "success": True,
                "cached": True,
                "parcel": {
                    "id": parcel.id,
                    "plot_id": parcel.plot_id,
                    "plot_no": parcel.plot_no,
                    "khata_no": parcel.khata_no,
                    "pniu": parcel.pniu,
                    "area": parcel.area,
                    "area_acres": parcel.area / 4046.8564 if parcel.area else 0.0,
                    "official_area_ha": official_area_ha,
                    "perimeter": parcel.perimeter,
                    "lat": p_lat,
                    "lon": p_lon,
                    "district": parcel.district,
                    "subdivision": parcel.subdivision,
                    "circle": parcel.circle,
                    "mouza": parcel.mouza,
                    "survey": parcel.survey,
                    "mapinst": parcel.mapinst,
                    "sheet_no": parcel.sheet_no,
                    "owner_names": json.loads(parcel.owner_names) if parcel.owner_names else [],
                    "num_vertices": len(vertices_list)
                },
                "vertices": vertices_list,
                "segments": segments_list,
                "report": {"url": report_url}
            })
    except Exception as db_err:
        print("DB query error:", db_err)
    
    # Build response with whatever data we can fetch (all remote calls are non-fatal)
    new_parcel = None
    plot_id = plot_no
    khata_no = ""
    pniu = None
    owner_names = []
    centroid_lon = None
    centroid_lat = None
    area_sqm = None
    perimeter_m = None
    vertices_list = []
    segments_list = []
    local_report_url = ""
    
    # Helper for robust metadata calls
    def meta_get(url, params=None, headers=None):
        try:
            return safe_get(url, params=params, headers=headers or get_proxy_headers())
        except Exception as e:
            print(f"meta_get exception for {url}: {e}")
            return None

    def meta_post(url, data=None, headers=None):
        try:
            return safe_post(url, data=data, headers=headers or get_proxy_headers())
        except Exception as e:
            print(f"meta_post exception for {url}: {e}")
            return None

    # 1. Try to fetch metadata from ScalarDatahandler (non-fatal, fast-fail)
    try:
        url_scalar = f"{BHUNAKSHA_URL}/ScalarDatahandler"
        params_scalar = {"OP": "5", "state": state, "levels": levels, "plotno": plot_no}
        r_scalar = meta_get(url_scalar, params=params_scalar, headers=get_proxy_headers(content_type=None))
        
        if r_scalar and r_scalar.status_code == 200:
            data_scalar = r_scalar.json()
            if data_scalar.get("has_data") == "Y":
                plot_id = data_scalar.get("ID") or plot_id
                pniu = data_scalar.get("PNIU") or pniu
                
            if not plot_id or plot_id == plot_no:
                plot_id = data_scalar.get("ID") or plot_id
            if not pniu:
                pniu = data_scalar.get("PNIU") or pniu
                
            html_info = data_scalar.get("info", "")
            if html_info:
                soup = BeautifulSoup(html_info, "html.parser")
                rows = soup.find_all("tr")
                mode = None
                for row in rows:
                    th = row.find("th")
                    if th:
                        text = th.get_text().strip()
                        if "रैयत का नाम" in text:
                            mode = "owners"
                        elif "खाता संख्या" in text:
                            mode = "khata"
                        continue
                    tds = row.find_all("td")
                    if not tds:
                        continue
                    if mode == "owners" and len(tds) >= 2:
                        owner_names.append(tds[1].get_text().strip())
                    elif mode == "khata":
                        khata_no = tds[0].get_text().strip()
                        
            try:
                xmin = float(data_scalar.get("xmin", 0))
                xmax = float(data_scalar.get("xmax", 0))
                ymin = float(data_scalar.get("ymin", 0))
                ymax = float(data_scalar.get("ymax", 0))
                if xmin or xmax or ymin or ymax:
                    centroid_lon = (xmin + xmax) / 2.0
                    centroid_lat = (ymin + ymax) / 2.0
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print("ScalarDatahandler error:", e)
    
    # 2. Try to fetch geometry and metadata from getPlotInfo (non-fatal, fast-fail)
    r_geom_failed = False
    try:
        url_geom = f"{BHUNAKSHA_URL}/rest/MapInfo/getPlotInfo"
        geom_headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
        r_geom = meta_post(url_geom, data={
            "state": state, "giscode": giscode, "plotno": plot_no
        }, headers=geom_headers)
        
        if r_geom is None:
            r_geom_failed = True
        elif r_geom.status_code == 200:
            data_geom = r_geom.json()
            
            if not plot_id or plot_id == plot_no:
                plot_id = data_geom.get("plotid") or plot_id
                
            if centroid_lat is None or centroid_lon is None:
                try:
                    centroid_lon = (float(data_geom.get("xmin", 0)) + float(data_geom.get("xmax", 0))) / 2.0
                    centroid_lat = (float(data_geom.get("ymin", 0)) + float(data_geom.get("ymax", 0))) / 2.0
                except (TypeError, ValueError):
                    pass
            
            geom_wkt = data_geom.get("the_geom") or data_geom.get("geom")
            if geom_wkt and "POLYGON" in geom_wkt:
                poly = shapely.wkt.loads(geom_wkt)
                area_sqm = poly.area
                perimeter_m = poly.length
                
                if poly.geom_type == 'MultiPolygon':
                    largest_poly = max(poly.geoms, key=lambda a: a.area)
                    raw_coords = list(largest_poly.exterior.coords)
                elif poly.geom_type == 'Polygon':
                    raw_coords = list(poly.exterior.coords)
                else:
                    raw_coords = []
                
                # Load GPS and UTM extents for coordinate conversion
                gps_extent = utm_extent = None
                gps_cache_key = f"{state}_{levels}_4326"
                utm_cache_key = f"{state}_{levels}_0"
                
                gps_extent = vvvv_extent_cache.get(gps_cache_key)
                if not gps_extent:
                    try:
                        r_gps = meta_post(f"{BHUNAKSHA_URL}/rest/MapInfo/getVVVVExtentGeoref", data={
                            "state": state, "gisLevels": levels, "srs": "4326"
                        }, headers=geom_headers)
                        gps_extent = r_gps.json() if r_gps and r_gps.status_code == 200 else None
                        if gps_extent:
                            vvvv_extent_cache[gps_cache_key] = gps_extent
                            save_json_cache(EXTENT_CACHE_FILE, vvvv_extent_cache)
                    except Exception:
                        pass
                
                utm_extent = vvvv_extent_cache.get(utm_cache_key)
                if not utm_extent:
                    try:
                        r_utm = meta_post(f"{BHUNAKSHA_URL}/rest/MapInfo/getVVVVExtentGeoref", data={
                            "state": state, "gisLevels": levels, "srs": "0"
                        }, headers=geom_headers)
                        utm_extent = r_utm.json() if r_utm and r_utm.status_code == 200 else None
                        if utm_extent:
                            vvvv_extent_cache[utm_cache_key] = utm_extent
                            save_json_cache(EXTENT_CACHE_FILE, vvvv_extent_cache)
                    except Exception:
                        pass
                
                if gps_extent and utm_extent:
                    g_xmin, g_xmax = gps_extent["xmin"], gps_extent["xmax"]
                    g_ymin, g_ymax = gps_extent["ymin"], gps_extent["ymax"]
                    u_xmin, u_xmax = utm_extent["xmin"], utm_extent["xmax"]
                    u_ymin, u_ymax = utm_extent["ymin"], utm_extent["ymax"]
                    
                    try:
                        import pyproj
                        transformer = pyproj.Transformer.from_crs("EPSG:32645", "EPSG:4326", always_xy=True)
                        def map_utm_to_gps(x, y):
                            return transformer.transform(x, y)
                    except Exception:
                        def map_utm_to_gps(x, y):
                            pct_x = (x - u_xmin) / (u_xmax - u_xmin) if (u_xmax - u_xmin) > 0 else 0.5
                            pct_y = (y - u_ymin) / (u_ymax - u_ymin) if (u_ymax - u_ymin) > 0 else 0.5
                            lon = g_xmin + pct_x * (g_xmax - g_xmin)
                            lat = g_ymin + pct_y * (g_ymax - g_ymin)
                            return lon, lat
                    
                    for i, (x_utm, y_utm) in enumerate(raw_coords):
                        lon_gps, lat_gps = map_utm_to_gps(x_utm, y_utm)
                        vertices_list.append({
                            "x": x_utm, "y": y_utm, "lon": lon_gps, "lat": lat_gps, "sequence_order": i
                        })
                    
                    for i in range(len(raw_coords) - 1):
                        p1 = raw_coords[i]
                        p2 = raw_coords[i+1]
                        dx = p2[0] - p1[0]
                        dy = p2[1] - p1[1]
                        length = math.sqrt(dx*dx + dy*dy)
                        bearing = math.atan2(dx, dy) * 180 / math.pi
                        bearing = (bearing + 360) % 360
                        segments_list.append({
                            "start_vertex": i, "end_vertex": i+1, "length_meters": length, "bearing": bearing
                        })
                
                # Also try to get PDF report
                # Also try to get PDF report via the REST API
                try:
                    import base64
                    pdf_api_url = f"{BHUNAKSHA_URL}/rest/Reports/PlotReportPDF"
                    pdf_payload = {
                        "state": state,
                        "giscode": giscode,
                        "plotno": plot_no,
                        "sameownerplotreport": "false",
                        "derivedlayerids": "-1",
                        "selectedlayerids": "-1",
                        "scaletextfield": "0"
                    }
                    pdf_r = meta_post(pdf_api_url, data=pdf_payload)
                    
                    if pdf_r and pdf_r.status_code == 200 and pdf_r.text:
                        text_data = pdf_r.text.strip()
                        # Check that the response is not HTML and is long enough
                        if not text_data.startswith("<") and len(text_data) > 1000:
                            try:
                                pdf_bytes = base64.b64decode(text_data)
                                if b"%PDF" in pdf_bytes[:20]:
                                    os.makedirs("static/reports", exist_ok=True)
                                    filename = f"{giscode}_{plot_no}.pdf"
                                    filepath = os.path.join("static", "reports", filename)
                                    with open(filepath, "wb") as f_pdf:
                                        f_pdf.write(pdf_bytes)
                                    local_report_url = f"/static/reports/{filename}"
                            except Exception as b64_err:
                                print("Base64 decode error for PDF:", b64_err)
                except Exception as report_err:
                    print("PDF download error:", report_err)
                    
    except Exception as e:
        print("getPlotInfo error:", e)
    
    # Cache whatever we got in the database
    if vertices_list:
        try:
            new_parcel = Parcel(
                plot_id=plot_id,
                plot_no=plot_no,
                khata_no=khata_no,
                pniu=pniu,
                area=area_sqm,
                perimeter=perimeter_m,
                lat=centroid_lat,
                lon=centroid_lon,
                district=dist_code,
                subdivision=subdiv_code,
                circle=circle_code,
                mouza=mouza_code,
                survey=survey_code,
                mapinst=mapinst_code,
                sheet_no=sheet_code,
                owner_names=json.dumps(owner_names, ensure_ascii=False)
            )
            db.session.add(new_parcel)
            db.session.flush()
            
            for v in vertices_list:
                db.session.add(ParcelVertex(
                    parcel_id=new_parcel.id, x=v["x"], y=v["y"],
                    lon=v["lon"], lat=v["lat"], sequence_order=v["sequence_order"]
                ))
            for s in segments_list:
                db.session.add(BoundarySegment(
                    parcel_id=new_parcel.id,
                    start_vertex_index=s["start_vertex"],
                    end_vertex_index=s["end_vertex"],
                    length_meters=s["length_meters"],
                    bearing=s["bearing"]
                ))
            if local_report_url:
                db.session.add(LdmReport(
                    parcel_id=new_parcel.id,
                    report_url=local_report_url,
                    filename=f"{giscode}_{plot_no}.pdf"
                ))
            db.session.commit()
        except Exception as commit_err:
            db.session.rollback()
            print("DB cache error:", commit_err)
    # Convert centroid coordinate to GPS if it is currently in UTM
    if centroid_lon is not None and centroid_lat is not None:
        if abs(centroid_lon) > 180 or abs(centroid_lat) > 180:
            try:
                gps_cache_key = f"{state}_{levels}_4326"
                utm_cache_key = f"{state}_{levels}_0"
                gps_extent = vvvv_extent_cache.get(gps_cache_key)
                utm_extent = vvvv_extent_cache.get(utm_cache_key)
                if gps_extent and utm_extent:
                    g_xmin = gps_extent.get("xmin")
                    g_xmax = gps_extent.get("xmax")
                    g_ymin = gps_extent.get("ymin")
                    g_ymax = gps_extent.get("ymax")
                    u_xmin = utm_extent.get("xmin")
                    u_xmax = utm_extent.get("xmax")
                    u_ymin = utm_extent.get("ymin")
                    u_ymax = utm_extent.get("ymax")
                    
                    if None not in (g_xmin, g_xmax, g_ymin, g_ymax, u_xmin, u_xmax, u_ymin, u_ymax):
                        pct_x = (centroid_lon - u_xmin) / (u_xmax - u_xmin) if (u_xmax - u_xmin) > 0 else 0.5
                        pct_y = (centroid_lat - u_ymin) / (u_ymax - u_ymin) if (u_ymax - u_ymin) > 0 else 0.5
                        centroid_lon = g_xmin + pct_x * (g_xmax - g_xmin)
                        centroid_lat = g_ymin + pct_y * (g_ymax - g_ymin)
            except Exception as coord_err:
                print("Error converting fallback centroid UTM to GPS:", coord_err)

    if not vertices_list:
        if r_geom_failed:
            err_msg = "BhuNaksha government server is currently offline or unreachable. Selected parcel details cannot be retrieved because it is not cached in the local database."
        else:
            err_msg = f"Plot {plot_no} was not found or has no geometry on the BhuNaksha server."
            
        return jsonify({
            "success": False,
            "error": err_msg
        }), 502

    # Extract Area from PDF if it was saved
    official_area_ha = None
    if local_report_url:
        pdf_path = os.path.join(app.root_path, local_report_url.lstrip("/"))
        if os.path.exists(pdf_path):
            try:
                import pdf_parser
                with open(pdf_path, "rb") as f_pdf:
                    official_area_ha = pdf_parser.extract_area_from_pdf_bytes(f_pdf.read())
            except Exception as e:
                pass

    return jsonify({
        "success": True,
        "cached": False,
        "parcel": {
            "id": new_parcel.id if (new_parcel and new_parcel.id) else None,
            "plot_id": plot_id,
            "plot_no": plot_no,
            "khata_no": khata_no,
            "pniu": pniu,
            "area": area_sqm,
            "area_acres": area_sqm / 4046.8564 if area_sqm else None,
            "official_area_ha": official_area_ha,
            "perimeter": perimeter_m,
            "lat": centroid_lat,
            "lon": centroid_lon,
            "district": dist_code,
            "subdivision": subdiv_code,
            "circle": circle_code,
            "mouza": mouza_code,
            "survey": survey_code,
            "mapinst": mapinst_code,
            "sheet_no": sheet_code,
            "owner_names": owner_names,
            "num_vertices": len(vertices_list)
        },
        "vertices": vertices_list,
        "segments": segments_list,
        "report": {"url": local_report_url}
    })

# --- ROUTE 10: Export Parcel as GeoJSON ---
# Called by: frontend app.js #btn-export-geojson click handler
# PROCESS:
#   1. Look up parcel by parcel_id or plot_no in SQLite
#   2. Build RFC 7946 GeoJSON Feature from stored vertex GPS coordinates
#   3. Return with Content-Disposition: attachment header to trigger browser download
@app.route("/proxy/Export/GeoJSON/<plot_no>", methods=["GET"])
def export_geojson(plot_no):
    parcel_id = request.args.get("parcel_id")
    if parcel_id and parcel_id not in ("null", "undefined", ""):
        parcel = db.session.get(Parcel, parcel_id)
    else:
        parcel = Parcel.query.filter_by(plot_no=plot_no).order_by(Parcel.id.desc()).first()
        
    if not parcel:
        return jsonify({"error": "Parcel not found"}), 404
        
    coords = [[v.lon, v.lat] for v in sorted(parcel.vertices, key=lambda x: x.sequence_order)]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
        
    geojson = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [coords]
        },
        "properties": {
            "plot_no": parcel.plot_no,
            "plot_id": parcel.plot_id,
            "khata_no": parcel.khata_no,
            "pniu": parcel.pniu,
            "area_sqm": parcel.area,
            "area_acres": parcel.area / 4046.8564 if parcel.area else 0.0,
            "perimeter_meters": parcel.perimeter,
            "owner_names": json.loads(parcel.owner_names) if parcel.owner_names else [],
            "district": parcel.district,
            "circle": parcel.circle,
            "mouza": parcel.mouza,
            "sheet_no": parcel.sheet_no
        }
    }
    response = jsonify(geojson)
    response.headers["Content-Disposition"] = f"attachment; filename=plot_{plot_no}.geojson"
    return response

# --- ROUTE 11: Export Parcel as CSV ---
# Called by: frontend app.js #btn-export-csv click handler
# PROCESS:
#   1. Look up parcel by parcel_id or plot_no in SQLite
#   2. Write parcel metadata, all vertices (UTM + GPS coordinates), and segment lengths to CSV
#   3. Return with Content-Disposition: attachment header to trigger browser download
@app.route("/proxy/Export/CSV/<plot_no>", methods=["GET"])
def export_csv(plot_no):
    parcel_id = request.args.get("parcel_id")
    if parcel_id and parcel_id not in ("null", "undefined", ""):
        parcel = db.session.get(Parcel, parcel_id)
    else:
        parcel = Parcel.query.filter_by(plot_no=plot_no).order_by(Parcel.id.desc()).first()
        
    if not parcel:
        return "Parcel not found", 404
        
    import io
    import csv
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(["Bhu-Overlay Parcel Boundary Data"])
    writer.writerow(["Plot Number", parcel.plot_no])
    writer.writerow(["Plot ID", parcel.plot_id])
    writer.writerow(["Khata Number", parcel.khata_no])
    writer.writerow(["PNIU Code", parcel.pniu])
    writer.writerow(["Area (Sq Meters)", parcel.area])
    writer.writerow(["Area (Acres)", parcel.area / 4046.8564 if parcel.area else 0.0])
    writer.writerow(["Perimeter (Meters)", parcel.perimeter])
    writer.writerow(["Owners", ", ".join(json.loads(parcel.owner_names) if parcel.owner_names else [])])
    writer.writerow([])
    
    writer.writerow(["Vertices Coordinates"])
    writer.writerow(["Sequence Order", "UTM Easting (X)", "UTM Northing (Y)", "Longitude", "Latitude"])
    for v in sorted(parcel.vertices, key=lambda x: x.sequence_order):
        writer.writerow([v.sequence_order, v.x, v.y, v.lon, v.lat])
    writer.writerow([])
    
    writer.writerow(["Boundary Segments Side Lengths"])
    writer.writerow(["Start Vertex", "End Vertex", "Length (Meters)", "Bearing (Degrees)"])
    for s in parcel.segments:
        writer.writerow([s.start_vertex_index, s.end_vertex_index, s.length_meters, s.bearing])
        
    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=plot_{plot_no}_boundary.csv"}
    )

# --- ROUTE 12: AI Kurra Land Division ---
# Called by: frontend app.js #btn-subdivide click handler
# This is the core AI feature of Bhu-Overlay.
#
# PROCESS FLOW:
#   STEP 1  Load parcel geometry from SQLite using parcel_id
#             Build Shapely Polygon from stored UTM vertex coordinates
#             Initialize pyproj transformers: UTM 45N (EPSG:32645)  WGS84 (EPSG:4326)
#
#   STEP 2  Query Bihar GIS ArcGIS REST servers for real infrastructure vectors:
#              gis_querier.get_nearby_vector_features() queries:
#               - NHRoads, SHRoads, MDR, ROAD_BIHAR, Village_Road MapServers
#               - Rivers, IrrigationStreams MapServers
#              Returns sorted lists of road and river LineString geometries with:
#               name, category, priority, distance_meters
#              The closest touching road becomes the "primary frontage"
#
#   STEP 3  Generate mathematical split strategies:
#              subdivide.generate_strategies() creates up to 4 strategies:
#               1. Compact Cut (parallel to shortest side of min rotated rectangle)
#               2. Longitudinal Cut (parallel to longest side)
#               3. Road Access Cut (perpendicular to nearest road frontage)
#               4. River Access Cut (perpendicular to nearest river)
#              Each strategy splits the UTM polygon using binary search on cut lines
#              Near-identical strategies (within 5 angle) are deduplicated
#
#   STEP 4  Pre-calculate per-strategy statistics for LLM context:
#             For each strategy  sub-plot:
#               - Road frontage length (meters): buffer(5m).intersection(road_line).length
#               - River frontage length (meters): same method
#               - Number of manually placed features (trees/wells) inside the sub-plot
#
#   STEP 5  Consult local LLM (llm_expert.consult_llm_for_division()):
#              Builds a detailed prompt with:
#               - Parcel metadata (district, circle, mouza, area, owners)
#               - All strategy stats (frontage, river, features per sub-plot)
#               - Road and river context
#               - User custom preferences (mutual consent, Rule 109(g))
#              LLM evaluates strategies against UP Revenue Code 2006 rules:
#               Rule 109(b) compactness, 109(f) road access, 116(2) trees/wells
#              LLM returns: recommended_strategy_index + explanation paragraph
#              If LLM is offline: falls back to strategy index 0 (Compact Cut)
#
#   STEP 6  Convert winning sub-polygon UTM coordinates back to GPS (pyproj)
#             Build GeoJSON FeatureCollection with per-sub-plot properties:
#             sub_plot_id, share_percentage, area_sqm, perimeter_m, frontage_m,
#             contained_features (trees/wells inside the sub-plot)
#
#   RESPONSE: GeoJSON FeatureCollection + strategy_name + llm_explanation + frontage_coords
#   USED BY frontend to: draw colored sub-polygons on map + show AI explanation box
@app.route("/api/parcel/<plot_no>/subdivide", methods=["POST"])
def subdivide_parcel(plot_no):
    data = request.json
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
        
    shares = data.get("shares")
    if not shares or not isinstance(shares, list):
         return jsonify({"error": "Missing or invalid 'shares' list"}), 400
         
    trees_and_wells = data.get("features", []) # List of dicts: {"type": "tree", "x": ..., "y": ...}
    parcel_info = data.get("parcel_info", {})
    user_preferences = data.get("user_preferences", "")
    
    parcel_id = request.args.get("parcel_id")
    if parcel_id and parcel_id not in ("null", "undefined", ""):
        parcel = db.session.get(Parcel, parcel_id)
    else:
        parcel = Parcel.query.filter_by(plot_no=plot_no).order_by(Parcel.id.desc()).first()
        
    if not parcel:
        return jsonify({"error": "Parcel not found"}), 404
        
    vertices = sorted(parcel.vertices, key=lambda v: v.sequence_order)
    if not vertices:
         return jsonify({"error": "Parcel has no geometric data"}), 400
         
    # Build shapely polygon using UTM coordinates for accurate area calculations in meters
    poly_coords = [(v.x, v.y) for v in vertices]
    if poly_coords[0] != poly_coords[-1]:
        poly_coords.append(poly_coords[0])
    
    poly = Polygon(poly_coords)
    if not poly.is_valid:
         poly = poly.buffer(0)
         
    # Function to map UTM back to GPS
    import pyproj
    try:
        transformer = pyproj.Transformer.from_crs("EPSG:32645", "EPSG:4326", always_xy=True)
        transformer_inv = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32645", always_xy=True)
        def utm_to_gps(x, y):
             return transformer.transform(x, y)
        def gps_to_utm(lon, lat):
             return transformer_inv.transform(lon, lat)
    except Exception:
         return jsonify({"error": "Coordinate transformation failed"}), 500
         
    # Build parcel polygon in GPS coordinates for distance checks
    import shapely.geometry
    parcel_coords_gps = [(v.lon, v.lat) for v in vertices]
    if parcel_coords_gps[0] != parcel_coords_gps[-1]:
        parcel_coords_gps.append(parcel_coords_gps[0])
    parcel_poly_gps = shapely.geometry.Polygon(parcel_coords_gps)
    if not parcel_poly_gps.is_valid:
        parcel_poly_gps = parcel_poly_gps.buffer(0)

    # Query Bihar GIS MapServer for real vector roads and rivers using our querier
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import gis_querier
    try:
        roads, rivers = gis_querier.get_nearby_vector_features(parcel_poly_gps)
    except Exception as e:
        print(f"ArcGIS REST query failed, falling back to empty geometries: {e}")
        roads, rivers = [], []

    frontage_utm = []
    road_name_for_llm = "None"
    road_details_for_llm = []
    
    if roads:
        # Choose the highest priority closest road as primary frontage
        best_road = roads[0]
        road_name_for_llm = best_road["name"]
        frontage_utm = [gps_to_utm(pt[0], pt[1]) for pt in best_road["geometry"].coords]
        
        # Build info for LLM
        for rd in roads[:3]: # list top 3 nearby roads
            road_details_for_llm.append({
                "name": rd["name"],
                "category": rd["category"],
                "distance_meters": round(rd["distance_m"], 1)
            })

    river_utm = []
    river_name_for_llm = "None"
    river_details_for_llm = []
    if rivers:
        best_river = rivers[0]
        river_name_for_llm = best_river["name"]
        river_utm = [gps_to_utm(pt[0], pt[1]) for pt in best_river["geometry"].coords]
        
        for rv in rivers[:3]:
            river_details_for_llm.append({
                "name": rv["name"],
                "category": rv["category"],
                "distance_meters": round(rv["distance_m"], 1)
            })
            
    nearby_river = len(rivers) > 0
    
    import llm_expert
    try:
         strategies = subdivide.generate_strategies(poly, shares, frontage_utm, river_utm)
         if not strategies:
             return jsonify({"error": "Failed to generate any valid division strategies"}), 500
             
         import shapely.geometry
         # Pre-calculate sub-plot stats for ALL strategies so LLM can make informed decisions
         for strat in strategies:
             strat_info = []
             for sp in strat["polys"]:
                 front_len = 0
                 if frontage_utm:
                     front_line = shapely.geometry.LineString(frontage_utm)
                     front_len = sp.buffer(5.0).intersection(front_line).length
                     
                 river_len = 0
                 if river_utm:
                     river_line = shapely.geometry.LineString(river_utm)
                     river_len = sp.buffer(5.0).intersection(river_line).length
                 
                 c_feats = 0
                 sp_coords = list(sp.exterior.coords)
                 sp_gps = shapely.geometry.Polygon([utm_to_gps(x, y) for x, y in sp_coords])
                 for feat in trees_and_wells:
                     pt_gps = shapely.geometry.Point(feat["x"], feat["y"])
                     if sp_gps.contains(pt_gps) or sp_gps.distance(pt_gps) < 1e-5:
                         c_feats += 1
                 strat_info.append({"frontage_m": front_len, "river_frontage_m": river_len, "features": c_feats})
             strat["sub_plot_stats"] = strat_info
             
         # Prepare parcel info for LLM
         payload_data = {
             "area_sqm": poly.area,
             "shares": shares,
             "has_frontage": len(frontage_utm) > 0,
             "primary_road": road_name_for_llm,
             "nearby_roads": road_details_for_llm,
             "nearby_river": nearby_river,
             "primary_river": river_name_for_llm,
             "nearby_rivers_list": river_details_for_llm,
             "features": trees_and_wells,
             "parcel_info": parcel_info,
             "user_preferences": user_preferences,
             "vision_context": "None"
         }
         
         # 4. Ask LLM
         llm_result = llm_expert.consult_llm_for_division(payload_data, strategies)
         
         best_idx = 0
         explanation = ""
         llm_failed = False
         
         if llm_result.get("success"):
             best_idx = llm_result.get("recommended_index", 0)
             if best_idx < 0 or best_idx >= len(strategies):
                 best_idx = 0
             explanation = llm_result.get("explanation")
         else:
             llm_failed = True
             explanation = f"Configured LLM provider failed ({llm_result.get('error', 'unknown error')}). Falling back to the default algorithmic strategy (Compact Cut)."
             
         best_strategy = strategies[best_idx]
         sub_polys = best_strategy["polys"]
         strategy_name = best_strategy["name"]
         
    except Exception as e:
         import traceback
         traceback.print_exc()
         return jsonify({"error": f"Subdivision failed: {str(e)}"}), 500
         
    results = []
    
    # Process each sub-polygon
    for i, sp in enumerate(sub_polys):
        sp_coords = list(sp.exterior.coords)
        gps_coords = [utm_to_gps(x, y) for x, y in sp_coords]
        
        # Check which features fall in this polygon
        contained_features = []
        sp_gps = Polygon(gps_coords)
        for feat in trees_and_wells:
            pt_gps = Point(feat["x"], feat["y"])
            if sp_gps.contains(pt_gps) or sp_gps.distance(pt_gps) < 1e-5:
                contained_features.append(feat)
                
        # Perimeter of the front edge (if provided)
        frontage_length = 0
        if frontage_utm:
             # Basic check if the subpolygon touches the frontage line
             front_line = shapely.geometry.LineString(frontage_utm)
             intersection = sp.buffer(5.0).intersection(front_line)
             frontage_length = intersection.length
             
        results.append({
             "type": "Feature",
             "properties": {
                 "sub_plot_id": i + 1,
                 "share_percentage": shares[i],
                 "area_sqm": sp.area,
                 "perimeter_m": sp.length,
                 "frontage_m": frontage_length,
                 "contained_features": contained_features
             },
             "geometry": {
                 "type": "Polygon",
                 "coordinates": [gps_coords]
             }
        })
        
    return jsonify({
        "type": "FeatureCollection",
        "features": results,
        "llm_explanation": explanation,
        "llm_failed": llm_failed,
        "strategy_name": strategy_name,
        "frontage_coords": [[utm_to_gps(x, y)[0], utm_to_gps(x, y)[1]] for x, y in frontage_utm] if frontage_utm else []
    })

# --- ROUTE 13: Generate PDF Kurra Report ---
# Called by: frontend app.js #btn-download-kurra-report click handler
# PROCESS:
#   1. Receive {features, subdivisions, frontage, parcel_info} from frontend
#      (frontend sends the sub-plot geometries it computed from the last subdivide call)
#   2. Load parcel vertex GPS coordinates from SQLite
#   3. Call report_generator.generate_kurra_report() which:
#      A. Creates a white canvas image using NumPy
#      B. Draws parcel outline, subdivision fills, road frontage, and feature dots with OpenCV
#      C. Saves image to a temp file
#      D. Builds a multi-page PDF using FPDF2:
#         - Page 1: Land Details (district, mouza, khata, area) + map image
#         - Page 2: Segregation Details (per-sub-plot stats) + AI explanation
#      E. Returns raw PDF bytes
#   4. Return bytes with Content-Disposition: attachment header for browser download
@app.route("/api/parcel/<plot_no>/generate_report", methods=["POST"])
def generate_report_route(plot_no):
    try:
        data = request.json or {}
        features = data.get("features", [])
        subdivisions = data.get("subdivisions", [])
        frontage_coords = data.get("frontage", [])
        parcel_info = data.get("parcel_info", {})
        
        parcel_id = request.args.get("parcel_id")
        if parcel_id and parcel_id not in ("null", "undefined", ""):
            parcel = db.session.get(Parcel, parcel_id)
        else:
            parcel = Parcel.query.filter_by(plot_no=plot_no).order_by(Parcel.id.desc()).first()
            
        if not parcel:
            return jsonify({"error": "Parcel not found"}), 404
            
        parcel_vertices = [[v.lon, v.lat] for v in sorted(parcel.vertices, key=lambda x: x.sequence_order)]
        
        import report_generator
        pdf_data = report_generator.generate_kurra_report(
            plot_no=plot_no,
            parcel_vertices=parcel_vertices,
            features=features,
            subdivisions=subdivisions,
            frontage_coords=frontage_coords,
            parcel_info=parcel_info
        )
        
        if not pdf_data:
            return jsonify({"error": "Failed to generate report"}), 500
            
        return Response(
            bytes(pdf_data),
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=Kurra_Report_{plot_no}.pdf"}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to generate report: {str(e)}"}), 500

def generate_grid_points(state, levels, grid_step=30.0):
    """Generates a grid of UTM coordinates inside the sheet's bounding box."""
    utm_cache_key = f"{state}_{levels}_0"
    utm_extent = vvvv_extent_cache.get(utm_cache_key)
    
    if not utm_extent:
        url = f"{BHUNAKSHA_URL}/rest/MapInfo/getVVVVExtentGeoref"
        headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
        try:
            r = safe_post(url, data={
                "state": state,
                "gisLevels": levels,
                "srs": "0"
            }, headers=headers)
            if r.status_code == 200:
                utm_extent = r.json()
                if utm_extent:
                    vvvv_extent_cache[utm_cache_key] = utm_extent
                    save_json_cache(EXTENT_CACHE_FILE, vvvv_extent_cache)
        except Exception as e:
            print("Error fetching UTM extent during grid generation:", e)
            
    if not utm_extent or utm_extent.get("xmin") is None:
        return []
        
    xmin, ymin = float(utm_extent["xmin"]), float(utm_extent["ymin"])
    xmax, ymax = float(utm_extent["xmax"]), float(utm_extent["ymax"])
    
    points = []
    x = xmin
    while x <= xmax:
        y = ymin
        while y <= ymax:
            points.append((x, y))
            y += grid_step
        x += grid_step
    return points

# --- ROUTE 14: Batch Sheet Scraper ---
# Called by: frontend app.js #btn-clone-sheet click handler (looped)
# PURPOSE: Auto-discover and cache ALL plot geometries in a given cadastral sheet.
# PROCESS:
#   1. Receive {state, giscode, levels, batch_index, batch_size, grid_step}
#   2. generate_grid_points(): Create a uniform grid of UTM points (35m spacing)
#      covering the entire sheet's UTM bounding box
#   3. Load all already-known parcel polygons from SQLite to build skip list
#   4. For each grid point in this batch:
#      A. If point is inside a known polygon: skip (already discovered)
#      B. If not: POST to BhuNaksha /rest/MapInfo/getPlotAtXY  get plot_no
#      C. Call get_plot_details_and_inspection() in a test context to fetch + cache geometry
#      D. Query new parcel polygon from DB and add to skip list
#   5. Return progress: {batch_index, scanned_points, new_plots_found, total_plots_saved, is_done}
# NOTE: Frontend loops this call (batch_index++) until is_done=true, then
#       redirects to /api/sheet/export_geojson to download the full sheet as GeoJSON.
@app.route("/api/sheet/scrape_batch", methods=["POST"])
def scrape_batch():
    data = request.json or request.form.to_dict()
    state = data.get("state", "10")
    giscode = data.get("giscode")
    levels = data.get("levels")
    batch_index = int(data.get("batch_index", 0))
    batch_size = int(data.get("batch_size", 100))
    grid_step = float(data.get("grid_step", 35.0))
    
    if not giscode or not levels:
        return jsonify({"success": False, "error": "Missing parameters"}), 400
        
    points = generate_grid_points(state, levels, grid_step)
    total_points = len(points)
    
    if total_points == 0:
        return jsonify({"success": False, "error": "Failed to generate grid or empty sheet bounds"}), 400
        
    start_idx = batch_index * batch_size
    end_idx = min(start_idx + batch_size, total_points)
    batch_points = points[start_idx:end_idx]
    
    levels_parts = [p.strip() for p in levels.split(",") if p.strip()]
    dist_code = levels_parts[0]
    subdiv_code = levels_parts[1]
    circle_code = levels_parts[2]
    mouza_code = levels_parts[3]
    survey_code = levels_parts[4]
    mapinst_code = levels_parts[5]
    sheet_code = levels_parts[6]
    
    # Load all already-known polygons from database
    db_parcels = Parcel.query.filter_by(
        district=dist_code,
        subdivision=subdiv_code,
        circle=circle_code,
        mouza=mouza_code,
        survey=survey_code,
        mapinst=mapinst_code,
        sheet_no=sheet_code
    ).all()
    
    from shapely.geometry import Polygon, Point
    
    known_polygons = []
    found_plot_nos = set()
    
    for p in db_parcels:
        verts = sorted(p.vertices, key=lambda x: x.sequence_order)
        if len(verts) >= 3:
            try:
                poly = Polygon([(v.x, v.y) for v in verts])
                known_polygons.append((poly, p.plot_no))
            except Exception:
                pass
        found_plot_nos.add(p.plot_no)
        
    new_plots_found = []
    points_skipped = 0
    points_queried = 0
    
    for x_val, y_val in batch_points:
        pt = Point(x_val, y_val)
        inside = False
        for poly, p_no in known_polygons:
            if poly.contains(pt):
                inside = True
                break
        if inside:
            points_skipped += 1
            continue
            
        points_queried += 1
        url_plot = f"{BHUNAKSHA_URL}/rest/MapInfo/getPlotAtXY"
        headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
        try:
            r_plot = resilient_request('POST', url_plot, data={
                "state": state,
                "giscode": giscode,
                "x": str(x_val),
                "y": str(y_val)
            }, headers=headers, timeout=8)
            
            if r_plot.status_code == 200:
                plot_no = r_plot.text.strip()
                if plot_no and not plot_no.startswith("<") and "error" not in plot_no.lower():
                    plot_no = plot_no.split(",")[0].strip()
                    if plot_no and plot_no not in found_plot_nos:
                        # Invoke get_plot_details_and_inspection in a mocked context to parse and store it
                        with app.test_request_context(method="POST", data={
                            "state": state,
                            "giscode": giscode,
                            "plot_no": plot_no,
                            "levels": levels
                        }):
                            get_plot_details_and_inspection()
                            
                        # Query the newly saved parcel from DB to get its polygon
                        p = Parcel.query.filter_by(
                            district=dist_code,
                            subdivision=subdiv_code,
                            circle=circle_code,
                            mouza=mouza_code,
                            survey=survey_code,
                            mapinst=mapinst_code,
                            sheet_no=sheet_code,
                            plot_no=plot_no
                        ).first()
                        
                        if p:
                            found_plot_nos.add(plot_no)
                            new_plots_found.append(plot_no)
                            verts = sorted(p.vertices, key=lambda x: x.sequence_order)
                            if len(verts) >= 3:
                                try:
                                    poly = Polygon([(v.x, v.y) for v in verts])
                                    known_polygons.append((poly, plot_no))
                                except Exception:
                                    pass
        except Exception as e:
            print(f"Error scraping at grid point ({x_val}, {y_val}): {e}")
            
    # Calculate overall progress
    all_sheet_parcels = Parcel.query.filter_by(
        district=dist_code,
        subdivision=subdiv_code,
        circle=circle_code,
        mouza=mouza_code,
        survey=survey_code,
        mapinst=mapinst_code,
        sheet_no=sheet_code
    ).all()
    
    return jsonify({
        "success": True,
        "batch_index": batch_index,
        "batch_size": batch_size,
        "total_points": total_points,
        "scanned_points_in_batch": len(batch_points),
        "points_skipped_in_batch": points_skipped,
        "points_queried_in_batch": points_queried,
        "new_plots_found": new_plots_found,
        "total_plots_saved": len(all_sheet_parcels),
        "is_done": end_idx >= total_points
    })

# --- ROUTE 15: Export All Cached Plots in a Sheet as GeoJSON ---
# Called by: frontend (triggered after batch scrape completes)
# PROCESS:
#   1. Parse admin level codes from the 'levels' query parameter
#   2. Query ALL Parcel rows from SQLite matching {district, circle, mouza, sheet}
#   3. Build a GeoJSON FeatureCollection with one Feature per parcel
#   4. Return with Content-Disposition: attachment to download as .geojson file
@app.route("/api/sheet/export_geojson", methods=["GET"])
def export_sheet_geojson():
    state = request.args.get("state", "10")
    levels = request.args.get("levels")
    
    if not levels:
        return jsonify({"error": "Missing levels parameter"}), 400
        
    levels_parts = [p.strip() for p in levels.split(",") if p.strip()]
    if len(levels_parts) < 7:
        return jsonify({"error": "Invalid levels format"}), 400
        
    dist_code = levels_parts[0]
    subdiv_code = levels_parts[1]
    circle_code = levels_parts[2]
    mouza_code = levels_parts[3]
    survey_code = levels_parts[4]
    mapinst_code = levels_parts[5]
    sheet_code = levels_parts[6]
    
    # Query all parcels for this sheet in SQLite
    parcels = Parcel.query.filter_by(
        district=dist_code,
        subdivision=subdiv_code,
        circle=circle_code,
        mouza=mouza_code,
        survey=survey_code,
        mapinst=mapinst_code,
        sheet_no=sheet_code
    ).all()
    
    features = []
    for parcel in parcels:
        coords = [[v.lon, v.lat] for v in sorted(parcel.vertices, key=lambda x: x.sequence_order)]
        if coords:
            if coords[0] != coords[-1]:
                coords.append(coords[0])
                
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords]
                },
                "properties": {
                    "plot_no": parcel.plot_no,
                    "plot_id": parcel.plot_id,
                    "khata_no": parcel.khata_no,
                    "pniu": parcel.pniu,
                    "area_sqm": parcel.area,
                    "area_acres": parcel.area / 4046.8564 if parcel.area else 0.0,
                    "perimeter_meters": parcel.perimeter,
                    "owner_names": json.loads(parcel.owner_names) if parcel.owner_names else [],
                    "district": parcel.district,
                    "circle": parcel.circle,
                    "mouza": parcel.mouza,
                    "sheet_no": parcel.sheet_no
                }
            })
            
    geojson = {
        "type": "FeatureCollection",
        "features": features
    }
    
    response = jsonify(geojson)
    response.headers["Content-Disposition"] = f"attachment; filename=sheet_{sheet_code}_clone.geojson"
    return response

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)
