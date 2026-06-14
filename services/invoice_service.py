import hashlib
import os
import re
from datetime import datetime
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models import Invoice, VerificationStatus, ReviewStatus, VerificationRecord, BlacklistMerchant, InvoiceType
from schemas import InvoiceCreate, InvoiceUpdate, VerificationRequest, VerificationResult, DuplicateCheckResult


class InvoiceService:
    @staticmethod
    def create_invoice(db: Session, invoice_data: InvoiceCreate, submitter_id: int, file_path: Optional[str] = None, file_type: Optional[str] = None) -> Tuple[Invoice, Optional[DuplicateCheckResult]]:
        duplicate_result = InvoiceService.check_duplicate(
            db, 
            invoice_data.invoice_code, 
            invoice_data.invoice_number, 
            invoice_data.total_amount
        )
        
        if duplicate_result.is_duplicate:
            return None, duplicate_result
        
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
        
        return invoice, None

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
                Invoice.verification_status.in_([
                    VerificationStatus.VERIFIED,
                    VerificationStatus.SUSPICIOUS,
                    VerificationStatus.PENDING,
                    VerificationStatus.PROCESSING
                ])
            )
        ).order_by(Invoice.created_at.asc()).all()

        submission_count = len(existing)
        if submission_count > 0:
            original = existing[0]
            original_invoice_data = {
                "id": original.id,
                "invoice_code": original.invoice_code,
                "invoice_number": original.invoice_number,
                "invoice_type": original.invoice_type.value if original.invoice_type else None,
                "seller_name": original.seller_name,
                "seller_tax_number": original.seller_tax_number,
                "buyer_name": original.buyer_name,
                "total_amount": original.total_amount,
                "tax_amount": original.tax_amount,
                "invoice_date": original.invoice_date.isoformat() if original.invoice_date else None,
                "verification_status": original.verification_status.value if original.verification_status else None,
                "review_status": original.review_status.value if original.review_status else None,
                "submitter_id": original.submitter_id,
                "created_at": original.created_at.isoformat() if original.created_at else None,
                "reimbursement_id": original.reimbursement_id,
                "expense_category": original.expense_category
            }
            
            return DuplicateCheckResult(
                is_duplicate=True,
                original_invoice_id=original.id,
                original_submission_date=original.created_at,
                submission_count=submission_count,
                original_invoice=original_invoice_data
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

    @staticmethod
    def recognize_invoice_content(content: bytes, filename: str, file_type: str) -> dict:
        try:
            extracted_fields = {}
            warnings = []
            confidence = 0.5
            
            invoice_code_pattern = r'(\d{10,12})'
            invoice_number_pattern = r'[号#](\d{8,12})'
            amount_pattern = r'[¥￥]?\s*(\d+\.?\d*)'
            date_pattern = r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)'
            
            try:
                if file_type in ['jpg', 'jpeg', 'png']:
                    import io
                    from PIL import Image
                    image = Image.open(io.BytesIO(content))
                    extracted_fields['image_info'] = {
                        'width': image.width,
                        'height': image.height,
                        'format': image.format
                    }
                elif file_type == 'pdf':
                    extracted_fields['pdf_info'] = {
                        'size': len(content),
                        'filename': filename
                    }
            except Exception:
                pass
            
            content_text = content.decode('utf-8', errors='ignore')
            content_text_lower = content_text.lower()
            
            invoice_codes = re.findall(invoice_code_pattern, content_text)
            if invoice_codes:
                extracted_fields['invoice_code'] = invoice_codes[0]
                confidence += 0.15
            
            invoice_numbers = re.findall(r'(?:发票号码|no|number)[：:\s]*([A-Z0-9]{10,20})', content_text, re.I)
            if not invoice_numbers:
                invoice_numbers = re.findall(r'[号#](\d{8,12})', content_text)
            if invoice_numbers:
                extracted_fields['invoice_number'] = invoice_numbers[0]
                confidence += 0.15
            
            amounts = re.findall(r'(?:价税合计|合计|总额|amount)[：:\s]*[¥￥]?\s*(\d+\.?\d*)', content_text, re.I)
            if amounts and len(amounts) > 0:
                try:
                    total = float(amounts[0])
                    extracted_fields['total_amount'] = total
                    
                    tax_pattern = r'(?:税额|税)[：:\s]*[¥￥]?\s*(\d+\.?\d*)'
                    taxes = re.findall(tax_pattern, content_text)
                    if taxes:
                        extracted_fields['tax_amount'] = float(taxes[0])
                        extracted_fields['amount_without_tax'] = total - float(taxes[0])
                    else:
                        extracted_fields['tax_amount'] = round(total / 1.13 * 0.13, 2)
                        extracted_fields['amount_without_tax'] = round(total / 1.13, 2)
                    
                    confidence += 0.2
                except (ValueError, IndexError):
                    pass
            
            dates = re.findall(date_pattern, content_text)
            if dates:
                date_str = dates[0].replace('年', '-').replace('月', '-').replace('日', '')
                try:
                    invoice_date = datetime.strptime(date_str, '%Y-%m-%d')
                    extracted_fields['invoice_date'] = invoice_date.strftime('%Y-%m-%d')
                    confidence += 0.1
                except ValueError:
                    pass
            
            if any(keyword in content_text_lower for keyword in ['增值税', '专用发票', 'special']):
                extracted_fields['invoice_type'] = 'vat_special'
            elif any(keyword in content_text_lower for keyword in ['普通发票', 'normal', 'generic']):
                extracted_fields['invoice_type'] = 'vat_normal'
            elif any(keyword in content_text_lower for keyword in ['火车票', 'train']):
                extracted_fields['invoice_type'] = 'train_ticket'
            elif any(keyword in content_text_lower for keyword in ['出租车', 'taxi']):
                extracted_fields['invoice_type'] = 'taxi_receipt'
            else:
                extracted_fields['invoice_type'] = 'other'
                warnings.append('未能确定发票类型')
            
            seller_patterns = [
                r'(?:销方|销售方|销售商|卖家|vendor|seller)[：:\s]*([^\n\d]{2,30})',
                r'(?:公司|企业|单位)[名称]*[：:\s]*([^\n\d]{2,30}公司)'
            ]
            for pattern in seller_patterns:
                sellers = re.findall(pattern, content_text)
                if sellers:
                    extracted_fields['seller_name'] = sellers[0].strip()
                    confidence += 0.1
                    break
            
            buyer_patterns = [
                r'(?:购方|购买方|购买商|买家|buyer)[：:\s]*([^\n\d]{2,30})',
            ]
            for pattern in buyer_patterns:
                buyers = re.findall(pattern, content_text)
                if buyers:
                    extracted_fields['buyer_name'] = buyers[0].strip()
                    confidence += 0.1
                    break
            
            tax_number_patterns = [
                r'(?:税号|纳税人识别号|tax\s*number)[：:\s]*([A-Z0-9]{15,20})',
                r'([0-9]{15,20})'
            ]
            for pattern in tax_number_patterns:
                tax_numbers = re.findall(pattern, content_text, re.I)
                if tax_numbers:
                    if 'seller_tax_number' not in extracted_fields:
                        extracted_fields['seller_tax_number'] = tax_numbers[0]
                        confidence += 0.05
                    elif 'buyer_tax_number' not in extracted_fields:
                        extracted_fields['buyer_tax_number'] = tax_numbers[0]
                        confidence += 0.05
                    break
            
            required_fields = ['invoice_code', 'invoice_number', 'total_amount']
            missing_fields = [f for f in required_fields if f not in extracted_fields]
            
            if missing_fields:
                return {
                    "success": False,
                    "error": f"未能识别关键字段: {', '.join(missing_fields)}",
                    "error_code": "MISSING_REQUIRED_FIELDS",
                    "details": {
                        "extracted": list(extracted_fields.keys()),
                        "missing": missing_fields
                    }
                }
            
            confidence = min(confidence, 0.95)
            
            return {
                "success": True,
                "data": extracted_fields,
                "confidence": confidence,
                "warnings": warnings
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"发票识别过程出错: {str(e)}",
                "error_code": "RECOGNITION_ERROR"
            }
