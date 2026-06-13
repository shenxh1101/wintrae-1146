from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole, VerificationRecord
from schemas import VerificationRecordResponse
from services.verification_service import VerificationRecordService
from auth import get_current_user, require_roles

router = APIRouter(prefix="/records", tags=["查验记录"])


@router.get("/invoice/{invoice_id}", response_model=List[VerificationRecordResponse])
async def get_invoice_records(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    records = db.query(VerificationRecord).filter(
        VerificationRecord.invoice_id == invoice_id
    ).order_by(VerificationRecord.created_at.desc()).all()
    
    return records


@router.get("/my", response_model=List[VerificationRecordResponse])
async def get_my_records(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    records = db.query(VerificationRecord).filter(
        VerificationRecord.user_id == current_user.id
    ).order_by(VerificationRecord.created_at.desc()).offset(skip).limit(limit).all()
    
    return records


@router.get("/all", response_model=List[VerificationRecordResponse])
async def get_all_records(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCE, UserRole.AUDITOR)),
    db: Session = Depends(get_db)
):
    records = db.query(VerificationRecord).order_by(
        VerificationRecord.created_at.desc()
    ).offset(skip).limit(limit).all()
    
    return records


@router.get("/invoice/{invoice_id}/latest")
async def get_latest_record(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    record = db.query(VerificationRecord).filter(
        VerificationRecord.invoice_id == invoice_id
    ).order_by(VerificationRecord.created_at.desc()).first()
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到查验记录"
        )
    
    return record
