import psycopg2
import os
import json
from datetime import datetime, date
from utils.env_utils import load_backend_env

load_backend_env()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def ensure_dss_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schemes (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            eligibility JSONB DEFAULT '{}'::jsonb
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dss_logs (
            id SERIAL PRIMARY KEY,
            user_query TEXT NOT NULL,
            parsed JSONB DEFAULT '{}'::jsonb,
            scheme_id INTEGER,
            result_count INTEGER DEFAULT 0,
            sample JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dss_documents (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            ingest_status TEXT DEFAULT 'uploaded',
            chunk_count INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            indexed_at TIMESTAMP
        )
        """
    )


# ---- DSS helper functions ----

def insert_scheme(name: str, description: str, eligibility: dict):
    """Insert a scheme into DB and return its id."""
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute(
        "INSERT INTO schemes (name, description, eligibility) VALUES (%s, %s, %s) RETURNING id",
        (name, description, json.dumps(eligibility)),
    )
    scheme_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return scheme_id


def get_scheme_by_name(name: str):
    """Fetch scheme details by name."""
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute(
        "SELECT id, name, description, eligibility FROM schemes WHERE name ILIKE %s",
        (name,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "description": row[2], "eligibility": row[3]}


def fetch_schemes():
    """Return all schemes."""
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute("SELECT id, name, description, eligibility FROM schemes")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "description": r[2], "eligibility": r[3]}
        for r in rows
    ]


# --- custom serializer ---
def _json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def write_dss_log(user_query: str, parsed: dict, scheme_id: int, count: int, sample: list):
    """Store DSS decision log for audit."""
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute(
        """
        INSERT INTO dss_logs (user_query, parsed, scheme_id, result_count, sample)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            user_query,
            json.dumps(parsed, default=_json_serializer),
            scheme_id,
            count,
            json.dumps(sample, default=_json_serializer),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def insert_dss_document(filename: str, stored_path: str, ingest_status: str = "uploaded") -> dict:
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute(
        """
        INSERT INTO dss_documents (filename, stored_path, ingest_status)
        VALUES (%s, %s, %s)
        RETURNING id, filename, stored_path, ingest_status, chunk_count, uploaded_at, indexed_at
        """,
        (filename, stored_path, ingest_status),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return {
        "id": row[0],
        "filename": row[1],
        "stored_path": row[2],
        "ingest_status": row[3],
        "chunk_count": row[4],
        "uploaded_at": row[5],
        "indexed_at": row[6],
    }


def update_dss_document_status(document_id: int, ingest_status: str, chunk_count: int = 0) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute(
        """
        UPDATE dss_documents
        SET ingest_status = %s,
            chunk_count = %s,
            indexed_at = CASE
                WHEN %s = 'indexed' THEN CURRENT_TIMESTAMP
                ELSE indexed_at
            END
        WHERE id = %s
        """,
        (ingest_status, chunk_count, ingest_status, document_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def list_dss_documents() -> list[dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    ensure_dss_schema(cur)
    cur.execute(
        """
        SELECT id, filename, stored_path, ingest_status, chunk_count, uploaded_at, indexed_at
        FROM dss_documents
        ORDER BY uploaded_at DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": row[0],
            "filename": row[1],
            "stored_path": row[2],
            "ingest_status": row[3],
            "chunk_count": row[4],
            "uploaded_at": row[5],
            "indexed_at": row[6],
        }
        for row in rows
    ]


def list_fra_applicants() -> list[dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, patta_holder_name, village_name, district, state, claim_id, claim_type, land_use, total_area_claimed, status
        FROM fra_documents
        ORDER BY patta_holder_name ASC NULLS LAST, id DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": row[0],
            "patta_holder_name": row[1],
            "village_name": row[2],
            "district": row[3],
            "state": row[4],
            "claim_id": row[5],
            "claim_type": row[6],
            "land_use": row[7],
            "total_area_claimed": row[8],
            "status": row[9],
        }
        for row in rows
    ]


def get_fra_applicant_by_id(doc_id: int) -> dict | None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, patta_holder_name, father_or_husband_name, age, gender, address,
               village_name, block, district, state, total_area_claimed, coordinates,
               land_use, claim_id, claim_type, date_of_application, water_bodies,
               forest_cover, homestead, status
        FROM fra_documents
        WHERE id = %s
        """,
        (doc_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "patta_holder_name": row[1],
        "father_or_husband_name": row[2],
        "age": row[3],
        "gender": row[4],
        "address": row[5],
        "village_name": row[6],
        "block": row[7],
        "district": row[8],
        "state": row[9],
        "total_area_claimed": row[10],
        "coordinates": row[11],
        "land_use": row[12],
        "claim_id": row[13],
        "claim_type": row[14],
        "date_of_application": row[15],
        "water_bodies": row[16],
        "forest_cover": row[17],
        "homestead": row[18],
        "status": row[19],
    }
