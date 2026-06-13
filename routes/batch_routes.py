from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import User, UserRole, BatchTask
from schemas import BatchTaskCreate, BatchTaskResponse, VerificationRequest
from services.verification_service import BatchTaskService, VerificationRecordService
from services.invoice_service import InvoiceService
from auth import get_current_user, require_roles

router = APIRouter(prefix="/batch", tags=["批量任务"])


@router.post("/tasks", response_model=BatchTaskResponse)
async def create_batch_task(
    task_data: BatchTaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if len(task_data.invoice_ids) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="发票ID列表不能为空"
        )
    
    task = BatchTaskService.create_batch_task(db, task_data, current_user.id)
    return task


@router.post("/tasks/{task_id}/process")
async def process_batch_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    task = BatchTaskService.get_batch_task(db, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )
    
    if task.created_by != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.FINANCE]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权处理此任务"
        )
    
    if task.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务已完成，不能重复处理"
        )
    
    task = BatchTaskService.process_batch_task(db, task_id)
    
    invoice_ids = task.invoice_ids or []
    success_count = 0
    failed_count = 0
    processed_count = 0
    results = []
    
    from models import Invoice
    for invoice_id in invoice_ids:
        processed_count += 1
        
        try:
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            
            if not invoice:
                failed_count += 1
                results.append({
                    "invoice_id": invoice_id,
                    "status": "failed",
                    "message": "发票不存在"
                })
                continue
            
            verification_request = VerificationRequest(
                invoice_code=invoice.invoice_code,
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date.strftime("%Y-%m-%d") if invoice.invoice_date else None,
                total_amount=invoice.total_amount,
                tax_number=invoice.seller_tax_number
            )
            
            result = InvoiceService.verify_invoice(db, invoice_id, verification_request)
            
            VerificationRecordService.create_verification_record(
                db=db,
                invoice_id=invoice_id,
                user_id=current_user.id,
                action="batch_verify",
                status=result.status.value,
                result=result.message,
                request_data=verification_request.model_dump(),
                response_data=result.model_dump()
            )
            
            if result.is_valid:
                success_count += 1
                results.append({
                    "invoice_id": invoice_id,
                    "status": "success",
                    "message": result.message
                })
            else:
                failed_count += 1
                results.append({
                    "invoice_id": invoice_id,
                    "status": "failed",
                    "message": result.message,
                    "exception_reason": result.exception_reason
                })
                
        except Exception as e:
            failed_count += 1
            results.append({
                "invoice_id": invoice_id,
                "status": "error",
                "message": str(e)
            })
        
        task.processed_count = processed_count
        task.success_count = success_count
        task.failed_count = failed_count
        db.commit()
    
    task.status = "completed"
    task.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    
    return {
        "success": True,
        "task_id": task_id,
        "status": task.status,
        "total_count": task.total_count,
        "processed_count": processed_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "results": results
    }


@router.get("/tasks/{task_id}", response_model=BatchTaskResponse)
async def get_batch_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    task = BatchTaskService.get_batch_task(db, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )
    
    if task.created_by != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.FINANCE]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此任务"
        )
    
    return task


@router.get("/tasks", response_model=List[BatchTaskResponse])
async def list_batch_tasks(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role in [UserRole.ADMIN, UserRole.FINANCE]:
        tasks = db.query(BatchTask).order_by(BatchTask.created_at.desc()).offset(skip).limit(limit).all()
    else:
        tasks = BatchTaskService.get_user_batch_tasks(db, current_user.id, skip, limit)
    
    return tasks
