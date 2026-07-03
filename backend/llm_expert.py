import os
import json
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# =============================================================================
# LLM EXPERT MODULE -- Google Gemini API Edition
# =============================================================================
# This module handles all AI/LLM interactions for the Kurra division engine.
# Unlike the local LLM version, this connects to Google's Gemini API using
# a GEMINI_API_KEY environment variable.
#
# REQUIREMENTS:
#   - Set the GEMINI_API_KEY environment variable (see INSTALL.md)
#   - Internet connectivity to https://generativelanguage.googleapis.com
#
# USAGE:
#   from llm_expert import consult_llm_for_division
#   result = consult_llm_for_division(parcel_info, strategies)
#
# FALLBACK:
#   If GEMINI_API_KEY is not set or the API call fails, the subdivide endpoint
#   automatically falls back to the first algorithmic strategy (Compact Cut).
# =============================================================================

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

def consult_llm_for_division(parcel_info, strategies):
    """
    Calls the Google Gemini API to evaluate subdivision strategies.
    Provides the UP Revenue Code Section 116/117 rules and the generated
    mathematical strategies, then returns the best recommended strategy.

    Args:
        parcel_info: dict containing parcel details, share percentages, and GIS context
        strategies: list of strategy dicts each with 'name', 'polys', 'sub_plot_stats'

    Returns:
        dict with keys:
          - success (bool)
          - recommended_index (int, 0-indexed) -- only if success is True
          - explanation (str) -- only if success is True
          - error (str) -- only if success is False
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "GEMINI_API_KEY environment variable is not set. Please set the GEMINI_API_KEY variable to use the AI Kurra division feature. See INSTALL.md for instructions."
        }

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
5. Road vs. River Priority: If both a road and a river are adjacent/nearby, road access must take absolute priority over river access. You MUST choose a strategy that guarantees road frontage/access to all co-sharers proportionately, even if it means some co-sharers do not get access to the river.

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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    payload = {
        "contents": [{
            "parts": [{
                "text": user_prompt
            }]
        }],
        "systemInstruction": {
            "parts": [{
                "text": system_prompt
            }]
        },
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "recommended_strategy_index": {
                        "type": "integer"
                    },
                    "explanation": {
                        "type": "string"
                    }
                },
                "required": ["recommended_strategy_index", "explanation"]
            },
            "temperature": 0.3
        }
    }

    print("\n--- SENDING PROMPT TO GEMINI API ---")
    print(user_prompt)
    print("------------------------------------\n")

    try:
        response = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)
        if response.status_code == 200:
            resp_json = response.json()
            content_text = resp_json['candidates'][0]['content']['parts'][0]['text']

            print("\n--- RECEIVED RESPONSE FROM GEMINI ---")
            print(content_text)
            print("-------------------------------------\n")

            result = json.loads(content_text)
            return {
                "success": True,
                "recommended_index": result.get("recommended_strategy_index", 1) - 1,  # 0-indexed
                "explanation": result.get("explanation", "The LLM provided a recommendation.")
            }
        else:
            return {
                "success": False,
                "error": f"Gemini API returned status {response.status_code}: {response.text}"
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
