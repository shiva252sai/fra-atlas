import json
import re
import os
import requests
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
# Added imports for DSS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from db import fetch_schemes
from typing import Dict, Any

# -------------------------
# Load API Keys
# -------------------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0,
    google_api_key=GEMINI_API_KEY
)

# -------------------------
# Conversion factors to acres
# -------------------------
UNIT_TO_ACRE = {
    "acre": 1.0, "acres": 1.0,
    "hectare": 2.47105, "hectares": 2.47105, "ha": 2.47105,
    "sq m": 0.000247105, "sqm": 0.000247105,
    "square meter": 0.000247105, "square meters": 0.000247105,
    "sq ft": 2.2957e-5, "sqft": 2.2957e-5, "square feet": 2.2957e-5,
    "bigha": 0.619, "cent": 0.0247, "guntha": 0.0247
}

# -------------------------
# Prompt Template (OCR → JSON Schema)
# -------------------------
SCHEMA_PROMPT = """
You are an assistant that extracts structured data from OCR text.

Rules:
1. Extract only values present in text.
2. No guessing.
3. Fix spelling mistakes.
4. Missing fields = "".
5. Return valid JSON only.

JSON Format:
{{
    "Patta-Holder Name": "",
    "Father/Husband Name": "",
    "Age": "",
    "Gender": "",
    "Address": "",
    "Village Name": "",
    "Block": "",
    "District": "",
    "State": "",
    "Total Area Claimed": "",
    "Coordinates": "",
    "Land Use": "",
    "Claim ID": "",
    "Date of Application": "",
    "Water bodies": "",
    "Forest cover": "",
    "Homestead": ""
}}

OCR Text:
{ocr_text}
"""
prompt = PromptTemplate.from_template(SCHEMA_PROMPT)
chain = prompt | llm

# -------------------------
# JSON Cleaning
# -------------------------
def clean_llm_output(raw_text: str) -> str:
    first = raw_text.find("{")
    last = raw_text.rfind("}")
    if first == -1 or last == -1:
        return raw_text
    raw_text = raw_text[first:last+1]
    raw_text = raw_text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    raw_text = raw_text.replace("'", '"')
    raw_text = re.sub(r',\s*([}\]])', r'\1', raw_text)
    return raw_text

