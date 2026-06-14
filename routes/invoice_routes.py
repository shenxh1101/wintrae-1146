import os
import uuid
import aiofiles
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from models import User, Invoice, VerificationStatus, ReviewStatus, BlacklistMerchant
from schemas import (
    InvoiceCreate, InvoiceUpdate, InvoiceResponse,
    VerificationRequest, VerificationResult, DuplicateCheckResult,
    TitleValidationRequest, TitleValidationResult, BlacklistMerchantCreate,
    BlacklistMerchantResponse, MonthlySummaryResponse, ExportRequest
)
from services.invoice_service import InvoiceService
from services.verification_service import VerificationRecordService
from services.export_service import ExportService
from auth import get_current_user, require_roles
from config import settings
from models import UserRole

router = APIRouter(prefix="/invoices", tags=["发票管理"])


@router.post("/upload")
async def upload_invoice(
    request: Request,
    file: UploadFile = File(...),
    invoice_type: str = Form("vat_special"),
    invoice_code: Optional[str] = Form(None),
    invoice_number: Optional[str] = Form(None),
    total_amount: Optional[float] = Form(None),
    tax_amount: Optional[float] = Form(None),
    buyer_name: Optional[str] = Form(None),
    seller_name: Optional[str] = Form(None),
    invoice_date: Optional[str] = Form(None),
    expense_category: Optional[str] = Form(None),
    reimbursement_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    action_records = []
    
    if file.size > settings.MAX_FILE_SIZE:
        return {
            "success": False,
            "is_duplicate": False,
            "is_new": False,
            "error_code": "FILE_TOO_LARGE",
            "message": f"文件大小超过限制 ({settings.MAX_FILE_SIZE // (1024*1024)}MB)"
        }
    
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        return {
            "success": False,
            "is_duplicate": False,
            "is_new": False,
            "error_code": "UNSUPPORTED_FILE_TYPE",
            "message": f"不支持的文件类型，仅支持: {', '.join(settings.ALLOWED_EXTENSIONS)}"
        }
    
    content = await file.read()
    if len(content) == 0:
        return {
            "success": False,
            "is_duplicate": False,
            "is_new": False,
            "error_code": "EMPTY_FILE",
            "message": "文件内容为空"
        }
    
    original_data = {
        "invoice_code": invoice_code,
        "invoice_number": invoice_number,
        "invoice_type": invoice_type,
        "total_amount": total_amount,
        "tax_amount": tax_amount,
        "buyer_name": buyer_name,
        "seller_name": seller_name,
        "invoice_date": invoice_date
    }
    
    parsed_date = None
    if invoice_date:
        try:
            parsed_date = datetime.fromisoformat(invoice_date.replace("/", "-"))
        except:
            try:
                parsed_date = datetime.strptime(invoice_date, "%Y-%m-%d")
            except:
                pass
    
    has_key_fields = bool(invoice_code and invoice_number and total_amount)
    
    if not has_key_fields:
        recognized_data = InvoiceService.recognize_invoice_content(
            content=content,
            filename=file.filename.lower(),
            file_type=file_ext
        )
        
        if not recognized_data.get("success"):
            return {
                "success": False,
                "is_duplicate": False,
                "is_new": False,
                "error_code": recognized_data.get("error_code", "RECOGNITION_FAILED"),
                "message": recognized_data.get("error", "无法从文件中识别发票信息"),
                "details": recognized_data.get("details"),
                "original_data": original_data
            }
        
        invoice_code = invoice_code or recognized_data.get("data", {}).get("invoice_code")
        invoice_number = invoice_number or recognized_data.get("data", {}).get("invoice_number")
        total_amount = total_amount or recognized_data.get("data", {}).get("total_amount")
        tax_amount = tax_amount or recognized_data.get("data", {}).get("tax_amount")
        buyer_name = buyer_name or recognized_data.get("data", {}).get("buyer_name")
        seller_name = seller_name or recognized_data.get("data", {}).get("seller_name")
        invoice_type = recognized_data.get("data", {}).get("invoice_type", invoice_type)
        
        if invoice_code and invoice_date:
            try:
                parsed_date = datetime.fromisoformat(invoice_date.replace("/", "-"))
            except:
                try:
                    parsed_date = datetime.strptime(invoice_date, "%Y-%m-%d")
                except:
                    pass
        elif recognized_data.get("data", {}).get("invoice_date"):
            try:
                parsed_date = datetime.strptime(recognized_data["data"]["invoice_date"], "%Y-%m-%d")
            except:
                pass
    
    if not invoice_code or not invoice_number or not total_amount:
        return {
            "success": False,
            "is_duplicate": False,
            "is_new": False,
            "error_code": "MISSING_KEY_FIELDS",
            "message": "缺少关键字段（发票代码、号码或金额）",
            "missing_fields": [
                f for f in ["invoice_code", "invoice_number", "total_amount"]
                if f == "invoice_code" and not invoice_code or
                   f == "invoice_number" and not invoice_number or
                   f == "total_amount" and not total_amount
            ],
            "original_data": original_data
        }
    
    duplicate_result = InvoiceService.check_duplicate(db, invoice_code, invoice_number, total_amount)
    
    if duplicate_result.is_duplicate:
        original_invoice = db.query(Invoice).filter(Invoice.id == duplicate_result.original_invoice_id).first()
        is_owner = original_invoice and original_invoice.submitter_id == current_user.id
        
        if not is_owner and current_user.role == UserRole.EMPLOYEE:
            return {
                "success": False,
                "is_duplicate": True,
                "is_new": False,
                "error_code": "DUPLICATE_INVOICE_OTHERS",
                "message": "该发票已被他人提交，无法重复上传"
            }
        
        return {
            "success": False,
            "is_duplicate": True,
            "is_new": False,
            "error_code": "DUPLICATE_INVOICE",
            "message": "该发票您已提交过，无需重复上传",
            "is_owner": True,
            "original_invoice_id": duplicate_result.original_invoice_id,
            "original_invoice": {
                "id": original_invoice.id,
                "invoice_code": original_invoice.invoice_code,
                "invoice_number": original_invoice.invoice_number,
                "seller_name": original_invoice.seller_name,
                "total_amount": original_invoice.total_amount,
                "verification_status": original_invoice.verification_status.value if original_invoice.verification_status else None,
                "created_at": original_invoice.created_at.isoformat() if original_invoice.created_at else None
            } if is_owner or current_user.role in [UserRole.ADMIN, UserRole.FINANCE, UserRole.AUDITOR] else None
        }
    
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    
    file_id = str(uuid.uuid4())
    file_path = os.path.join(upload_dir, f"{file_id}.{file_ext}")
    
    async with aiofiles.open(file_path, 'wb') as out_file:
        await out_file.write(content)
    
    invoice_data = InvoiceCreate(
        invoice_code=invoice_code,
        invoice_number=invoice_number,
        invoice_type=invoice_type,
        total_amount=total_amount,
        tax_amount=tax_amount,
        amount_without_tax=total_amount - tax_amount if total_amount and tax_amount else None,
        buyer_name=buyer_name,
        seller_name=seller_name,
        invoice_date=parsed_date,
        expense_category=expense_category,
        reimbursement_id=reimbursement_id
    )
    
    invoice = Invoice(
        submitter_id=current_user.id,
        file_path=file_path,
        file_type=file_ext,
        verification_status=VerificationStatus.PENDING,
        review_status=ReviewStatus.PENDING_REVIEW,
        **invoice_data.model_dump()
    )
    
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    
    upload_record = VerificationRecordService.create_verification_record(
        db=db,
        invoice_id=invoice.id,
        user_id=current_user.id,
        action="upload",
        status="success",
        result="发票上传成功",
        request_data={"file_type": file_ext, "file_size": len(content), "fields_provided": {
            "invoice_code": bool(invoice_code),
            "invoice_number": bool(invoice_number),
            "total_amount": bool(total_amount),
            "tax_amount": bool(tax_amount)
        }},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    action_records.append({
        "id": upload_record.id,
        "action": "upload",
        "status": "success",
        "message": "发票上传成功",
        "created_at": upload_record.created_at.isoformat() if upload_record.created_at else None
    })
    
    verification_request = VerificationRequest(
        invoice_code=invoice.invoice_code,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date.strftime("%Y-%m-%d") if invoice.invoice_date else None,
        total_amount=invoice.total_amount,
        tax_number=invoice.seller_tax_number
    )
    
    try:
        verification_result = InvoiceService.verify_invoice(db, invoice.id, verification_request)
        
        verify_record = VerificationRecordService.create_verification_record(
            db=db,
            invoice_id=invoice.id,
            user_id=current_user.id,
            action="verify",
            status=verification_result.status.value,
            result=verification_result.message,
            request_data=verification_request.model_dump(),
            response_data=verification_result.model_dump()
        )
        
        action_records.append({
            "id": verify_record.id,
            "action": "verify",
            "status": verification_result.status.value,
            "message": verification_result.message,
            "is_valid": verification_result.is_valid,
            "exception_reason": verification_result.exception_reason,
            "merchant_blacklisted": verification_result.merchant_blacklisted,
            "created_at": verify_record.created_at.isoformat() if verify_record.created_at else None
        })
        
        verification_result_dict = verification_result.model_dump()
    except Exception as e:
        verification_result_dict = {"error": str(e)}
    
    db.refresh(invoice)
    
    invoice_dict = {
        "id": invoice.id,
        "invoice_code": invoice.invoice_code,
        "invoice_number": invoice.invoice_number,
        "invoice_type": invoice.invoice_type.value if invoice.invoice_type else None,
        "buyer_name": invoice.buyer_name,
        "buyer_tax_number": invoice.buyer_tax_number,
        "seller_name": invoice.seller_name,
        "seller_tax_number": invoice.seller_tax_number,
        "total_amount": invoice.total_amount,
        "tax_amount": invoice.tax_amount,
        "amount_without_tax": invoice.amount_without_tax,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "verification_status": invoice.verification_status.value if invoice.verification_status else None,
        "review_status": invoice.review_status.value if invoice.review_status else None,
        "is_blacklisted": invoice.is_blacklisted,
        "exception_reason": invoice.exception_reason,
        "expense_category": invoice.expense_category,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None
    }
    
    return {
        "success": True,
        "is_duplicate": False,
        "is_new": True,
        "error_code": None,
        "message": "发票上传并查验成功",
        "invoice": invoice_dict,
        "verification_result": verification_result_dict,
        "action_records": action_records
    }


@router.post("/recognize")
async def recognize_invoice(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        return {
            "success": False,
            "error": "不支持的文件类型",
            "error_code": "UNSUPPORTED_FILE_TYPE",
            "supported_types": settings.ALLOWED_EXTENSIONS
        }
    
    try:
        content = await file.read()
        
        if len(content) == 0:
            return {
                "success": False,
                "error": "文件内容为空",
                "error_code": "EMPTY_FILE"
            }
        
        filename = file.filename.lower()
        
        recognized_data = InvoiceService.recognize_invoice_content(
            content=content,
            filename=filename,
            file_type=file_ext
        )
        
        if recognized_data["success"]:
            return {
                "success": True,
                "data": recognized_data["data"],
                "confidence": recognized_data.get("confidence", 0.0),
                "warnings": recognized_data.get("warnings", [])
            }
        else:
            return {
                "success": False,
                "error": recognized_data.get("error", "识别失败"),
                "error_code": recognized_data.get("error_code", "RECOGNITION_FAILED"),
                "details": recognized_data.get("details")
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": f"识别过程发生错误: {str(e)}",
            "error_code": "RECOGNITION_ERROR"
        }


@router.post("/verify/{invoice_id}", response_model=VerificationResult)
async def verify_invoice(
    invoice_id: int,
    verification_request: VerificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    invoice = InvoiceService.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
    
    if current_user.role == UserRole.EMPLOYEE and invoice.submitter_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权操作此发票"
        )
    
    try:
        result = InvoiceService.verify_invoice(db, invoice_id, verification_request)
        
        VerificationRecordService.create_verification_record(
            db=db,
            invoice_id=invoice_id,
            user_id=current_user.id,
            action="verify",
            status=result.status.value,
            result=result.message,
            request_data=verification_request.model_dump(),
            response_data=result.model_dump()
        )
        
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/check-duplicate")
async def check_duplicate(
    invoice_code: str,
    invoice_number: str,
    total_amount: Optional[float] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    result = InvoiceService.check_duplicate(db, invoice_code, invoice_number, total_amount)
    
    if result.is_duplicate:
        original_invoice = db.query(Invoice).filter(Invoice.id == result.original_invoice_id).first()
        
        if original_invoice:
            if original_invoice.submitter_id != current_user.id and \
               current_user.role not in [UserRole.ADMIN, UserRole.FINANCE, UserRole.AUDITOR]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权查询此发票的重复情况"
                )
            
            return {
                "is_duplicate": True,
                "is_owner": original_invoice.submitter_id == current_user.id,
                "original_invoice_id": result.original_invoice_id,
                "original_invoice": {
                    "id": original_invoice.id,
                    "invoice_code": original_invoice.invoice_code,
                    "invoice_number": original_invoice.invoice_number,
                    "seller_name": original_invoice.seller_name,
                    "total_amount": original_invoice.total_amount,
                    "verification_status": original_invoice.verification_status.value if original_invoice.verification_status else None,
                    "created_at": original_invoice.created_at.isoformat() if original_invoice.created_at else None
                } if original_invoice.submitter_id == current_user.id or current_user.role in [UserRole.ADMIN, UserRole.FINANCE, UserRole.AUDITOR] else None
            }
    
    return {
        "is_duplicate": False,
        "submission_count": 0
    }


@router.post("/validate-title", response_model=TitleValidationResult)
async def validate_invoice_title(
    validation_request: TitleValidationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    result = InvoiceService.validate_title(
        db,
        validation_request.buyer_name,
        validation_request.buyer_tax_number,
        validation_request.seller_name,
        validation_request.seller_tax_number
    )
    return result


@router.post("/categorize/{invoice_id}")
async def categorize_expense(
    invoice_id: int,
    category: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    invoice = InvoiceService.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
    
    if current_user.role == UserRole.EMPLOYEE and invoice.submitter_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权操作此发票"
        )
    
    invoice = InvoiceService.categorize_expense(db, invoice_id, category)
    return {"success": True, "message": "费用类别已更新"}


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    invoice = InvoiceService.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
    
    if current_user.role == UserRole.EMPLOYEE and invoice.submitter_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此发票"
        )
    
    return invoice


@router.get("/", response_model=List[InvoiceResponse])
async def list_invoices(
    skip: int = 0,
    limit: int = 100,
    verification_status: Optional[str] = None,
    review_status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    parsed_start = datetime.fromisoformat(start_date) if start_date else None
    parsed_end = datetime.fromisoformat(end_date) if end_date else None
    
    ver_status = VerificationStatus(verification_status) if verification_status else None
    rev_status = ReviewStatus(review_status) if review_status else None
    
    submitter_id = None
    if current_user.role == UserRole.EMPLOYEE:
        submitter_id = current_user.id
    
    invoices = InvoiceService.get_invoices(
        db=db,
        skip=skip,
        limit=limit,
        submitter_id=submitter_id,
        verification_status=ver_status,
        review_status=rev_status,
        start_date=parsed_start,
        end_date=parsed_end
    )
    
    return invoices


@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    invoice_update: InvoiceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    invoice = InvoiceService.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
    
    if current_user.role == UserRole.EMPLOYEE and invoice.submitter_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权修改此发票"
        )
    
    updated_invoice = InvoiceService.update_invoice(db, invoice_id, invoice_update)
    return updated_invoice


@router.post("/link-reimbursement/{invoice_id}")
async def link_reimbursement(
    invoice_id: int,
    reimbursement_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    invoice = InvoiceService.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
    
    if current_user.role == UserRole.EMPLOYEE and invoice.submitter_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权操作此发票"
        )
    
    invoice = InvoiceService.link_reimbursement(db, invoice_id, reimbursement_id)
    return {"success": True, "message": "报销单关联成功"}


@router.post("/blacklist")
async def add_blacklist_merchant(
    merchant: BlacklistMerchantCreate,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCE)),
    db: Session = Depends(get_db)
):
    existing = db.query(BlacklistMerchant).filter(
        BlacklistMerchant.merchant_name == merchant.merchant_name,
        BlacklistMerchant.is_active == True
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="商户已在黑名单中"
        )
    
    db_merchant = BlacklistMerchant(
        merchant_name=merchant.merchant_name,
        tax_number=merchant.tax_number,
        reason=merchant.reason,
        risk_level=merchant.risk_level,
        created_by=current_user.id
    )
    
    db.add(db_merchant)
    db.commit()
    db.refresh(db_merchant)
    
    return {"success": True, "message": "商户已加入黑名单"}


@router.get("/blacklist/merchants", response_model=List[BlacklistMerchantResponse])
async def list_blacklist_merchants(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCE)),
    db: Session = Depends(get_db)
):
    merchants = db.query(BlacklistMerchant).filter(
        BlacklistMerchant.is_active == True
    ).offset(skip).limit(limit).all()
    
    return merchants


