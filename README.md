# Bihar Cadastral FMB Sketch Division (Google Gemini API Setup)

This repository contains the standalone version of the Bihar Cadastral FMB Sketch Division application, configured to run using Google's Gemini API key directly (with no local LLM/LM Studio server dependencies).

## Prerequisites

Ensure you have **Python 3.8+** installed on your system.

---

## Getting Started

Follow these commands to set up and run the application on your computer.

### 1. Clone the Repository
Open a terminal and run:
```bash
git clone https://github.com/GladwinDaniel/fmb_bihar_using_gemini_api.git
cd fmb_bihar_using_gemini_api
```

### 2. Create and Activate a Virtual Environment
Create a clean environment to isolate package dependencies:

* **Windows (Command Prompt / CMD)**:
  ```cmd
  python -m venv venv
  venv\Scripts\activate
  ```

* **Windows (PowerShell)**:
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```

* **macOS / Linux**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 3. Install Dependencies
Install all required libraries using the provided `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 4. Configure Your Gemini API Key
Obtain an API key from Google AI Studio and set it as an environment variable:

* **Windows (Command Prompt / CMD)**:
  ```cmd
  set GEMINI_API_KEY=your_actual_api_key_here
  ```

* **Windows (PowerShell)**:
  ```powershell
  $env:GEMINI_API_KEY="your_actual_api_key_here"
  ```

* **macOS / Linux**:
  ```bash
  export GEMINI_API_KEY="your_actual_api_key_here"
  ```

---

## Running the Application

1. **Start the Flask Server**:
   ```bash
   python app.py
   ```
   The application will start on port `5001`. You should see the following output in the terminal:
   ```text
    * Serving Flask app 'app'
    * Running on http://127.0.0.1:5001
   ```

2. **Access the App**:
   Open your browser and navigate to:
   **[http://127.0.0.1:5001](http://127.0.0.1:5001)**

---

## Verifying the Setup

To run the automated test suite and ensure all backend components and database models are working perfectly:
```bash
python -m pytest test_phase2_backend.py -v
```
All test cases should return `PASSED`.