def safe_json_parse(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        cleaned = clean_llm_output(text)
        try:
            return json.loads(cleaned)
        except Exception:
            return {"raw_text": text, "error": "LLM JSON parse failed"}

# -------------------------
# Regex Fallback
# -------------------------
def fallback_extract(data: dict, text: str) -> dict:
    patterns = {
        "Patta-Holder Name": r"Patta[- ]Holder Name[:\-]?\s*(.+)",
        "Father/Husband Name": r"(Father|Husband) Name[:\-]?\s*(.+)",
        "Age": r"Age[:\-]?\s*(\d+)",
        "Gender": r"Gender[:\-]?\s*(Male|Female|Other)",
        "Address": r"Address[:\-]?\s*(.+)",
        "Village Name": r"Village Name[:\-]?\s*(.+)",
        "Block": r"Block[:\-]?\s*(.+)",
        "District": r"District[:\-]?\s*(.+)",
        "State": r"State[:\-]?\s*(.+)",
        "Total Area Claimed": r"Total Area Claimed[:\-]?\s*([\d\.]+.*)",
        "Coordinates": r"Coordinates[:\-]?\s*(.+)",
        "Land Use": r"Land Use[:\-]?\s*(.+)",
        "Claim ID": r"Claim ID[:\-]?\s*(.+)",
        "Date of Application": r"Date of Application[:\-]?\s*(.+)",
        "Water bodies": r"Water bodies[:\-]?\s*(.+)",
        "Forest cover": r"Forest cover[:\-]?\s*(.+)",
        "Homestead": r"Homestead[:\-]?\s*(.+)"
    }
    for field, pattern in patterns.items():
        if not data.get(field) or data.get(field) == "":
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data[field] = match.group(1).strip()
    return data

# -------------------------
# Area Conversion
# -------------------------
def convert_area_to_acres(area_str: str) -> str:
    if not area_str:
        return ""
    match = re.search(r"([\d\.]+)\s*([a-zA-Z ]+)?", area_str)
    if not match:
        return area_str
    value = float(match.group(1))
    unit = (match.group(2) or "acre").strip().lower()
    for key, factor in UNIT_TO_ACRE.items():
        if key in unit:
            acres = value * factor
            return f"{acres:.2f} acres"
    return f"{value:.2f} acres"

# -------------------------
# Coordinate Helpers
# -------------------------
def is_valid_coordinates(coords: str) -> bool:
    if not coords:
        return False
    return bool(re.match(r"^-?\d+\.\d+,\s*-?\d+\.\d+$", coords))

def fetch_coordinates_from_address(address: str) -> str:
    if not address.strip():
        return ""
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "FRA-System/1.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        print("🔍 Geocoding request:", resp.url)
        data = resp.json()
        print("📍 Geocoding response:", data)
        if data:
            return f"{data[0]['lat']}, {data[0]['lon']}"
    except Exception as e:
        print("Geocoding error:", e)
    return ""

# -------------------------
# Main Cleaning
# -------------------------
def clean_with_llm(text: str) -> dict:
    response = chain.invoke({"ocr_text": text})
    data = safe_json_parse(response.content)
    data = fallback_extract(data, text)

    if "Total Area Claimed" in data and data["Total Area Claimed"]:
        data["Total Area Claimed"] = convert_area_to_acres(data["Total Area Claimed"])

    # Try coordinates
    coords = data.get("Coordinates", "").strip()
    if not is_valid_coordinates(coords):
        # Build full address
        full_address = ", ".join(
            filter(None, [
                data.get("Address", ""),
                data.get("Village Name", ""),
                data.get("Block", ""),
                data.get("District", ""),
                data.get("State", ""),
                "India"
            ])
        )
        print("📌 Full Address for geocoding:", full_address)
        new_coords = fetch_coordinates_from_address(full_address)

        # If still empty, try District+State
        if not new_coords and data.get("District") and data.get("State"):
            alt_address = f"{data['District']}, {data['State']}, India"
            new_coords = fetch_coordinates_from_address(alt_address)

        # If still empty, try pincode inside Address
        if not new_coords:
            pincode_match = re.search(r"\b\d{6}\b", full_address)
            if pincode_match:
                new_coords = fetch_coordinates_from_address(pincode_match.group(0) + ", India")

        if new_coords:
            data["Coordinates"] = new_coords

    return data

# -------------------------
# DSS Query Parsing
# -------------------------
DSS_PROMPT = """
You are an assistant that extracts structured filters from a natural language question
about government scheme eligibility.

Rules:
1. Extract scheme, village, district, state (if available).
2. If any field is missing, return null.
3. Output ONLY valid JSON. No explanation, no markdown.

Example:
Question: Who is eligible for Farm Support Scheme in Bhimganga?
Answer:
{
  "scheme": "Farm Support Scheme",
  "village": "Bhimganga",
  "district": null,
  "state": null
}

Question: List all people in Mandla eligible for Old Age Pension.
Answer:
{
  "scheme": "Old Age Pension",
  "village": "Mandla",
  "district": null,
  "state": null
}
"""


dss_prompt = PromptTemplate.from_template(DSS_PROMPT)
dss_chain: Runnable = dss_prompt | llm | StrOutputParser()

def parse_dss_query(user_query: str) -> Dict[str, Any]:
    result = {"scheme": None, "village": None, "district": None, "state": None}

    try:
        llm_out = dss_chain.invoke(user_query)
        parsed = json.loads(llm_out)

        for key in result.keys():
            if key in parsed:
                result[key] = parsed[key]

    except Exception as e:
        print("⚠️ LLM parse failed, fallback:", e)

        # Regex fallback for village
        m = re.search(r"in ([A-Za-z]+)", user_query)
        if m:
            result["village"] = m.group(1)

        # Match scheme from DB
        schemes = fetch_schemes()
        for s in schemes:
            if s["name"].lower() in user_query.lower():
                result["scheme"] = s["name"]
                break

    return result
