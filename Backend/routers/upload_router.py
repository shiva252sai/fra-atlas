from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
import requests
import re
from db import get_db_connection
from utils.ocr_utils import extract_text_from_file
from utils.llm_utils import clean_with_llm  # with regex fallback

router = APIRouter(prefix="/upload", tags=["upload"])


class DocumentPayload(BaseModel):
    patta_holder_name: str = ""
    father_or_husband_name: str = ""
    age: str = ""
    gender: str = ""
    address: str = ""
    village_name: str = ""
    block: str = ""
    district: str = ""
    state: str = ""
    total_area_claimed: str = ""
    coordinates: str = ""
    land_use: str = ""
    claim_id: str = ""
    claim_type: str = ""
    date_of_application: str = ""
    water_bodies: str = ""
    forest_cover: str = ""
    homestead: str = ""


REQUIRED_FIELDS = {
    "claim_id": "Claim ID",
    "patta_holder_name": "Applicant / Patta Holder",
    "village_name": "Village Name",
    "district": "District",
    "state": "State",
    "claim_type": "Claim Type",
    "total_area_claimed": "Total Area Claimed",
    "land_use": "Land Use",
    "date_of_application": "Date of Application",
}

ALLOWED_CLAIM_TYPES = {"IFR", "CR", "CFR"}


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def score_geocoding_result(result: dict, village: str, district: str, state: str) -> int:
    display_name = normalize_text(result.get("display_name", ""))
    result_name = normalize_text(result.get("name", ""))
    result_type = str(result.get("type", "")).lower()
    address = result.get("address") or {}
    address_values = " ".join(normalize_text(value) for value in address.values())

    village_text = normalize_text(village)
    district_text = normalize_text(district)
    state_text = normalize_text(state)

    score = 0

    if village_text:
        if result_name == village_text:
            score += 10
        elif village_text in result_name or village_text in display_name:
            score += 6

    if district_text:
        if district_text in address_values:
            score += 8
        elif district_text in display_name:
            score += 5

    if state_text:
        if state_text in address_values:
            score += 8
        elif state_text in display_name:
            score += 5

    if "india" in display_name:
        score += 2

    if result_type in {"village", "hamlet", "isolated_dwelling", "suburb"}:
        score += 4
    elif result_type in {"administrative", "residential"}:
        score += 1

    importance = result.get("importance")
    if isinstance(importance, (int, float)):
        score += min(int(importance * 10), 3)

    return score


def get_coordinates_from_address(address: str, village: str = "", district: str = "", state: str = ""):
    """
    Get coordinates using OpenStreetMap Nominatim API.
    Returns (lat, lon) or "" if not found.
    """
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": address,
            "format": "jsonv2",
            "addressdetails": 1,
            "countrycodes": "in",
            "limit": 5,
        }
        headers = {"User-Agent": "fra-doc-system"}  # required by Nominatim
        response = requests.get(url, params=params, headers=headers, timeout=10)

        if response.status_code == 200:
            results = response.json()
            if results:
                best_match = max(
                    results,
                    key=lambda item: score_geocoding_result(item, village, district, state),
                )
                lat = best_match["lat"]
                lon = best_match["lon"]
                return f"{lat}, {lon}"
    except Exception as e:
        print(f"Coordinate fetch error: {e}")
    return ""


def build_address_candidates(data: dict) -> list[str]:
    address = str(data.get("Address") or data.get("address") or "").strip()
    village = str(data.get("Village Name") or data.get("village_name") or "").strip()
    block = str(data.get("Block") or data.get("block") or "").strip()
    district = str(data.get("District") or data.get("district") or "").strip()
    state = str(data.get("State") or data.get("state") or "").strip()

    candidates = [
        ", ".join(part for part in [village, district, state, "India"] if part),
        ", ".join(part for part in [village, block, district, state, "India"] if part),
        ", ".join(part for part in [address, village, district, state, "India"] if part),
        ", ".join(part for part in [district, state, "India"] if part),
    ]

    seen = set()
    return [candidate for candidate in candidates if candidate and not (candidate in seen or seen.add(candidate))]


def ensure_coordinates(data: dict) -> dict:
    coords = str(data.get("Coordinates") or data.get("coordinates") or "").strip()
    if coords:
        data["Coordinates"] = coords
        data["coordinates"] = coords
        return data

    village = str(data.get("Village Name") or data.get("village_name") or "").strip()
    district = str(data.get("District") or data.get("district") or "").strip()
    state = str(data.get("State") or data.get("state") or "").strip()
    coords = ""
    for candidate in build_address_candidates(data):
        coords = get_coordinates_from_address(candidate, village=village, district=district, state=state)
        if coords:
            break

    data["Coordinates"] = coords
    data["coordinates"] = coords
    return data


