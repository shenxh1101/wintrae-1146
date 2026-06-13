from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole
from schemas import VerificationResult
from services.verification_service import VerificationRecordService
from auth import get_current_user, require_roles

router = APIRouter(prefix="/records", tags=["查验记录"])


@router.get("/invoice/{invoice_id}", response_model=List[VerificationResult])
async def get_invoice_records(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    records = VerificationRecordService.get_invoice_records(db, invoice_id)
    
    return [
        VerificationResult(
            is_valid=record.status == "verified",
            status=record.status,
            message=record.result or "",
            exception_reason=record.error_message
        )
        for record in records
    ]


@router.get("/my", response_model=List[dict])
async def get_my_records(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    records = VerificationRecordService.get_user_records(db, current_user.id, skip, limit)
    
    return [
        {
            "id": record.id,
            "invoice_id": record.invoice_id,
            "action": record.action,
            "status": record.status,
            "result": record.result,
            "created_at": record.created_at
        }
        for record in records
    ]


@router.get("/all", response_model=List[dict])
async def get_all_records(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCE, UserRole.AUDITOR)),
    db: Session = Depends(get_db)
):
    records = db.query(VerificationRecord).order_by(
        VerificationRecord.created_at.desc()
    ).offset(skip).limit(limit).all()
    
    return [
        {
            "id": record.id,
            "invoice_id": record.invoice_id,
            "user_id": record.user_id,
            "action": record.action,
            "status": record.status,
            "result": record.result,
            "ip_address": record.ip_address,
            "created_at": record.created_at
        }
        for record in records
    ]
