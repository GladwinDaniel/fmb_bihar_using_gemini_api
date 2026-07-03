# Bhu-Overlay -- Installation Guide (Gemini API Edition)

This guide walks you through setting up **Bhu-Overlay** on a Windows, macOS, or Linux machine from scratch.

> **This is the Gemini API version.** It uses Google's Gemini API for AI-powered Kurra division instead of a local LLM. No LM Studio installation is required.

---

## Prerequisites

Before you begin, ensure you have the following:

| Tool | Version | Where to get it |
|---|---|---|
| **Python** | 3.9 or higher | https://www.python.org/downloads/ |
| **pip** | (bundled with Python) | Run `pip --version` to verify |
| **Git** | Any recent version | https://git-scm.com/downloads |
| **Google Gemini API Key** | Free tier available | https://aistudio.google.com/apikey |
| A modern web browser | Chrome, Firefox, Edge |  |

> [!NOTE]
> Internet access to Bihar Government GIS servers is only needed for the first time you load a village sheet. After that, all data is cached locally in `bhunaksha.db` and JSON cache files, and the app works fully offline (except for AI Kurra division, which always requires internet to reach the Gemini API).

---

## Step 1 -- Clone the Repository

Open a terminal (Command Prompt / PowerShell on Windows, Terminal on macOS/Linux):

```bash
git clone https://github.com/GladwinDaniel/fmb_bihar_using_gemini_api.git
cd fmb_bihar_using_gemini_api
```

Your project directory should look like:
```
fmb_bihar_using_gemini_api/
 backend/           Python Flask backend
 frontend/          HTML/JS/CSS frontend
 requirements.txt   Python dependency list
 README.md
 INSTALL.md         This file
```

---

## Step 2 -- Create a Virtual Environment (Recommended)

It is best practice to isolate project dependencies.

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` appear at the start of your terminal prompt.

> [!TIP]
> To deactivate the virtual environment later, simply type `deactivate`.

---

## Step 3 -- Install Python Dependencies

With the virtual environment active, run:

```bash
pip install -r requirements.txt
```

This installs all required packages:

| Package | Purpose |
|---|---|
| `flask` | Core web server and API framework for the backend |
| `flask-cors` | Allows the browser frontend to call the backend API (CORS headers) |
| `flask-sqlalchemy` | ORM for persisting parcel data in SQLite |
| `requests` | HTTP client for proxying calls to BhuNaksha, Bihar GIS, and Gemini API |
| `urllib3` | HTTP library (suppresses SSL warnings from government servers) |
| `beautifulsoup4` | Parses BhuNaksha HTML responses to extract owner names and khata data |
| `lxml` | Fast HTML parser backend for BeautifulSoup |
| `shapely` | Polygon geometry: splitting, containment checks, buffers |
| `pyproj` | Coordinate conversion: WGS84 GPS <-> UTM Zone 45N (EPSG:32645) |
| `numpy` | Numerical arrays for geometry computations |
| `fpdf2` | Generates the Kurra Division PDF reports |
| `pdfplumber` | Extracts the official area value from BhuNaksha downloaded PDF reports |
| `opencv-python` | Draws the parcel map diagram (polygon overlays, feature dots) inside the PDF |
| `python-dotenv` | Loads `GEMINI_API_KEY` from a `.env` file |
| `pytest` | Test framework to run backend unit tests |

> [!WARNING]
> **Windows Users**: `opencv-python` requires Microsoft Visual C++ Redistributable. If the install fails, download it from [Microsoft](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) and retry.

---

## Step 4 -- Configure the Gemini API Key (Required for AI Kurra Division)

The AI Kurra Division engine sends subdivision strategy data to the **Google Gemini API**. You need a free API key to use this feature.

### Get a Free API Key

1. Go to [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key

### Set the API Key

**Option A -- Environment Variable (Recommended for quick start)**

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

> [!IMPORTANT]
> You must set this environment variable in the same terminal session where you run `python app.py`. The variable is lost when you close the terminal.

**Option B -- Use a `.env` File (Recommended for persistent setup)**

Create a file named `.env` inside the `backend/` directory:

```
backend/.env
```

With this content:
```env
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

The `python-dotenv` package will automatically load this file when the backend starts.

> [!NOTE]
> `GEMINI_MODEL` is optional. If not set, it defaults to `gemini-2.5-flash`. You can change it to `gemini-2.5-pro` or any other available Gemini model.

> [!TIP]
> If the Gemini API key is not set or the API call fails, the system automatically falls back to the first mathematical strategy (Compact Cut). You can still use all other features without an API key.

---

## Step 5 -- Start the Backend Server

Navigate into the backend directory and start the Flask application:

```bash
cd backend
python app.py
```

You should see output like:
```
Session cookies loaded from disk.
 * Running on http://127.0.0.1:5001
 * Debug mode: on
```

> [!IMPORTANT]
> The backend **must be running** before you open the frontend. The backend serves as the API gateway -- all map data flows through it.
>
> The backend runs on port **5001** by default. Do **not** change this unless you also update `API_BASE_URL` in `frontend/app.js`.

**Do not close this terminal window** -- keep the backend running in the background.

---

## Step 6 -- Open the Frontend

Open a **new terminal window** (keep the backend terminal open), and start a simple HTTP server from the project root:

```bash
# Navigate back to the project root from the backend/ folder
cd ..

# Start a simple HTTP server on port 8080
python -m http.server 8080
```

