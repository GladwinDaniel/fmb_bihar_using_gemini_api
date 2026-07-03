"""
llm_expert.py -- Multi-provider LLM router
==========================================
Supports three LLM backends, selected via the LLM_PROVIDER environment variable:

  LLM_PROVIDER=gemini   (default) -- Google Gemini REST API
  LLM_PROVIDER=grok                -- xAI Grok REST API
  LLM_PROVIDER=local               -- Any local OpenAI-compatible server (LM Studio, Ollama, etc.)

Provider-specific environment variables
----------------------------------------
Gemini:
  GEMINI_API_KEY   -- required
  GEMINI_MODEL     -- optional, default: gemini-2.5-flash

Grok:
  GROK_API_KEY     -- required
  GROK_MODEL       -- optional, default: grok-2-latest
  GROK_API_URL     -- optional, default: https://api.x.ai/v1/chat/completions

Local (LM Studio / Ollama):
  LLM_API_URL      -- optional, default: http://localhost:1234/v1/chat/completions
  LLM_MODEL_NAME   -- optional, default: local-model

See .env.example in the project root for a ready-to-copy configuration template.
"""

import json
import os
import re

import requests

# ---------------------------------------------------------------------------
# Load .env file if present (python-dotenv is optional)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()          # reads backend/.env if it exists
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass  # python-dotenv not installed; use system env vars instead

# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower().strip()

# Gemini settings
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL",   "gemini-2.5-flash")
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# Grok settings
GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
GROK_MODEL   = os.environ.get("GROK_MODEL",   "grok-2-latest")
GROK_API_URL = os.environ.get("GROK_API_URL", "https://api.x.ai/v1/chat/completions")

# Local (LM Studio / Ollama) settings
LOCAL_LLM_API_URL   = os.environ.get("LLM_API_URL",    "http://localhost:1234/v1/chat/completions")
LOCAL_LLM_MODEL     = os.environ.get("LLM_MODEL_NAME", "local-model")


# ===========================================================================
# Internal helpers -- one function per backend
# ===========================================================================

