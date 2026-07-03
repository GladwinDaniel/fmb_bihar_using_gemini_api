# Bhu-Overlay -- Installation Guide

> **Target platform**: Ubuntu Linux with VS Code.  
> All commands below are run in the **VS Code integrated terminal** (bash).

---

## Prerequisites

Install these once on your Ubuntu machine (skip anything already installed):

```bash
# Python 3.9+, pip, venv support, and git
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git

# Verify versions
python3 --version   # must be 3.9 or higher
git --version
```

You also need a modern browser (Chrome, Firefox, or Edge).

---

## Step 1 -- Clone the Repository

Open VS Code, press **Ctrl+`** to open the integrated terminal, then:

```bash
git clone <your-repo-url>
cd Fmb

# Open the folder in VS Code
code .
```

> [!TIP]
> VS Code will automatically suggest installing recommended extensions (Python, Pylance, etc.).  
> Click **Install All** when prompted.

---

## Step 2 -- Run the Setup Script (first-time only)

This single command does **everything**: creates a virtual environment, installs all Python packages, asks which LLM provider you want, writes `backend/.env`, and starts the backend.

```bash
chmod +x setup.sh
./setup.sh
```

You will see an interactive menu:

```
Which LLM provider do you want to use?

  1) Google Gemini  (recommended -- free API key)
  2) xAI Grok       (API key required)
  3) Local LM Studio / Ollama  (offline, no key needed)
  4) Skip LLM setup  (fallback mode)

Enter choice [1-4]:
```

Pick your provider, paste your API key when asked, and the backend starts automatically.

> [!IMPORTANT]
> `backend/.env` (which contains your API key) is listed in `.gitignore` and will **never** be committed to git.

---

## Step 3 -- Start the Frontend

Open a **second terminal tab** in VS Code (**Ctrl+Shift+`**) and run:

```bash
source venv/bin/activate
python -m http.server 8080
```

Then open your browser at:
```
http://localhost:8080/frontend/index.html
```

---

## Using VS Code Tasks (Recommended)

Instead of typing commands every day, use the built-in tasks.  
Press **Ctrl+Shift+P** → type **"Run Task"** → pick from the list:

| Task | What it does |
|---|---|
| 🚀 **Setup & Start (first-time install)** | Runs `setup.sh` -- full install + backend start |
| ▶ **Start Backend** | Activates venv and starts Flask (use after first-time setup) |
| 🌐 **Start Frontend (HTTP server on :8080)** | Serves the frontend at `localhost:8080` |
| 🔑 **Switch to Gemini** | Updates `backend/.env` to use Gemini (prompts for key) |
| 🔑 **Switch to Grok** | Updates `backend/.env` to use Grok (prompts for key) |
| 🖥 **Switch to Local LM Studio** | Updates `backend/.env` for local/offline LLM |
| 📦 **Install / Update Dependencies** | Re-runs `pip install -r requirements.txt` |
| 🧪 **Run Backend Tests** | Runs `pytest` on the backend test suite |

> [!TIP]
> You can also use **Ctrl+Shift+B** as a shortcut to run the default build task (**Setup & Start**).

---

## Daily Workflow (after first-time setup)

Every time you want to work on the project:

```bash
# Terminal 1 -- backend
source venv/bin/activate
cd backend && python app.py

# Terminal 2 -- frontend
source venv/bin/activate
python -m http.server 8080
```

Or simply use the VS Code tasks above.

---

## Switching LLM Providers

To switch from Gemini to Grok (or vice versa) **without editing any code**:

**Option A -- VS Code Task (easiest):**  
`Ctrl+Shift+P` → **Run Task** → **🔑 Switch to Gemini** (or Grok)

**Option B -- Edit `backend/.env` directly:**
```bash
# Use your preferred editor
nano backend/.env
```

Change `LLM_PROVIDER=` to `gemini`, `grok`, or `local`, fill in the matching key, save, then restart the backend.

**Option C -- Environment variables in the terminal:**
```bash
# Gemini
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=your_key_here
export GEMINI_MODEL=gemini-2.5-flash   # optional

# Grok
export LLM_PROVIDER=grok
export GROK_API_KEY=your_key_here
export GROK_MODEL=grok-2-latest        # optional
```

Then restart `python app.py`.

> [!NOTE]
> Environment variables set in the terminal override `backend/.env` values.

---

## Project File Overview

