from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
import requests
import re
import hashlib
import json
import psycopg2.extras
from datetime import datetime, date
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


def apply_deterministic_jitter(coords: str, seed_string: str) -> str:
    """Adds a natural geographic spread (approx 1-3km) based on a seed string to separate overlapping fallback pins."""
    if not coords or not seed_string:
        return coords
    try:
        lat_str, lon_str = coords.split(",")
        lat, lon = float(lat_str), float(lon_str)
        hash_val = int(hashlib.md5(seed_string.encode()).hexdigest(), 16)
        lat_offset = ((hash_val % 1000) / 1000.0) * 0.04 - 0.02
        lon_offset = (((hash_val // 1000) % 1000) / 1000.0) * 0.04 - 0.02
        return f"{lat + lat_offset:.6f}, {lon + lon_offset:.6f}"
    except Exception:
        return coords


def ensure_coordinates(data: dict) -> dict:
    coords = str(data.get("Coordinates") or data.get("coordinates") or "").strip()
    if coords:
        data["Coordinates"] = coords
        data["coordinates"] = coords
        return data

    applicant = str(data.get("patta_holder_name") or data.get("Patta-Holder Name") or "").strip()
    village = str(data.get("Village Name") or data.get("village_name") or "").strip()
    district = str(data.get("District") or data.get("district") or "").strip()
    state = str(data.get("State") or data.get("state") or "").strip()
    
    coords = ""
    for candidate in build_address_candidates(data):
        coords = get_coordinates_from_address(candidate, village=village, district=district, state=state)
        if coords:
            # If Nominatim failed to find the village and fell back to the district center, try querying Gemini LLM
            if village and village.lower() not in candidate.lower() and district.lower() in candidate.lower():
                from utils.llm_utils import geocode_village_with_llm
                llm_coords = geocode_village_with_llm(village, district, state)
                if llm_coords:
                    coords = llm_coords
            break

    # Prevent perfect marker stacking by using the claimant's name and village to
    # deterministically map their unique physical plot within the area.
    seed = f"{village}_{applicant}"
    if coords:
        coords = apply_deterministic_jitter(coords, seed)

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
        ADD COLUMN IF NOT EXISTS claim_type TEXT,
        ADD COLUMN IF NOT EXISTS previous_hash TEXT,
        ADD COLUMN IF NOT EXISTS current_hash TEXT;
        """
    )
    # Create asset_data table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_data (
            id SERIAL PRIMARY KEY,
            fra_id INTEGER REFERENCES fra_documents(id),
            land_type TEXT,
            water_available BOOLEAN,
            irrigation BOOLEAN,
            confidence FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Create audit logs table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fra_audit_logs (
            id SERIAL PRIMARY KEY,
            doc_id INTEGER,
            editor TEXT,
            action TEXT,
            previous_data JSONB,
            new_data JSONB,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def build_insert_values(payload: DocumentPayload, previous_hash: str, current_hash: str):
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
        previous_hash,
        current_hash
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
            water_bodies, forest_cover, homestead, previous_hash, current_hash
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                ensure_schema(cur)
                
                cur.execute("SELECT current_hash FROM fra_documents ORDER BY id DESC LIMIT 1;")
                last_record = cur.fetchone()
                previous_hash = last_record[0] if (last_record and last_record[0]) else "0"
                
                name = normalized_payload.patta_holder_name or ""
                village = normalized_payload.village_name or ""
                claim_type = normalized_payload.claim_type or ""
                status = "pending"
                coordinates_val = normalized_payload.coordinates or ""
                
                data_to_hash = f"{name}{village}{claim_type}{status}{coordinates_val}{previous_hash}"
                current_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
                
                print(f"🔐 previous_hash: {previous_hash}")
                print(f"🔐 current_hash: {current_hash}")

                cur.execute(insert_query, build_insert_values(normalized_payload, previous_hash, current_hash))
                doc_id = cur.fetchone()[0]
                conn.commit()

                # --- 4. MAP LAND TYPE TO ASSET DATA & STORE ---
                try:
                    from routers.model_pred import get_asset_data
                    
                    if coordinates_val:
                        # Extract asset data automatically using CNN logic
                        land_type, confidence, water_available, irrigation = get_asset_data(coordinates_val)
                        
                        # Debug Prints
                        print(f"\n--- DEBUG: NEW ASSET DATA ---")
                        print(f"fra_id: {doc_id}")
                        print(f"land_type: {land_type}")
                        print(f"confidence: {confidence}")
                        print(f"water_available: {water_available}")
                        print(f"irrigation: {irrigation}")
                        print(f"-----------------------------\n")
                        
                        # Insert into asset_data
                        cur.execute(
                            """
                            INSERT INTO asset_data (fra_id, land_type, water_available, irrigation, confidence)
                            VALUES (%s, %s, %s, %s, %s);
                            """,
                            (doc_id, land_type, water_available, irrigation, confidence)
                        )
                        conn.commit()
                except Exception as e:
                    print(f"⚠️ Warning: Could not save asset data. Error: {e}")
                # ----------------------------------------------

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


# ✅ Enhanced Forensic route: Fetch and confirm blockchain
@router.get("/verify-chain")
async def verify_blockchain():
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                ensure_schema(cur)
                cur.execute("SELECT * FROM fra_documents ORDER BY id ASC;")
                rows = cur.fetchall()
                
                if not rows:
                    return {"status": "safe", "message": "Blockchain is secure"}

                last_curr = "0"
                for r in rows:
                    rec_id = r['id']
                    prev_hash = r['previous_hash']
                    curr_hash = r['current_hash']
                    
                    # Also recalculate expected to see if tampered
                    name = r['patta_holder_name'] or ""
                    village = r['village_name'] or ""
                    claim_type = r['claim_type'] or ""
                    status = r.get('status', 'pending') or "pending"
                    coords = r['coordinates'] or ""
                    
                    expected_hash = hashlib.sha256(f"{name}{village}{claim_type}{status}{coords}{last_curr}".encode()).hexdigest()
                    
                    if prev_hash != last_curr or curr_hash != expected_hash:
                        # Chain logic broken OR data directly tampered
                        cur.execute("SELECT editor, previous_data, new_data, timestamp FROM fra_audit_logs WHERE doc_id = %s ORDER BY timestamp DESC LIMIT 1;", (rec_id,))
                        audit = cur.fetchone()
                        
                        return {
                            "status": "error", 
                            "message": f"Chain mathematically broken at record id {rec_id}.",
                            "broken_record_id": rec_id,
                            "expected_hash": expected_hash,
                            "actual_hash": curr_hash,
                            "last_audit_log": audit if audit else "No authorized UI edits recorded for this document. Likely direct database manipulation."
                        }
                    last_curr = curr_hash
                    
        return {"status": "safe", "message": "Blockchain is mathematically secure"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
async def delete_document(doc_id: int, request: Request):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # FIRST: Archive the state before deleting so we maintain perfect audit persistence!
                cur.execute("SELECT * FROM fra_documents WHERE id = %s", (doc_id,))
                doc_to_delete = cur.fetchone()
                
                if not doc_to_delete:
                    raise HTTPException(status_code=404, detail="Document not found")
                
                # Fetch IP / Pseudo-user
                editor = request.headers.get("x-user-id", request.client.host)
                
                # Log the destructive action
                cur.execute(
                    """
                    INSERT INTO fra_audit_logs (doc_id, editor, action, previous_data, new_data)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (doc_id, editor, "DELETE", json.dumps(doc_to_delete, default=str), json.dumps({"status": "DELETED"}, default=str))
                )

                cur.execute("DELETE FROM fra_documents WHERE id = %s RETURNING id;", (doc_id,))
                deleted = cur.fetchone()

                # REPAIR CHAIN: A deletion severs a blockchain link. We must ripple forward to reconnect.
                cur.execute("SELECT id, patta_holder_name, village_name, claim_type, status, coordinates FROM fra_documents ORDER BY id ASC;")
                all_docs = cur.fetchall()
                
                last_hash = "0"
                for doc in all_docs:
                    name = doc['patta_holder_name'] or ""
                    village = doc['village_name'] or ""
                    claim_type = doc['claim_type'] or ""
                    status = doc.get('status') or "pending"
                    coords = doc['coordinates'] or ""
                    
                    data_to_hash = f"{name}{village}{claim_type}{status}{coords}{last_hash}"
                    current_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
                    
                    cur.execute("UPDATE fra_documents SET previous_hash=%s, current_hash=%s WHERE id=%s", (last_hash, current_hash, doc['id']))
                    last_hash = current_hash
                
                conn.commit()

        return {"status": "success", "doc_id": deleted['id']}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _dserializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)

@router.put("/{doc_id}")
async def update_document(doc_id: int, payload: DocumentPayload, request: Request):
    try:
        validated_payload = normalize_and_validate_payload(payload)
        data = ensure_coordinates(validated_payload.model_dump())
        normalized_payload = DocumentPayload(**data)
        
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                ensure_schema(cur)
                
                # 1. Fetch existing record to audit log
                cur.execute("SELECT * FROM fra_documents WHERE id = %s;", (doc_id,))
                old_record = cur.fetchone()
                if not old_record:
                    raise HTTPException(status_code=404, detail="Document not found")

                editor_id = request.client.host if request and request.client else "Unknown IP"
                editor_id = f"IP: {editor_id} (Unauthenticated Proxy)" # Prepare for Role-Based Login

                cur.execute(
                    """
                    INSERT INTO fra_audit_logs (doc_id, editor, action, previous_data, new_data)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (doc_id, editor_id, "EDIT", json.dumps(old_record, default=_dserializer), json.dumps(normalized_payload.model_dump(), default=_dserializer))
                )
                
                # 2. Update the record
                cur.execute("""
                    UPDATE fra_documents SET
                    patta_holder_name=%s, father_or_husband_name=%s, age=%s, gender=%s, address=%s,
                    village_name=%s, block=%s, district=%s, state=%s, total_area_claimed=%s,
                    coordinates=%s, land_use=%s, claim_id=%s, claim_type=%s, date_of_application=%s,
                    water_bodies=%s, forest_cover=%s, homestead=%s
                    WHERE id=%s
                """, build_update_values(normalized_payload, doc_id))
                
                # 3. Recalculate chain strictly forward
                cur.execute("SELECT id, patta_holder_name, village_name, claim_type, status, coordinates FROM fra_documents ORDER BY id ASC;")
                all_docs = cur.fetchall()
                
                last_hash = "0"
                for doc in all_docs:
                    name = doc['patta_holder_name'] or ""
                    village = doc['village_name'] or ""
                    claim_type = doc['claim_type'] or ""
                    status = doc.get('status') or "pending"
                    coords = doc['coordinates'] or ""
                    
                    data_to_hash = f"{name}{village}{claim_type}{status}{coords}{last_hash}"
                    current_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
                    
                    cur.execute("UPDATE fra_documents SET previous_hash=%s, current_hash=%s WHERE id=%s", (last_hash, current_hash, doc['id']))
                    last_hash = current_hash
                
                conn.commit()
                
        return {"status": "success", "message": "Record updated and blockchain recalculated securely", "doc_id": doc_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{doc_id}/audit-history")
async def get_audit_history(doc_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM fra_audit_logs WHERE doc_id = %s ORDER BY timestamp DESC", (doc_id,))
                logs = cur.fetchall()
        return {"status": "success", "data": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{doc_id}/assets")
async def get_document_assets(doc_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM asset_data WHERE fra_id = %s ORDER BY created_at DESC LIMIT 1", (doc_id,))
                asset = cur.fetchone()
        if not asset:
            return {"status": "success", "data": None, "message": "No machine learning asset data found for this claim."}
        return {"status": "success", "data": asset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
