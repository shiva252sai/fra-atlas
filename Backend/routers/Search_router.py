from typing import Optional
import os

import psycopg2
from fastapi import APIRouter, Query

from routers.dss_helpers import write_dss_log
from utils.env_utils import load_backend_env

load_backend_env()
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

    try:
        write_dss_log(
            user_query=q or "",
            parsed={"status": None, "state": None, "district": None},
            scheme_id=None,
            count=len(results),
            sample=results[:3],
        )
    except Exception as exc:
        print("DSS log failed:", exc)

    cur.close()
    conn.close()
    return {"count": len(results), "results": results}