```
Fmb/
├── setup.sh                    ← Run this first after cloning (Ubuntu/Linux)
├── .env.example                ← Configuration template (copy to backend/.env)
├── requirements.txt            ← Python dependencies
├── .vscode/
│   ├── tasks.json              ← VS Code tasks (Setup, Start, Switch provider, Test)
│   └── extensions.json         ← Recommended VS Code extensions
│
├── backend/
│   ├── app.py                  ← Flask server -- all API routes
│   ├── llm_expert.py           ← LLM router (Gemini / Grok / Local)
│   ├── models.py               ← SQLAlchemy ORM models
│   ├── subdivide.py            ← Polygon splitting algorithms (Shapely)
│   ├── gis_querier.py          ← Bihar ArcGIS REST queries (roads, rivers)
│   ├── report_generator.py     ← Kurra PDF generation (FPDF2 + OpenCV)
│   ├── pdf_parser.py           ← Extracts area from BhuNaksha PDFs
│   ├── test_phase2_backend.py  ← Backend unit tests
│   └── .env                    ← YOUR API KEYS (created by setup.sh, not in git)
│
├── frontend/
│   ├── index.html              ← Single-page app
│   ├── app.js                  ← All UI logic (OpenLayers, AJAX)
│   └── style.css               ← Dark-theme responsive CSS
│
└── docs/
    ├── README.md
    ├── INSTALL.md              ← This file
    ├── TECHNICAL_DOCS.md
    ├── CONNECTION_GUIDE.md
    ├── backend_architecture.md
    └── frontend_architecture.md
```

---

## Verification

Once both servers are running, verify everything works:

1. **Dropdown loads** -- The "District" dropdown should auto-populate with Bihar districts. If empty, the backend is not running on port `5001`.
2. **Select a location** -- District → Sub-Division → Circle → Mouza → Survey → Map Instance → Sheet. A cadastral map overlay should appear.
3. **Click a parcel** -- Click any plot boundary. Owner name, plot number, and area should appear in the sidebar.
4. **Run tests** (optional):
   ```bash
   source venv/bin/activate
   cd backend && python -m pytest test_phase2_backend.py -v
   ```

---

## Common Errors & Fixes

| Error | Cause | Fix |
|---|---|---|
| `python3: command not found` | Python not installed | `sudo apt install python3` |
| `ModuleNotFoundError: No module named 'flask'` | venv not activated or deps not installed | `source venv/bin/activate && pip install -r requirements.txt` |
| `Address already in use` on port 5001 | Another Flask process is running | `pkill -f "python app.py"` then restart |
| `Address already in use` on port 8080 | Another HTTP server is running | `pkill -f "http.server"` then restart |
| Districts dropdown is empty | Backend not reachable | Check that `python app.py` is running in `backend/` |
| `cv2` import error | OpenCV binary issue | `pip install opencv-python-headless` |
| LLM gives no output / fallback fires | Wrong API key or `LLM_PROVIDER` not set | Check `backend/.env` -- run **Switch to Gemini** task |
| `GEMINI_API_KEY is not set` error in terminal | `.env` missing or empty | Copy `.env.example` → `backend/.env` and fill in key |
| `bhunaksha.db` permission error | DB file locked | `fuser -k backend/instance/bhunaksha.db` |
| Map shows no WMS tiles | BhuNaksha government server offline | Normal -- tiles are cached after first load |

---

## Updating the Project

```bash
git pull origin main
source venv/bin/activate
pip install -r requirements.txt   # install any new dependencies
```

---

## Team Quickstart

### Frontend Team
- Edit files in `frontend/` (`index.html`, `app.js`, `style.css`)
- You still need the backend running -- use the **▶ Start Backend** VS Code task
- Read `CONNECTION_GUIDE.md` to understand how the UI calls the API

### Backend Team
- Work inside `backend/`
- Start the server: **▶ Start Backend** VS Code task
- Run tests: **🧪 Run Backend Tests** VS Code task
- Read `TECHNICAL_DOCS.md` and `backend_architecture.md`

### AI / LLM Team
- Edit `backend/llm_expert.py` (the LLM router)
- Switch providers with the **🔑 Switch to Gemini / Grok** VS Code tasks
- The LLM is called at every `/api/parcel/<plot_no>/subdivide` request

### GIS / Data Team
- Focus on `backend/gis_querier.py`, `backend/subdivide.py`, and the SQLite DB
- Inspect the DB: `sudo apt install sqlitebrowser` then open `backend/instance/bhunaksha.db`
