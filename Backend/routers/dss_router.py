from fastapi import APIRouter, HTTPException, Query
from db import insert_scheme, get_scheme_by_name, fetch_schemes, write_dss_log
from services.scheme_service import find_eligible_people_by_scheme
from utils.llm_utils import parse_dss_query  # your LLM query parser

router = APIRouter(prefix="/dss", tags=["dss"])


@router.post("/schemes")
def create_scheme(payload: dict):
    name = payload.get("name")
    eligibility = payload.get("eligibility")
    if not name or not eligibility:
        raise HTTPException(status_code=400, detail="name and eligibility required")

    scheme_id = insert_scheme(name, payload.get("description", ""), eligibility)
    return {"id": scheme_id, "name": name}


@router.get("/schemes")
def list_schemes():
    return fetch_schemes()


@router.get("/check")
def dss_check(q: str = Query(..., description="Natural language query")):
    parsed = parse_dss_query(q)

    scheme_name = parsed.get("scheme")
    village = parsed.get("village")
    district = parsed.get("district")
    state = parsed.get("state")

    if not scheme_name:
        return {"status": "error", "message": "Could not extract scheme name from query"}

    scheme = get_scheme_by_name(scheme_name)
    if not scheme:
        return {"status": "error", "message": f"Scheme '{scheme_name}' not found"}

    try:
        results = find_eligible_people_by_scheme(
            scheme, village=village, district=district, state=state
        )
    except Exception as e:
        return {"status": "error", "message": f"Database error: {str(e)}"}

    return {
        "status": "ok",
        "scheme": scheme_name,
        "filters": parsed,
        "count": len(results),
        "results": results[:5]  # sample
    }
