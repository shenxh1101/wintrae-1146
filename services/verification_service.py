import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from models import BatchTask, Invoice, VerificationRecord, VerificationStatus
from schemas import BatchTaskCreate


class BatchTaskService:
    @staticmethod
    def create_batch_task(db: Session, task_data: BatchTaskCreate, user_id: int) -> BatchTask:
        task_id = str(uuid.uuid4())
        
        task = BatchTask(
            task_id=task_id,
            task_name=task_data.task_name,
            total_count=len(task_data.invoice_ids),
            invoice_ids=task_data.invoice_ids,
            created_by=user_id,
            status="pending"
        )
        
        db.add(task)
        db.commit()
        db.refresh(task)
        
        return task

    @staticmethod
    def process_batch_task(db: Session, task_id: str) -> BatchTask:
        task = db.query(BatchTask).filter(BatchTask.task_id == task_id).first()
        if not task:
            raise ValueError("Task not found")
        
        task.status = "processing"
        db.commit()
        
        return task

    @staticmethod
    def complete_batch_task(db: Session, task_id: str, success: bool, 
                          result_file_path: Optional[str] = None, 
                          error_log: Optional[str] = None) -> BatchTask:
        task = db.query(BatchTask).filter(BatchTask.task_id == task_id).first()
        if not task:
            raise ValueError("Task not found")
        
        task.status = "completed" if success else "failed"
        task.completed_at = datetime.utcnow()
        
        if result_file_path:
            task.result_file_path = result_file_path
        if error_log:
            task.error_log = error_log
        
        db.commit()
        db.refresh(task)
        
        return task

    @staticmethod
    def get_batch_task(db: Session, task_id: str) -> Optional[BatchTask]:
        return db.query(BatchTask).filter(BatchTask.task_id == task_id).first()

    @staticmethod
    def get_user_batch_tasks(db: Session, user_id: int, skip: int = 0, limit: int = 50) -> List[BatchTask]:
        return db.query(BatchTask).filter(
            BatchTask.created_by == user_id
        ).order_by(BatchTask.created_at.desc()).offset(skip).limit(limit).all()


class VerificationRecordService:
    @staticmethod
    def create_verification_record(
        db: Session,
        invoice_id: int,
        user_id: int,
        action: str,
        status: str,
        result: Optional[str] = None,
        error_message: Optional[str] = None,
        request_data: Optional[dict] = None,
        response_data: Optional[dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> VerificationRecord:
        record = VerificationRecord(
            invoice_id=invoice_id,
            user_id=user_id,
            action=action,
            status=status,
            result=result,
            error_message=error_message,
            request_data=request_data,
            response_data=response_data,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        db.add(record)
        db.commit()
        db.refresh(record)
        
        return record

    @staticmethod
    def get_invoice_records(db: Session, invoice_id: int) -> List[VerificationRecord]:
        return db.query(VerificationRecord).filter(
            VerificationRecord.invoice_id == invoice_id
        ).order_by(VerificationRecord.created_at.desc()).all()

    @staticmethod
    def get_user_records(db: Session, user_id: int, skip: int = 0, limit: int = 100) -> List[VerificationRecord]:
        return db.query(VerificationRecord).filter(
            VerificationRecord.user_id == user_id
        ).order_by(VerificationRecord.created_at.desc()).offset(skip).limit(limit).all()
