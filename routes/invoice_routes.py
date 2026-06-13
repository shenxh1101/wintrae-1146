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


@router.post("/upload", response_model=InvoiceResponse)
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
    if file.size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件大小超过限制 ({settings.MAX_FILE_SIZE // (1024*1024)}MB)"
        )
    
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型，仅支持: {', '.join(settings.ALLOWED_EXTENSIONS)}"
        )
    
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    
    file_id = str(uuid.uuid4())
    file_path = os.path.join(upload_dir, f"{file_id}.{file_ext}")
    
    async with aiofiles.open(file_path, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)
    
    parsed_date = None
    if invoice_date:
        try:
            parsed_date = datetime.fromisoformat(invoice_date.replace("/", "-"))
        except:
            try:
                parsed_date = datetime.strptime(invoice_date, "%Y-%m-%d")
            except:
                pass
    
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
    
    invoice, duplicate_result = InvoiceService.create_invoice(
        db=db,
        invoice_data=invoice_data,
        submitter_id=current_user.id,
        file_path=file_path,
        file_type=file_ext
    )
    
    if invoice is None and duplicate_result is not None:
        return {
            "success": False,
            "is_duplicate": True,
            "message": "发票重复提交",
            "duplicate_info": duplicate_result.model_dump()
        }
    
    VerificationRecordService.create_verification_record(
        db=db,
        invoice_id=invoice.id,
        user_id=current_user.id,
        action="upload",
        status="success",
        result="发票上传成功",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    return invoice


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


@router.post("/check-duplicate", response_model=DuplicateCheckResult)
async def check_duplicate(
    invoice_code: str,
    invoice_number: str,
    total_amount: Optional[float] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return InvoiceService.check_duplicate(db, invoice_code, invoice_number, total_amount)


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
    invoice = InvoiceService.categorize_expense(db, invoice_id, category)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
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
    invoice = InvoiceService.link_reimbursement(db, invoice_id, reimbursement_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="发票不存在"
        )
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