def _call_gemini(system_prompt: str, user_prompt: str) -> str:
    """Call Google Gemini REST API and return raw text content."""
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY is not set. "
            "Add it to backend/.env or set it as an environment variable."
        )

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2000,
        },
    }

    resp = requests.post(GEMINI_API_URL, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_grok(system_prompt: str, user_prompt: str) -> str:
    """Call xAI Grok REST API (OpenAI-compatible) and return raw text content."""
    if not GROK_API_KEY:
        raise ValueError(
            "GROK_API_KEY is not set. "
            "Add it to backend/.env or set it as an environment variable."
        )

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    resp = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_local(system_prompt: str, user_prompt: str) -> str:
    """Call a local OpenAI-compatible LLM server (LM Studio, Ollama, etc.)."""
    payload = {
        "model": LOCAL_LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    resp = requests.post(LOCAL_LLM_API_URL, json=payload, timeout=45)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ===========================================================================
# JSON extraction helper (works for all backends)
# ===========================================================================

def _extract_json(content: str) -> dict:
    """Extract a JSON object from an LLM response that may contain markdown fences."""
    # Try stripping markdown code fences
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        start = content.find("{")
        end   = content.rfind("}")
        json_str = content[start : end + 1] if start != -1 and end != -1 else content

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback: regex parse for the two fields we need
        idx_match = re.search(r'"recommended_strategy_index"\s*:\s*(\d+)', content)
        exp_match = re.search(r'"explanation"\s*:\s*"([^"]+)"', content)
        if not exp_match:
            exp2 = re.search(r'"explanation"\s*:\s*"?([\s\S]+)', content)
            exp_str = (
                exp2.group(1).replace('"', "").replace("}", "").strip()
                if exp2
                else f"Failed to parse LLM response. Raw: {content[:200]}"
            )
        else:
            exp_str = exp_match.group(1)

        return {
            "recommended_strategy_index": int(idx_match.group(1)) if idx_match else 1,
            "explanation": exp_str,
        }


# ===========================================================================
# Public API
# ===========================================================================

def consult_llm_for_division(parcel_info: dict, strategies: list) -> dict:
    """
    Route the subdivision consultation to the configured LLM backend.

    Parameters
    ----------
    parcel_info : dict   -- parcel metadata and user preferences
    strategies  : list   -- list of subdivision strategy dicts with polygon stats

    Returns
    -------
    dict with keys:
      success (bool)
      recommended_index (int, 0-indexed)
      explanation (str)
      provider (str)   -- which backend was used
    OR on failure:
      success (bool) = False
      error (str)
    """

    # ------------------------------------------------------------------
    # Build strategy text
    # ------------------------------------------------------------------
    str_text = ""
    for i, strat in enumerate(strategies):
        str_text += f"\nStrategy {i + 1}: {strat['name']}\n"
        stats = strat.get("sub_plot_stats", [])
        for j, poly in enumerate(strat["polys"]):
            frontage       = stats[j]["frontage_m"]         if j < len(stats) else 0
            river_frontage = stats[j].get("river_frontage_m", 0) if j < len(stats) else 0
            feats          = stats[j]["features"]            if j < len(stats) else 0
            str_text += (
                f"  - Sub-Plot {j + 1}: "
                f"Area = {poly.area:.1f} sqm, "
                f"Perimeter = {poly.length:.1f} m, "
                f"Road Frontage = {frontage:.1f} m, "
                f"River Frontage = {river_frontage:.1f} m, "
                f"Features Inside = {feats}\n"
            )

    # ------------------------------------------------------------------
    # Prompts (identical for all backends)
    # ------------------------------------------------------------------
    system_prompt = """You are an expert Indian land revenue officer and surveyor.
Your task is to review mathematical subdivision strategies for a land parcel (Kurra division) and explain which strategy is best according to the UP Revenue Code, 2006, Section 116 and 117.

Key Legal Rules you MUST enforce:
1. Rule 109(f) [Road Access/Commercial Value]: "If the plot or any part thereof is of commercial value or is adjacent to road, abadi or any other land of commercial value, the same shall be allotted to each tenure holder proportionately..." You must heavily penalize strategies that landlock a plot or deny proportional road access. Land adjacent to a road is significantly more valuable.
2. Rule 109(b) [Compactness]: "The portion allotted to each party shall be as compact as possible."
3. Section 116(2) [Trees & Wells]: "The Court may also divide the trees, wells and other improvements existing on such holding but where such division is not possible, the trees, wells and other improvements aforesaid and valuation thereof shall be divided and adjusted in the manner prescribed." Assess if a strategy fairly divides or compensates for trees/wells.
4. Rule 109(g) [Mutual Consent]: If the co-tenure holders have a mutual consent or family settlement, the Kurra shall be fixed accordingly.
5. Road vs. River Priority: If both a road and a river are adjacent/nearby, road access must take absolute priority over river access. You MUST choose a strategy that guarantees road frontage/access to all co-sharers proportionately, even if it means some co-sharers do not get access to the river.

Choose the best strategy and provide a clear, concise paragraph explaining WHY it is the best choice for the farmers, citing these rules.
"""

    p_info    = parcel_info.get("parcel_info", {})
    user_pref = parcel_info.get("user_preferences", "").strip()

    user_prompt = f"""Parcel Details:
District: {p_info.get('district', 'Unknown')}
Circle/Tehsil: {p_info.get('circle', 'Unknown')}
Mouza/Village: {p_info.get('mouza', 'Unknown')}
Plot No: {p_info.get('plot_no', 'Unknown')}
Khata No: {p_info.get('khata_no', 'Unknown')}
Total Area: {parcel_info.get('area_sqm', 0):.1f} sqm
Partitions Requested: {len(parcel_info.get('shares', []))}
Requested Share Percentages: {parcel_info.get('shares')}

Surroundings Context:
Has Road Frontage: {parcel_info.get('has_frontage', False)} (Primary Road: {parcel_info.get('primary_road', 'None')})
Nearby Roads: {parcel_info.get('nearby_roads', [])}
Nearby River (Within 100m): {parcel_info.get('nearby_river', False)} (Primary River: {parcel_info.get('primary_river', 'None')})
Nearby Rivers: {parcel_info.get('nearby_rivers_list', [])}
Features Inside Plot: {parcel_info.get('features')}

Custom User Instructions/Preferences (Mutual Consent under Rule 109(g)):
{user_pref if user_pref else "None provided."}

Generated Mathematical Strategies:{str_text}

Analyze the strategies and select the best one based on the UP Revenue Code rules, the provided Surroundings Context, and the Custom User Instructions.
CRITICAL REQUIREMENT 1: The Custom User Instructions (Mutual Consent under Rule 109(g)) ALWAYS take absolute precedence over ALL other rules (including Road Access and Compactness). If a strategy fulfills the Custom Instructions better, you MUST choose it, even if it performs poorly on other rules.
CRITICAL REQUIREMENT 2: If your chosen strategy violates a standard rule (like Road Access or Compactness) in order to satisfy the Custom User Instructions, you MUST start your `explanation` paragraph with a clear "WARNING: This strategy compromises on Rule [X] to fulfill the custom mutual consent instructions."
CRITICAL REQUIREMENT 3: In your `explanation` paragraph, you MUST explicitly state the exact amount of Road Frontage AND River Frontage (in meters) that each sub-plot receives to justify your choice (e.g. "Both sub-plots receive exactly 12.5 meters of road frontage and 45.2 meters of river frontage...").
CRITICAL REQUIREMENT 4: If both a road and a river are adjacent/nearby, road access must take absolute priority over river access. You MUST choose a strategy that guarantees road frontage/access to all co-sharers proportionately, even if it means some co-sharers do not get access to the river.

Return JSON output with `recommended_strategy_index` (1-indexed integer) and `explanation`.
"""

    print(f"\n--- SENDING PROMPT TO LLM (provider={LLM_PROVIDER}) ---")
    print(user_prompt)
    print("------------------------------------------------------\n")

    # ------------------------------------------------------------------
    # Route to the correct backend
    # ------------------------------------------------------------------
    try:
        if LLM_PROVIDER == "gemini":
            raw = _call_gemini(system_prompt, user_prompt)
        elif LLM_PROVIDER == "grok":
            raw = _call_grok(system_prompt, user_prompt)
        elif LLM_PROVIDER == "local":
            raw = _call_local(system_prompt, user_prompt)
        else:
            return {
                "success": False,
                "error": (
                    f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. "
                    "Valid values: gemini, grok, local"
                ),
            }

        print("\n--- RECEIVED RESPONSE FROM LLM ---")
        print(raw)
        print("----------------------------------\n")

        result = _extract_json(raw)
        return {
            "success": True,
            "recommended_index": result.get("recommended_strategy_index", 1) - 1,  # 0-indexed
            "explanation": result.get("explanation", "The LLM provided a recommendation."),
            "provider": LLM_PROVIDER,
        }

    except Exception as exc:
        return {"success": False, "error": str(exc)}
