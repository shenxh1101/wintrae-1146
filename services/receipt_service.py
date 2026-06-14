import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from models import Receipt, Invoice, VerificationRecord, VerificationStatus, ReviewStatus
from schemas import ReceiptResponse, NotificationMessage


class ReceiptService:
    @staticmethod
    def create_receipt(db: Session, source_type: str, source_id: str, user_id: int, 
                       invoice_count: int = 1) -> Receipt:
        receipt_id = str(uuid.uuid4())
        
        receipt = Receipt(
            receipt_id=receipt_id,
            source_type=source_type,
            source_id=source_id,
            user_id=user_id,
            invoice_count=invoice_count,
            status="processing"
        )
        
        db.add(receipt)
        db.commit()
        db.refresh(receipt)
        
        return receipt
    
    @staticmethod
    def update_receipt_progress(db: Session, receipt_id: str, 
                               processed_count: int = None, success_count: int = None,
                               failed_count: int = None, duplicate_count: int = None) -> Optional[Receipt]:
        receipt = db.query(Receipt).filter(Receipt.receipt_id == receipt_id).first()
        if not receipt:
            return None
        
        if processed_count is not None:
            receipt.processed_count = processed_count
        if success_count is not None:
            receipt.success_count = success_count
        if failed_count is not None:
            receipt.failed_count = failed_count
        if duplicate_count is not None:
            receipt.duplicate_count = duplicate_count
        
        receipt.updated_at = datetime.utcnow()
        
        if receipt.processed_count >= receipt.invoice_count:
            receipt.status = "completed"
            receipt.completed_at = datetime.utcnow()
        
        db.commit()
        db.refresh(receipt)
        
        return receipt
    
    @staticmethod
    def complete_receipt(db: Session, receipt_id: str, final_status: str, 
                        final_message: str, final_result: dict = None) -> Optional[Receipt]:
        receipt = db.query(Receipt).filter(Receipt.receipt_id == receipt_id).first()
        if not receipt:
            return None
        
        receipt.final_status = final_status
        receipt.final_message = final_message
        receipt.final_result = final_result
        receipt.status = "completed"
        receipt.updated_at = datetime.utcnow()
        receipt.completed_at = datetime.utcnow()
        
        db.commit()
        db.refresh(receipt)
        
        return receipt
    
    @staticmethod
    def get_receipt(db: Session, receipt_id: str, user_id: int = None) -> Optional[Receipt]:
        query = db.query(Receipt).filter(Receipt.receipt_id == receipt_id)
        if user_id:
            query = query.filter(Receipt.user_id == user_id)
        return query.first()
    
    @staticmethod
    def get_receipt_items(db: Session, receipt_id: str) -> List[dict]:
        receipt = db.query(Receipt).filter(Receipt.receipt_id == receipt_id).first()
        if not receipt:
            return []
        
        if receipt.source_type == "single":
            invoice = db.query(Invoice).filter(Invoice.id == receipt.source_id).first()
            if invoice:
                records = db.query(VerificationRecord).filter(
                    VerificationRecord.invoice_id == invoice.id
                ).order_by(VerificationRecord.created_at.asc()).all()
                
                return [{
                    "invoice_id": invoice.id,
                    "invoice_code": invoice.invoice_code,
                    "invoice_number": invoice.invoice_number,
                    "seller_name": invoice.seller_name,
                    "total_amount": invoice.total_amount,
                    "verification_status": invoice.verification_status.value if invoice.verification_status else None,
                    "review_status": invoice.review_status.value if invoice.review_status else None,
                    "is_blacklisted": invoice.is_blacklisted,
                    "exception_reason": invoice.exception_reason,
                    "action_records": [{
                        "action": r.action,
                        "status": r.status,
                        "message": r.result,
                        "created_at": r.created_at.isoformat() if r.created_at else None
                    } for r in records]
                }]
        
        elif receipt.source_type == "batch":
            invoice_ids = receipt.source_id.split(",") if receipt.source_id else []
            items = []
            for inv_id in invoice_ids:
                try:
                    invoice = db.query(Invoice).filter(Invoice.id == int(inv_id)).first()
                    if invoice:
                        records = db.query(VerificationRecord).filter(
                            VerificationRecord.invoice_id == invoice.id
                        ).order_by(VerificationRecord.created_at.asc()).all()
                        
                        items.append({
                            "invoice_id": invoice.id,
                            "invoice_code": invoice.invoice_code,
                            "invoice_number": invoice.invoice_number,
                            "seller_name": invoice.seller_name,
                            "total_amount": invoice.total_amount,
                            "verification_status": invoice.verification_status.value if invoice.verification_status else None,
                            "review_status": invoice.review_status.value if invoice.review_status else None,
                            "is_blacklisted": invoice.is_blacklisted,
                            "exception_reason": invoice.exception_reason,
                            "action_records": [{
                                "action": r.action,
                                "status": r.status,
                                "message": r.result,
                                "created_at": r.created_at.isoformat() if r.created_at else None
                            } for r in records]
                        })
                except ValueError:
                    continue
            return items
        
        return []
    
    @staticmethod
    def build_receipt_response(db: Session, receipt: Receipt) -> dict:
        items = ReceiptService.get_receipt_items(db, receipt.receipt_id)
        
        return {
            "receipt_id": receipt.receipt_id,
            "source_type": receipt.source_type,
            "source_id": receipt.source_id,
            "status": receipt.status,
            "progress": {
                "total": receipt.invoice_count,
                "processed": receipt.processed_count,
                "success": receipt.success_count,
                "failed": receipt.failed_count,
                "duplicate": receipt.duplicate_count,
                "percentage": round(receipt.processed_count / receipt.invoice_count * 100, 2) if receipt.invoice_count > 0 else 0
            },
            "final_status": receipt.final_status,
            "final_message": receipt.final_message,
            "final_result": receipt.final_result,
            "items": items,
            "created_at": receipt.created_at.isoformat() if receipt.created_at else None
        }
    
    @staticmethod
    def build_notification_message(db: Session, receipt: Receipt, event_type: str,
                                  invoice_id: int = None, message: str = None) -> dict:
        invoice_info = None
        action_records = None
        
        if invoice_id:
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if invoice:
                invoice_info = {
                    "invoice_id": invoice.id,
                    "invoice_code": invoice.invoice_code,
                    "invoice_number": invoice.invoice_number,
                    "seller_name": invoice.seller_name,
                    "total_amount": invoice.total_amount,
                    "verification_status": invoice.verification_status.value if invoice.verification_status else None,
                    "is_blacklisted": invoice.is_blacklisted,
                    "exception_reason": invoice.exception_reason
                }
                
                records = db.query(VerificationRecord).filter(
                    VerificationRecord.invoice_id == invoice_id
                ).order_by(VerificationRecord.created_at.desc()).limit(5).all()
                
                action_records = [{
                    "action": r.action,
                    "status": r.status,
                    "message": r.result,
                    "created_at": r.created_at.isoformat() if r.created_at else None
                } for r in records]
        
        return {
            "receipt_id": receipt.receipt_id,
            "event_type": event_type,
            "status": receipt.status,
            "message": message or receipt.final_message or "处理中",
            "invoice_id": invoice_id,
            "invoice_info": invoice_info,
            "action_records": action_records,
            "timestamp": datetime.utcnow().isoformat()
        }


notification_subscribers = {}


class NotificationService:
    @staticmethod
    def subscribe(receipt_id: str, websocket):
        if receipt_id not in notification_subscribers:
            notification_subscribers[receipt_id] = []
        notification_subscribers[receipt_id].append(websocket)
    
    @staticmethod
    def unsubscribe(receipt_id: str, websocket):
        if receipt_id in notification_subscribers:
            if websocket in notification_subscribers[receipt_id]:
                notification_subscribers[receipt_id].remove(websocket)
    
    @staticmethod
    async def send_notification(receipt_id: str, message: dict):
        if receipt_id in notification_subscribers:
            for websocket in notification_subscribers[receipt_id]:
                try:
                    await websocket.send_json(message)
                except Exception:
                    pass
    
    @staticmethod
    def notify_status_change(db: Session, receipt_id: str, event_type: str,
                           invoice_id: int = None, message: str = None):
        receipt = ReceiptService.get_receipt(db, receipt_id)
        if not receipt:
            return
        
        notification = ReceiptService.build_notification_message(
            db, receipt, event_type, invoice_id, message
        )
        
        return notification