@router.delete("/blacklist/{merchant_id}")
async def remove_blacklist_merchant(
    merchant_id: int,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    merchant = db.query(BlacklistMerchant).filter(BlacklistMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="商户不存在"
        )
    
    merchant.is_active = False
    db.commit()
    
    return {"success": True, "message": "商户已从黑名单移除"}


@router.get("/summary/monthly", response_model=MonthlySummaryResponse)
async def get_monthly_summary(
    year: int,
    month: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    is_admin = current_user.role in [UserRole.ADMIN, UserRole.AUDITOR, UserRole.FINANCE]
    
    summary = InvoiceService.get_monthly_summary(
        db=db,
        year=year,
        month=month,
        user_id=current_user.id if not is_admin else None,
        is_admin=is_admin
    )
    
    return summary


@router.post("/export")
async def export_invoices(
    export_request: ExportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(Invoice)
    
    if export_request.start_date:
        query = query.filter(Invoice.invoice_date >= export_request.start_date)
    if export_request.end_date:
        query = query.filter(Invoice.invoice_date <= export_request.end_date)
    if export_request.review_status:
        query = query.filter(Invoice.review_status == export_request.review_status)
    if export_request.verification_status:
        query = query.filter(Invoice.verification_status == export_request.verification_status)
    if export_request.expense_category:
        query = query.filter(Invoice.expense_category == export_request.expense_category)
    
    if current_user.role == UserRole.EMPLOYEE:
        query = query.filter(Invoice.submitter_id == current_user.id)
    
    invoices = query.all()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = os.path.join(settings.UPLOAD_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    
    if export_request.format == "excel":
        output_path = os.path.join(export_dir, f"invoices_export_{timestamp}.xlsx")
        ExportService.export_to_excel(db, invoices, output_path)
    elif export_request.format == "word":
        output_path = os.path.join(export_dir, f"invoices_export_{timestamp}.docx")
        ExportService.export_to_word(db, invoices, output_path)
    elif export_request.format == "pdf":
        output_path = os.path.join(export_dir, f"invoices_export_{timestamp}.pdf")
        ExportService.export_to_pdf(db, invoices, output_path)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的导出格式"
        )
    
    return FileResponse(
        path=output_path,
        filename=os.path.basename(output_path),
        media_type="application/octet-stream"
    )
