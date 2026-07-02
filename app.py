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
import cv_detector

# Disable insecure request warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bhunaksha.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()

BHUNAKSHA_URL = "https://bhunaksha.bihar.gov.in"

# Global session to maintain cookies
session = requests.Session()
session.verify = False

# Persistent cache files
DROPDOWN_CACHE_FILE = "dropdown_cache.json"
EXTENT_CACHE_FILE = "extent_cache.json"

def load_json_cache(filename):
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading cache file {filename}: {e}")
    return {}

def save_json_cache(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving cache file {filename}: {e}")

lists_after_level_cache = load_json_cache(DROPDOWN_CACHE_FILE)
vvvv_extent_cache = load_json_cache(EXTENT_CACHE_FILE)

GISCODE_CACHE_FILE = "giscode_cache.json"
PNIU_CACHE_FILE = "pniu_cache.json"
PLOT_AT_XY_CACHE_FILE = "plot_at_xy_cache.json"

giscode_cache = load_json_cache(GISCODE_CACHE_FILE)
pniu_cache = load_json_cache(PNIU_CACHE_FILE)
plot_at_xy_cache = load_json_cache(PLOT_AT_XY_CACHE_FILE)

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
session_init_success = False

def init_session(force=False):
    """Establishes the session cookies with BhuNaksha, using disk cache if available."""
    global session, last_session_init_time, session_init_success
    import time
    
    current_time = time.time()
    # Apply cooldown of 60 seconds unless forced
    if not force and current_time - last_session_init_time < 60:
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
      - Exponential backoff with random jitter on retries
      - Connection error/timeout handling
      - Dynamic session recovery on HTML redirect or 401/403 auth errors
    """
    import time
    import random
    
    is_bhunaksha = BHUNAKSHA_URL in url
    
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
                
            # Detect expired/redirected sessions
            is_html_error = False
            if is_bhunaksha:
                content_type = r.headers.get("Content-Type", "")
                is_html_error = content_type.startswith("text/html") or r.text.strip().startswith("<")
                
            # If session is invalid (and we're not asking for standard HTML/WMS)
            if is_bhunaksha and (r.status_code in [401, 403] or is_html_error) and "WMS" not in url:
                print(f"Auth/session error on {url} (HTTP {r.status_code}/HTML response). Attempting session re-init...")
                if init_session(force=True):
                    # Update referer header if present
                    if 'headers' in kwargs and 'Referer' in kwargs['headers']:
                        kwargs['headers']['Referer'] = f"{BHUNAKSHA_URL}/10/indexmain.jsp"
                    # Retry immediately with fresh session
                    if method.upper() == 'POST':
                        r = session.post(url, **kwargs)
                    else:
                        r = session.get(url, **kwargs)
            
            # If server has an internal error (500) or bad status code
            if r.status_code in [500, 502, 503, 504]:
                raise requests.exceptions.HTTPError(f"HTTP {r.status_code}", response=r)
                
            return r
            
        except (requests.exceptions.RequestException, ConnectionError, Exception) as e:
            print(f"Request failed ({url}): {e} (Attempt {attempt} of {max_retries})")
            
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

@app.route("/")
def home():
    return render_template("index.html")

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

@app.route("/proxy/WMS", methods=["GET"])
def proxy_wms():
    import hashlib
    import base64
    
    params = request.args.to_dict()
    
    # 1. Compute dynamic unique hash key for this WMS tile/map request
    sorted_keys = sorted(params.keys())
    param_str = "&".join(f"{k}={params[k]}" for k in sorted_keys)
    h = hashlib.md5(param_str.encode('utf-8')).hexdigest()
    
    # Define cache directory and file path
    cache_dir = os.path.join("static", "wms_cache")
    cache_path = os.path.join(cache_dir, f"{h}.png")
    
    # 2. Return from disk cache if exists
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
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
        # Request with a slightly shorter timeout (10s) to keep app responsive
        r = session.get(url, params=params, headers=headers, timeout=10)
        
        # Check if we got an actual image back or an HTML redirect/session-expired page
        content_type = r.headers.get("Content-Type", "")
        is_html_error = content_type.startswith("text/html") or (r.text and r.text.strip().startswith("<"))
        
        if (r.status_code in [401, 403] or is_html_error):
            print("WMS request detected expired/missing session. Re-initializing session...")
            if init_session(force=True):
                r = session.get(url, params=params, headers=headers, timeout=10)
                content_type = r.headers.get("Content-Type", "")
        
        if r.status_code == 200 and content_type.startswith("image/"):
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, "wb") as f:
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
                import os
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
    try:
        url_geom = f"{BHUNAKSHA_URL}/rest/MapInfo/getPlotInfo"
        geom_headers = get_proxy_headers(content_type="application/x-www-form-urlencoded; charset=UTF-8")
        r_geom = meta_post(url_geom, data={
            "state": state, "giscode": giscode, "plotno": plot_no
        }, headers=geom_headers)
        
        if r_geom and r_geom.status_code == 200:
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
        return jsonify({
            "success": False,
            "error": "BhuNaksha government server is currently offline or unreachable. Selected parcel details cannot be retrieved because it is not cached in the local database."
        }), 502

    # Extract Area from PDF if it was saved
    official_area_ha = None
    if local_report_url:
        import os
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

@app.route("/proxy/Export/GeoJSON/<plot_no>", methods=["GET"])
def export_geojson(plot_no):
    parcel_id = request.args.get("parcel_id")
    if parcel_id and parcel_id not in ("null", "undefined", ""):
        parcel = Parcel.query.get(parcel_id)
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

@app.route("/proxy/Export/CSV/<plot_no>", methods=["GET"])
def export_csv(plot_no):
    parcel_id = request.args.get("parcel_id")
    if parcel_id and parcel_id not in ("null", "undefined", ""):
        parcel = Parcel.query.get(parcel_id)
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

@app.route("/api/parcel/<plot_no>/nearby", methods=["GET"])
def get_nearby_infrastructure(plot_no):
    parcel_id = request.args.get("parcel_id")
    gis_code = request.args.get("gis_code")
    
    if not parcel_id or not gis_code:
        return jsonify({"error": "Missing parcel_id or gis_code"}), 400
        
    if parcel_id and parcel_id not in ("null", "undefined", ""):
        parcel = Parcel.query.get(parcel_id)
    else:
        parcel = Parcel.query.filter_by(plot_no=plot_no).order_by(Parcel.id.desc()).first()
        
    if not parcel:
        return jsonify({"error": "Parcel not found"}), 404
        
    vertices = sorted(parcel.vertices, key=lambda v: v.sequence_order)
    if not vertices:
        return jsonify({"error": "No valid geometry"}), 400
        
    poly_coords = [(v.x, v.y) for v in vertices]
    if poly_coords[0] != poly_coords[-1]:
        poly_coords.append(poly_coords[0])
    poly = Polygon(poly_coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
        
    import math
    minx, miny, maxx, maxy = poly.bounds
    lat_buffer = 100.0 / 111000.0
    lon_buffer = 100.0 / (111000.0 * math.cos(math.radians((miny + maxy) / 2)))
    
    import cv_detector
    gis_features = cv_detector.query_bihar_gis_features(
        minx - lon_buffer, miny - lat_buffer, maxx + lon_buffer, maxy + lat_buffer
    )
    
    return jsonify({
        "success": True,
        "roads": gis_features.get("roads", []),
        "rivers": gis_features.get("rivers", [])
    })

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
        parcel = Parcel.query.get(parcel_id)
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
         
    # Query Bihar GIS MapServer for real roads and rivers
    lats = [v.lat for v in vertices]
    lons = [v.lon for v in vertices]
    radius_m = 100.0
    lat_buffer = radius_m / 111000.0
    lon_buffer = radius_m / (111000.0 * math.cos(math.radians((min(lats) + max(lats)) / 2)))
    
    min_lon = min(lons) - lon_buffer
    max_lon = max(lons) + lon_buffer
    min_lat = min(lats) - lat_buffer
    max_lat = max(lats) + lat_buffer
    
    import cv_detector
    gis_features = cv_detector.query_bihar_gis_features(min_lon, min_lat, max_lon, max_lat)
    
    frontage_utm = []
    if gis_features.get("roads"):
        # Just pick the first road path for frontage strategy
        road_gps_path = gis_features["roads"][0]["path"]
        frontage_utm = [gps_to_utm(lon, lat) for lon, lat in road_gps_path]
        
    river_utm = []
    if gis_features.get("rivers"):
        river_gps_path = gis_features["rivers"][0]["path"]
        river_utm = [gps_to_utm(lon, lat) for lon, lat in river_gps_path]
        
    nearby_river = len(gis_features.get("rivers", [])) > 0
    
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
             "nearby_river": nearby_river,
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
             print("Gemini API Error fallback:", llm_result.get("error"))
             explanation = "Google Gemini API unreachable or failed. Falling back to the default algorithmic strategy (Compact Cut)."
             
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
        "strategy_name": strategy_name
    })

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
            parcel = Parcel.query.get(parcel_id)
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
            pdf_data,
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
    app.run(host="127.0.0.1", port=5002, debug=True, use_reloader=False)
