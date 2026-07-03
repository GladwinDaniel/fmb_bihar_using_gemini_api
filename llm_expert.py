import os
import json
import requests
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-2-latest")
GROK_API_URL = os.environ.get("GROK_API_URL", "https://api.x.ai/v1/chat/completions")


def _extract_json_object(text):
    if not text:
        return None

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)

    try:
        return json.loads(candidate)
    except Exception:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = candidate[start:end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


def _parse_llm_decision(content_text):
    parsed = _extract_json_object(content_text)
    if not parsed:
        return None
    return {
        "recommended_index": int(parsed.get("recommended_strategy_index", 1)) - 1,
        "explanation": parsed.get("explanation", "The LLM provided a recommendation.")
    }


def _call_gemini(system_prompt, user_prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "GEMINI_API_KEY is not set. Set GEMINI_API_KEY or switch provider via LLM_PROVIDER=grok."
        }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "recommended_strategy_index": {"type": "integer"},
                    "explanation": {"type": "string"}
                },
                "required": ["recommended_strategy_index", "explanation"]
            },
            "temperature": 0.3
        }
    }

    try:
        response = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)
        if response.status_code != 200:
            return {"success": False, "error": f"Gemini API returned status {response.status_code}: {response.text}"}

        resp_json = response.json()
        content_text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _parse_llm_decision(content_text)
        if not parsed:
            return {"success": False, "error": "Gemini response could not be parsed as decision JSON."}

        return {"success": True, "recommended_index": parsed["recommended_index"], "explanation": parsed["explanation"]}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _call_grok(system_prompt, user_prompt):
    api_key = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "GROK_API_KEY (or XAI_API_KEY) is not set. Set it or switch provider via LLM_PROVIDER=gemini."
        }

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(GROK_API_URL, json=payload, headers=headers, timeout=45)
        if response.status_code != 200:
            return {"success": False, "error": f"Grok API returned status {response.status_code}: {response.text}"}

        resp_json = response.json()
        content_text = resp_json["choices"][0]["message"]["content"]
        parsed = _parse_llm_decision(content_text)
        if not parsed:
            return {"success": False, "error": "Grok response could not be parsed as decision JSON."}

        return {"success": True, "recommended_index": parsed["recommended_index"], "explanation": parsed["explanation"]}
    except Exception as e:
        return {"success": False, "error": str(e)}

def consult_llm_for_division(parcel_info, strategies):
    """Calls configured provider (Gemini or Grok) and returns recommendation."""

    # Format strategies text
    str_text = ""
    for i, strat in enumerate(strategies):  
        str_text += f"\nStrategy {i+1}: {strat['name']}\n"
        stats = strat.get("sub_plot_stats", [])
        for j, poly in enumerate(strat['polys']):
            frontage = stats[j]["frontage_m"] if j < len(stats) else 0
            river_frontage = stats[j].get("river_frontage_m", 0) if j < len(stats) else 0
            feats = stats[j]["features"] if j < len(stats) else 0
            str_text += f"  - Sub-Plot {j+1}: Area = {poly.area:.1f} sqm, Perimeter = {poly.length:.1f} m, Road Frontage = {frontage:.1f} m, River Frontage = {river_frontage:.1f} m, Features Inside = {feats}\n"

    system_prompt = """You are an expert Indian land revenue officer and surveyor.
Your task is to review mathematical subdivision strategies for a land parcel (Kurra division) and explain which strategy is best according to the UP Revenue Code, 2006, Section 116 and 117.

Key Legal Rules you MUST enforce:
1. Rule 109(f) [Road Access/Commercial Value]: "If the plot or any part thereof is of commercial value or is adjacent to road, abadi or any other land of commercial value, the same shall be allotted to each tenure holder proportionately..." You must heavily penalize strategies that landlock a plot or deny proportional road access. Land adjacent to a road is significantly more valuable.
2. Rule 109(b) [Compactness]: "The portion allotted to each party shall be as compact as possible."
3. Section 116(2) [Trees & Wells]: "The Court may also divide the trees, wells and other improvements existing on such holding but where such division is not possible, the trees, wells and other improvements aforesaid and valuation thereof shall be divided and adjusted in the manner prescribed." Assess if a strategy fairly divides or compensates for trees/wells.
4. Rule 109(g) [Mutual Consent]: If the co-tenure holders have a mutual consent or family settlement, the Kurra shall be fixed accordingly.

Choose the best strategy and provide a clear, concise paragraph explaining WHY it is the best choice for the farmers, citing these rules.
"""

    p_info = parcel_info.get("parcel_info", {})
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
Has Road Frontage: {len(parcel_info.get('road_frontage', [])) > 0}
Nearby River (Within 100m): {parcel_info.get('nearby_river', False)}
Features Inside Plot: {parcel_info.get('features')}

Custom User Instructions/Preferences (Mutual Consent under Rule 109(g)):
{user_pref if user_pref else "None provided."}

Generated Mathematical Strategies:{str_text}

Analyze the strategies and select the best one based on the UP Revenue Code rules, the provided Surroundings Context, and the Custom User Instructions. 
CRITICAL REQUIREMENT 1: The Custom User Instructions (Mutual Consent under Rule 109(g)) ALWAYS take absolute precedence over ALL other rules (including Road Access and Compactness). If a strategy fulfills the Custom Instructions better, you MUST choose it, even if it performs poorly on other rules.
CRITICAL REQUIREMENT 2: If your chosen strategy violates a standard rule (like Road Access or Compactness) in order to satisfy the Custom User Instructions, you MUST start your `explanation` paragraph with a clear "WARNING: This strategy compromises on Rule [X] to fulfill the custom mutual consent instructions."
CRITICAL REQUIREMENT 3: In your `explanation` paragraph, you MUST explicitly state the exact amount of Road Frontage AND River Frontage (in meters) that each sub-plot receives to justify your choice (e.g. "Both sub-plots receive exactly 12.5 meters of road frontage and 45.2 meters of river frontage...").
"""

    print(f"\n--- SENDING PROMPT TO {LLM_PROVIDER.upper()} ---")
    print(user_prompt)
    print("----------------------------------------------\n")

    if LLM_PROVIDER == "grok":
        return _call_grok(system_prompt, user_prompt)
    return _call_gemini(system_prompt, user_prompt)
