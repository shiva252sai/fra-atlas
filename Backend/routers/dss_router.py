from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from db import insert_scheme, get_scheme_by_name, fetch_schemes, list_dss_documents
from services.scheme_service import find_eligible_people_by_scheme
from services.rag_service import (
    get_available_applicants,
    ingest_uploaded_pdf,
    run_applicant_dss_query,
    run_hybrid_dss_query,
)
from utils.llm_utils import parse_dss_query  # your LLM query parser

router = APIRouter(prefix="/dss", tags=["dss"])


class AssistantQueryPayload(BaseModel):
    query: str
    village: str | None = None
    district: str | None = None
    state: str | None = None
    top_k: int = 4


class ApplicantRecommendationPayload(BaseModel):
    note: str | None = None
    top_k: int = 6


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


@router.get("/documents")
def get_dss_documents():
    return {"status": "ok", "documents": list_dss_documents()}


@router.get("/applicants")
def get_applicants():
    return {"status": "ok", "applicants": get_available_applicants()}


@router.post("/documents")
async def upload_dss_documents(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF is required.")

    results = []
    for file in files:
        filename = file.filename or "document.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{file.filename} is not a PDF.")
        file_bytes = await file.read()
        results.append(ingest_uploaded_pdf(filename, file_bytes))

    return {"status": "ok", "documents": results}


@router.post("/assistant")
def dss_assistant(payload: AssistantQueryPayload):
    return run_hybrid_dss_query(
        query=payload.query,
        village=payload.village,
        district=payload.district,
        state=payload.state,
        top_k=payload.top_k,
    )


@router.post("/applicants/{applicant_id}/recommendations")
def dss_applicant_recommendations(applicant_id: int, payload: ApplicantRecommendationPayload | None = None):
    response = run_applicant_dss_query(
        applicant_id=applicant_id,
        extra_prompt=payload.note if payload else None,
        top_k=payload.top_k if payload else 6,
    )
    if response.get("status") == "error":
        raise HTTPException(status_code=404, detail=response.get("message"))
    return response


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
