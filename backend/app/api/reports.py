"""POST /api/reports/verify: recompute and compare the hashes of an uploaded report."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models import User
from app.reports.service import verify_upload
from app.security import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])
MAX_UPLOAD = 25 * 1024 * 1024


@router.post("/verify")
async def verify(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise AppError("The file is larger than 25 MB.", code="upload_too_large", status_code=413)
    return verify_upload(db, data, file.filename, user.username)