def normalize_and_validate_payload(payload: DocumentPayload) -> DocumentPayload:
    raw = payload.model_dump()
    raw["claim_type"] = str(raw.get("claim_type", "")).strip().upper()

    missing = [
        label
        for field, label in REQUIRED_FIELDS.items()
        if not str(raw.get(field, "")).strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Required fields missing: {', '.join(missing)}",
        )

    if raw["claim_type"] not in ALLOWED_CLAIM_TYPES:
        raise HTTPException(status_code=400, detail="Claim Type must be IFR, CR, or CFR")

    return DocumentPayload(**raw)


def ensure_schema(cur) -> None:
    cur.execute(
        """
        ALTER TABLE fra_documents
        ADD COLUMN IF NOT EXISTS claim_type TEXT;
        """
    )


def build_insert_values(payload: DocumentPayload):
    return (
        payload.patta_holder_name,
        payload.father_or_husband_name,
        payload.age,
        payload.gender,
        payload.address,
        payload.village_name,
        payload.block,
        payload.district,
        payload.state,
        payload.total_area_claimed,
        payload.coordinates,
        payload.land_use,
        payload.claim_id,
        payload.claim_type,
        payload.date_of_application,
        payload.water_bodies,
        payload.forest_cover,
        payload.homestead,
    )


def build_update_values(payload: DocumentPayload, doc_id: int):
    return (
        payload.patta_holder_name,
        payload.father_or_husband_name,
        payload.age,
        payload.gender,
        payload.address,
        payload.village_name,
        payload.block,
        payload.district,
        payload.state,
        payload.total_area_claimed,
        payload.coordinates,
        payload.land_use,
        payload.claim_id,
        payload.claim_type,
        payload.date_of_application,
        payload.water_bodies,
        payload.forest_cover,
        payload.homestead,
        doc_id,
    )


@router.post("/")
async def upload_document(file: UploadFile = File(...)):
    try:
        # 1. Read file
        file_bytes = await file.read()

        # 2. Extract OCR text
        ocr_text = extract_text_from_file(file_bytes)
        print("OCR Output:", ocr_text)

        # 3. Clean + Structure text using LLM
        data = clean_with_llm(ocr_text)
        if "error" in data:
            raise HTTPException(status_code=500, detail=data["error"])

        return {"status": "success", "data": data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm")
async def confirm_document(payload: DocumentPayload):
    try:
        validated_payload = normalize_and_validate_payload(payload)
        data = ensure_coordinates(validated_payload.model_dump())
        normalized_payload = DocumentPayload(**data)

        insert_query = """
        INSERT INTO fra_documents (
            patta_holder_name, father_or_husband_name, age, gender, address,
            village_name, block, district, state, total_area_claimed,
            coordinates, land_use, claim_id, claim_type, date_of_application,
            water_bodies, forest_cover, homestead
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                ensure_schema(cur)
                cur.execute(insert_query, build_insert_values(normalized_payload))
                doc_id = cur.fetchone()[0]
                conn.commit()

        return {
            "status": "success",
            "doc_id": doc_id,
            "data": normalized_payload.model_dump(),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
async def preview_document(payload: DocumentPayload):
    try:
        preview_payload = DocumentPayload(**ensure_coordinates(payload.model_dump()))
        return {
            "status": "success",
            "data": preview_payload.model_dump(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ✅ New route: Fetch all FRA documents
@router.get("/all")
async def get_all_documents():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM fra_documents ORDER BY created_at DESC;")
                rows = cur.fetchall()
                colnames = [desc[0] for desc in cur.description]

        results = [dict(zip(colnames, row)) for row in rows]
        return {"status": "success", "count": len(results), "results": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{doc_id}")
async def delete_document(doc_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM fra_documents WHERE id = %s RETURNING id;", (doc_id,))
                deleted = cur.fetchone()
                conn.commit()

        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found")

        return {"status": "success", "doc_id": deleted[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{doc_id}")
async def update_document(doc_id: int, payload: DocumentPayload):
    try:
        validated_payload = normalize_and_validate_payload(payload)

        update_query = """
        UPDATE fra_documents
        SET
            patta_holder_name = %s,
            father_or_husband_name = %s,
            age = %s,
            gender = %s,
            address = %s,
            village_name = %s,
            block = %s,
            district = %s,
            state = %s,
            total_area_claimed = %s,
            coordinates = %s,
            land_use = %s,
            claim_id = %s,
            claim_type = %s,
            date_of_application = %s,
            water_bodies = %s,
            forest_cover = %s,
            homestead = %s
        WHERE id = %s
        RETURNING id;
        """

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                ensure_schema(cur)
                cur.execute(update_query, build_update_values(validated_payload, doc_id))
                updated = cur.fetchone()
                conn.commit()

        if not updated:
            raise HTTPException(status_code=404, detail="Document not found")

        return {
            "status": "success",
            "doc_id": updated[0],
            "data": validated_payload.model_dump(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
