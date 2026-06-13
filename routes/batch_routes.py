from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole
from schemas import BatchTaskCreate, BatchTaskResponse
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
    
    task = BatchTaskService.process_batch_task(db, task_id)
    
    success_count = 0
    failed_count = 0
    
    for invoice_id in task_data.invoice_ids if 'task_data' in dir() else []:
        try:
            invoice = InvoiceService.get_invoice(db, invoice_id)
            if invoice:
                success_count += 1
            else:
                failed_count += 1
        except Exception:
            failed_count += 1
    
    task.processed_count = task.total_count
    task.success_count = success_count
    task.failed_count = failed_count
    
    db.commit()
    db.refresh(task)
    
    return {
        "success": True,
        "task_id": task_id,
        "processed_count": task.processed_count,
        "success_count": success_count,
        "failed_count": failed_count
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
