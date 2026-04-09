from fastapi import APIRouter, Query
from typing import Optional
import psycopg2
import os
from dotenv import load_dotenv
from routers.dss_helpers import write_dss_log  # ✅ FIXED

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

router = APIRouter(prefix="/search", tags=["Search"])


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


@router.get("/")
async def search_claims(
    q: Optional[str] = Query(None, description="General search query")
):
    conn = get_db_connection()
    cur = conn.cursor()

    base_query = "SELECT * FROM fra_documents WHERE 1=1"
    params = []

    if q:
        base_query += """ AND (
            patta_holder_name ILIKE %s OR
            village_name ILIKE %s OR
            district ILIKE %s OR
            state ILIKE %s OR
            claim_id ILIKE %s
        )"""
        like_q = f"%{q}%"
        params += [like_q] * 5

    cur.execute(base_query, params)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

    results = [dict(zip(columns, row)) for row in rows]

    # log DSS usage
    try:
        write_dss_log(
            user_query=q or "",
            parsed={"status": None, "state": None, "district": None},
            scheme_id=None,
            count=len(results),
            sample=results[:3],
        )
    except Exception as e:
        print("⚠️ DSS log failed:", e)

    cur.close()
    conn.close()
    return {"count": len(results), "results": results}
