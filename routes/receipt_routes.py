from typing import List
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole
from schemas import ReceiptResponse
from services.receipt_service import ReceiptService, NotificationService, notification_subscribers
from auth import get_current_user_from_token, decode_token
from database import SessionLocal

router = APIRouter(prefix="/receipts", tags=["回执管理"])


@router.get("/{receipt_id}")
async def get_receipt(
    receipt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    receipt = ReceiptService.get_receipt(db, receipt_id, current_user.id)
    
    if not receipt:
        raise HTTPException(
            status_code=404,
            detail="回执不存在"
        )
    
    if receipt.user_id != current_user.id and current_user.role == UserRole.EMPLOYEE:
        raise HTTPException(
            status_code=403,
            detail="无权访问此回执"
        )
    
    return ReceiptService.build_receipt_response(db, receipt)


@router.get("/user/my")
async def get_my_receipts(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from models import Receipt
    
    query = db.query(Receipt).filter(Receipt.user_id == current_user.id)
    receipts = query.order_by(Receipt.created_at.desc()).offset(skip).limit(limit).all()
    
    return [
        {
            "receipt_id": r.receipt_id,
            "source_type": r.source_type,
            "status": r.status,
            "progress": {
                "total": r.invoice_count,
                "processed": r.processed_count,
                "success": r.success_count,
                "failed": r.failed_count,
                "duplicate": r.duplicate_count,
                "percentage": round(r.processed_count / r.invoice_count * 100, 2) if r.invoice_count > 0 else 0
            },
            "final_status": r.final_status,
            "final_message": r.final_message,
            "created_at": r.created_at.isoformat() if r.created_at else None
        }
        for r in receipts
    ]


@router.get("/{receipt_id}/items")
async def get_receipt_items(
    receipt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    receipt = ReceiptService.get_receipt(db, receipt_id, current_user.id)
    
    if not receipt:
        raise HTTPException(
            status_code=404,
            detail="回执不存在"
        )
    
    if receipt.user_id != current_user.id and current_user.role == UserRole.EMPLOYEE:
        raise HTTPException(
            status_code=403,
            detail="无权访问此回执"
        )
    
    items = ReceiptService.get_receipt_items(db, receipt_id)
    
    return {
        "receipt_id": receipt_id,
        "status": receipt.status,
        "items": items
    }


@router.get("/{receipt_id}/track")
async def get_receipt_track(
    receipt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    receipt = ReceiptService.get_receipt(db, receipt_id, current_user.id)
    
    if not receipt:
        raise HTTPException(
            status_code=404,
            detail="回执不存在"
        )
    
    if receipt.user_id != current_user.id and current_user.role == UserRole.EMPLOYEE:
        raise HTTPException(
            status_code=403,
            detail="无权访问此回执"
        )
    
    items = ReceiptService.get_receipt_items(db, receipt_id)
    
    track = []
    for item in items:
        for record in item.get("action_records", []):
            track.append({
                "invoice_id": item["invoice_id"],
                "invoice_number": item.get("invoice_number", ""),
                "action": record["action"],
                "status": record["status"],
                "message": record["message"],
                "timestamp": record["created_at"]
            })
    
    track.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    
    return {
        "receipt_id": receipt_id,
        "status": receipt.status,
        "track": track
    }


@router.websocket("/{receipt_id}/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    receipt_id: str
):
    await websocket.accept()
    
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return
    
    try:
        token_data = decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return
    
    db = SessionLocal()
    try:
        receipt = ReceiptService.get_receipt(db, receipt_id, token_data.user_id)
        if not receipt:
            await websocket.close(code=4004)
            return
        
        NotificationService.subscribe(receipt_id, websocket)
        
        notification = NotificationService.notify_status_change(
            db, receipt_id, "connected",
            message="已连接到回执通知"
        )
        if notification:
            await websocket.send_json(notification)
        
        try:
            while True:
                data = await websocket.receive_text()
        except WebSocketDisconnect:
            pass
    finally:
        NotificationService.unsubscribe(receipt_id, websocket)
        db.close()
