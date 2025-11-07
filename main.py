import os
from datetime import datetime, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import create_document, get_documents

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AttendanceIn(BaseModel):
    name: str = Field(..., description="Student full name")
    nisn: str = Field(..., description="Student ID (NISN)")
    major: str = Field(..., description="Major/Department")
    scanned_at: Optional[datetime] = Field(None, description="Timestamp when QR was scanned (UTC)")
    source: Optional[str] = Field("qr", description="Origin of record: qr or manual")


class AttendanceOut(AttendanceIn):
    id: Optional[str] = None


@app.get("/")
def read_root():
    return {"message": "Student Attendance API is running"}


@app.get("/config")
def get_config():
    """Expose non-sensitive config so frontend can adapt UI."""
    return {
        "sheets_webhook": bool(os.getenv("GOOGLE_SHEETS_WEBAPP_URL")),
    }


@app.post("/attendance", response_model=dict)
def create_attendance(payload: AttendanceIn):
    """Create an attendance record then forward to Google Sheets webhook if configured."""
    data = payload.model_dump()
    # Ensure timestamp
    scanned_at = data.get("scanned_at") or datetime.now(timezone.utc)
    if isinstance(scanned_at, str):
        try:
            scanned_at = datetime.fromisoformat(scanned_at)
        except Exception:
            scanned_at = datetime.now(timezone.utc)
    data["scanned_at"] = scanned_at

    # Persist to MongoDB
    try:
        inserted_id = create_document("studentattendance", data)
    except Exception as e:
        # If DB is not available, continue but note error
        inserted_id = None

    # Forward to Google Sheets (Apps Script Web App) if configured
    sheets_url = os.getenv("GOOGLE_SHEETS_WEBAPP_URL")
    forward_status = "skipped"
    if sheets_url:
        try:
            # Send as JSON
            r = requests.post(
                sheets_url,
                json={
                    "name": data["name"],
                    "nisn": data["nisn"],
                    "major": data["major"],
                    "scanned_at": scanned_at.isoformat(),
                    "source": data.get("source", "qr"),
                },
                timeout=10,
            )
            r.raise_for_status()
            forward_status = "ok"
        except Exception as e:
            forward_status = f"error: {str(e)[:120]}"

    return {
        "ok": True,
        "id": inserted_id,
        "forward_to_sheets": forward_status,
    }


@app.get("/attendance", response_model=List[dict])
def list_attendance(limit: int = 50):
    """List recent attendance records from the database (if available)."""
    try:
        docs = get_documents("studentattendance", {}, limit)
        # Convert ObjectId and datetime to strings
        result = []
        for d in docs:
            d["id"] = str(d.pop("_id", ""))
            if isinstance(d.get("scanned_at"), datetime):
                d["scanned_at"] = d["scanned_at"].isoformat()
            if isinstance(d.get("created_at"), datetime):
                d["created_at"] = d["created_at"].isoformat()
            if isinstance(d.get("updated_at"), datetime):
                d["updated_at"] = d["updated_at"].isoformat()
            result.append(d)
        # Show latest first by created_at if available
        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return result
    except Exception:
        # If DB not available, return empty list
        return []


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        # Try to import database module
        from database import db

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            # Try to list collections to verify connectivity
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]  # Show first 10 collections
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