Now open your browser and navigate to:
```
http://localhost:8080/frontend/index.html
```

---

## Fast Setup On Another PC (Windows PowerShell)

Use these commands exactly:

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

Open a second PowerShell window:

```powershell
cd fmb_bihar_using_gemini_api
python -m http.server 8080
```

Then open `http://localhost:8080/frontend/index.html`.

> [!TIP]
> You can also open `frontend/index.html` directly by double-clicking the file in your file explorer.
> The app auto-detects `file://` and `localhost` origins and routes API calls to `http://127.0.0.1:5001` correctly.

---

## Verification

Once you have the frontend open in your browser, verify the setup works:

1. **Districts load**: The "District" dropdown should auto-populate with Bihar's district names. If it doesn't, the backend is not reachable -- check that the Flask server is running on port `5001`.

2. **Select a location**: Choose District -> Sub-Division -> Circle -> Mouza -> Survey -> Map Instance -> Sheet. The cadastral map overlay should appear on the satellite map.

3. **Click a parcel**: Click any plot boundary on the map. Plot details (Number, Khata, Owner, Area) should appear in the sidebar.

4. **Test AI Kurra**: Select a plot, click "Kurra Division", set shares, and click "Generate". If `GEMINI_API_KEY` is set, the AI recommendation box should appear with an explanation from Gemini.

5. **Run tests** (optional): From the `backend/` directory:
   ```bash
   python -m pytest test_phase2_backend.py -v
   ```

---

## Project File Overview

```
Fmb_gemini/
 README.md                     Feature overview and API reference
 INSTALL.md                    This installation guide
 TECHNICAL_DOCS.md             Deep technical documentation
 requirements.txt              Python package dependencies
 backend_architecture.md       Backend Mermaid architecture diagram
 frontend_architecture.md      Frontend Mermaid architecture diagram
 CONNECTION_GUIDE.md           How frontend & backend communicate (HTTP/AJAX)

 backend/
    app.py                    <- Flask server -- API gateway, all 14 routes
    models.py                 SQLAlchemy ORM models (Parcel, Vertex, Segment, Report)
    subdivide.py              Polygon splitting (binary search, Shapely)
    llm_expert.py             Google Gemini API consultation for strategy ranking
    gis_querier.py            Bihar ArcGIS REST queries (roads, rivers)
    report_generator.py       Kurra PDF generation (FPDF2 + OpenCV)
    pdf_parser.py             Extracts area from BhuNaksha PDF reports
    dropdown_cache.json       Auto-generated: administrative hierarchy cache
    extent_cache.json         Auto-generated: sheet bounding box cache
    session_cookies.json      Auto-generated: BhuNaksha session cookie store
    test_phase2_backend.py    Backend unit tests
    instance/
        bhunaksha.db          Auto-created SQLite database on first run

 frontend/
     index.html                Single-page application HTML
     app.js                    All application logic (OpenLayers, AJAX, UI)
     style.css                 Responsive dark-theme CSS
```

---

## Common Errors & Fixes

| Error | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'flask'` | Dependencies not installed | Run `pip install -r requirements.txt` |
| `Address already in use` on port 5001 | Another process is using port 5001 | Kill the other process or change the port in `app.py` (last line) |
| Districts dropdown is empty | Backend is not reachable | Ensure `python app.py` is running in the `backend/` directory |
| `cv2` / OpenCV import error | Binary not installed properly | Try `pip install opencv-python-headless` instead of `opencv-python` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Python on macOS needs certificates | Run `/Applications/Python 3.x/Install Certificates.command` |
| AI Kurra shows "GEMINI_API_KEY not set" | API key not configured | Set the `GEMINI_API_KEY` environment variable (see Step 4) |
| Gemini API returns 429 (rate limit) | Too many requests on free tier | Wait and retry, or upgrade your Google AI plan |
| Gemini API returns 400 | Invalid API key | Check your key at https://aistudio.google.com/apikey |
| `bhunaksha.db` permission error | DB file locked | Close all other processes accessing the DB |
| Map shows no WMS tiles | BhuNaksha server offline | Normal -- government server has downtime. Tiles are cached for future use. |

---

## Updating the Project

To update to the latest version:

```bash
git pull origin main
pip install -r requirements.txt   # Install any new dependencies
```

---

## Team Quickstart

### Frontend-Only Team
- Edit files in `frontend/` (`index.html`, `app.js`, `style.css`).
- You still need the backend running to test API calls. Follow Steps 5-6 above.
- Read `frontend_architecture.md` and `CONNECTION_GUIDE.md`.

### Backend-Only Team
- Focus on the `backend/` directory.
- You need Python 3.9+, `pip`, and the virtual environment (Steps 2-3).
- Run `python app.py` to start the server (Step 5).
- Run `python -m pytest test_phase2_backend.py -v` to execute unit tests.
- Read `backend_architecture.md` and `TECHNICAL_DOCS.md`.

### GIS / Data Team
- Focus on `gis_querier.py` (road/river queries from Bihar GIS), `subdivide.py` (polygon algorithms), and the SQLite database in `backend/instance/bhunaksha.db`.

### AI / LLM Team
- Focus on `llm_expert.py` (prompt engineering and strategy ranking using Gemini API).
- Configure your API key via `.env` or environment variables (Step 4).
- The Gemini API is consulted during every Kurra division request at the `/api/parcel/<plot_no>/subdivide` endpoint.
