import hashlib
import os
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models import Invoice, VerificationStatus, ReviewStatus, VerificationRecord, BlacklistMerchant, InvoiceType
from schemas import InvoiceCreate, InvoiceUpdate, VerificationRequest, VerificationResult, DuplicateCheckResult


class InvoiceService:
    @staticmethod
    def create_invoice(db: Session, invoice_data: InvoiceCreate, submitter_id: int, file_path: Optional[str] = None, file_type: Optional[str] = None) -> Invoice:
        invoice_dict = invoice_data.model_dump()
        invoice_dict.update({
            "submitter_id": submitter_id,
            "file_path": file_path,
            "file_type": file_type,
            "verification_status": VerificationStatus.PENDING,
            "review_status": ReviewStatus.PENDING_REVIEW
        })
        
        invoice = Invoice(**invoice_dict)
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def get_invoice(db: Session, invoice_id: int) -> Optional[Invoice]:
        return db.query(Invoice).filter(Invoice.id == invoice_id).first()

    @staticmethod
    def get_invoices(
        db: Session,
        skip: int = 0,
        limit: int = 100,
        submitter_id: Optional[int] = None,
        verification_status: Optional[VerificationStatus] = None,
        review_status: Optional[ReviewStatus] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Invoice]:
        query = db.query(Invoice)
        
        if submitter_id:
            query = query.filter(Invoice.submitter_id == submitter_id)
        if verification_status:
            query = query.filter(Invoice.verification_status == verification_status)
        if review_status:
            query = query.filter(Invoice.review_status == review_status)
        if start_date:
            query = query.filter(Invoice.invoice_date >= start_date)
        if end_date:
            query = query.filter(Invoice.invoice_date <= end_date)
            
        return query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit).all()

    @staticmethod
    def update_invoice(db: Session, invoice_id: int, invoice_data: InvoiceUpdate) -> Optional[Invoice]:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return None
        
        update_data = invoice_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(invoice, field, value)
        
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def verify_invoice(db: Session, invoice_id: int, verification_request: VerificationRequest) -> VerificationResult:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise ValueError("Invoice not found")

        invoice.verification_status = VerificationStatus.PROCESSING
        db.commit()

        exception_reasons = []
        is_valid = True
        merchant_blacklisted = False
        blacklist_reason = None

        if invoice.invoice_code and invoice.invoice_number and verification_request.invoice_code:
            if invoice.invoice_code != verification_request.invoice_code or \
               invoice.invoice_number != verification_request.invoice_number:
                is_valid = False
                exception_reasons.append("发票代码或号码不匹配")

        if invoice.total_amount and verification_request.total_amount:
            if abs(invoice.total_amount - verification_request.total_amount) > 0.01:
                is_valid = False
                exception_reasons.append("金额不匹配")

        blacklisted = db.query(BlacklistMerchant).filter(
            and_(
                BlacklistMerchant.is_active == True,
                or_(
                    BlacklistMerchant.merchant_name == invoice.seller_name,
                    BlacklistMerchant.tax_number == invoice.seller_tax_number
                )
            )
        ).first()
        
        if blacklisted:
            merchant_blacklisted = True
            blacklist_reason = blacklisted.reason
            invoice.is_blacklisted = True
            invoice.blacklisted_reason = blacklisted.reason

        duplicate_check = InvoiceService.check_duplicate(db, invoice.invoice_code, invoice.invoice_number, invoice.total_amount)
        if duplicate_check.is_duplicate:
            invoice.verification_status = VerificationStatus.DUPLICATE
            invoice.exception_reason = "发票重复提交"
            db.commit()
            return VerificationResult(
                is_valid=False,
                status=VerificationStatus.DUPLICATE,
                message="发票已被其他用户提交",
                exception_reason="发票重复提交",
                merchant_blacklisted=merchant_blacklisted,
                blacklist_reason=blacklist_reason
            )

        if is_valid and not merchant_blacklisted:
            invoice.verification_status = VerificationStatus.VERIFIED
            status_msg = "验证通过"
        elif merchant_blacklisted:
            invoice.verification_status = VerificationStatus.SUSPICIOUS
            status_msg = "商户在黑名单中"
            is_valid = False
        else:
            invoice.verification_status = VerificationStatus.FAILED
            status_msg = "验证失败"

        if exception_reasons:
            invoice.exception_reason = "; ".join(exception_reasons)

        db.commit()
        db.refresh(invoice)

        return VerificationResult(
            is_valid=is_valid,
            status=invoice.verification_status,
            message=status_msg,
            exception_reason=invoice.exception_reason,
            merchant_blacklisted=merchant_blacklisted,
            blacklist_reason=blacklist_reason
        )

    @staticmethod
    def check_duplicate(db: Session, invoice_code: Optional[str], invoice_number: Optional[str], total_amount: Optional[float]) -> DuplicateCheckResult:
        if not invoice_code or not invoice_number:
            return DuplicateCheckResult(is_duplicate=False, submission_count=0)
        
        existing = db.query(Invoice).filter(
            and_(
                Invoice.invoice_code == invoice_code,
                Invoice.invoice_number == invoice_number,
                Invoice.verification_status != VerificationStatus.FAILED
            )
        ).all()

        submission_count = len(existing)
        if submission_count > 0:
            original = existing[0]
            return DuplicateCheckResult(
                is_duplicate=True,
                original_invoice_id=original.id,
                original_submission_date=original.created_at,
                submission_count=submission_count
            )
        
        return DuplicateCheckResult(is_duplicate=False, submission_count=0)

    @staticmethod
    def validate_title(db: Session, buyer_name: Optional[str], buyer_tax_number: Optional[str], 
                       seller_name: Optional[str], seller_tax_number: Optional[str]) -> dict:
        from schemas import TitleValidationResult
        
        warnings = []
        buyer_match = True
        seller_match = True
        
        if buyer_name and buyer_tax_number:
            if not InvoiceService._validate_tax_number(buyer_tax_number):
                warnings.append("购买方税号格式不正确")
                buyer_match = False
        
        if seller_name and seller_tax_number:
            if not InvoiceService._validate_tax_number(seller_tax_number):
                warnings.append("销售方税号格式不正确")
                seller_match = False
        
        return {
            "is_valid": buyer_match and seller_match,
            "buyer_match": buyer_match,
            "seller_match": seller_match,
            "warnings": warnings
        }

    @staticmethod
    def _validate_tax_number(tax_number: str) -> bool:
        if not tax_number or len(tax_number) < 15:
            return False
        return True

    @staticmethod
    def categorize_expense(db: Session, invoice_id: int, category: str) -> Optional[Invoice]:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return None
        
        invoice.expense_category = category
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def link_reimbursement(db: Session, invoice_id: int, reimbursement_id: str) -> Optional[Invoice]:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            return None
        
        invoice.reimbursement_id = reimbursement_id
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def get_monthly_summary(db: Session, year: int, month: int, user_id: Optional[int] = None, 
                           is_admin: bool = False) -> dict:
        from datetime import datetime
        from sqlalchemy import func
        
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
        
        query = db.query(Invoice).filter(
            and_(
                Invoice.invoice_date >= start_date,
                Invoice.invoice_date < end_date
            )
        )
        
        if not is_admin and user_id:
            query = query.filter(Invoice.submitter_id == user_id)
        
        invoices = query.all()
        
        total_amount = sum(inv.total_amount or 0 for inv in invoices)
        total_tax = sum(inv.tax_amount or 0 for inv in invoices)
        pending_review = sum(1 for inv in invoices if inv.review_status == ReviewStatus.PENDING_REVIEW)
        approved = sum(1 for inv in invoices if inv.review_status == ReviewStatus.APPROVED)
        rejected = sum(1 for inv in invoices if inv.review_status == ReviewStatus.REJECTED)
        suspicious = sum(1 for inv in invoices if inv.verification_status == VerificationStatus.SUSPICIOUS)
        
        by_category = {}
        for inv in invoices:
            cat = inv.expense_category or "未分类"
            if cat not in by_category:
                by_category[cat] = {"count": 0, "amount": 0}
            by_category[cat]["count"] += 1
            by_category[cat]["amount"] += inv.total_amount or 0
        
        return {
            "year": year,
            "month": month,
            "total_invoices": len(invoices),
            "total_amount": total_amount,
            "total_tax": total_tax,
            "pending_review_count": pending_review,
            "approved_count": approved,
            "rejected_count": rejected,
            "suspicious_count": suspicious,
            "by_category": by_category
        }
