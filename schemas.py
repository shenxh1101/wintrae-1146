from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime
from enum import Enum


class InvoiceType(str, Enum):
    VAT_SPECIAL = "vat_special"
    VAT_NORMAL = "vat_normal"
    TRAIN_TICKET = "train_ticket"
    TAXI_RECEIPT = "taxi_receipt"
    OTHER = "other"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    VERIFIED = "verified"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    SUSPICIOUS = "suspicious"


class ReviewStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEED_INFO = "need_info"


class UserRole(str, Enum):
    ADMIN = "admin"
    FINANCE = "finance"
    EMPLOYEE = "employee"
    AUDITOR = "auditor"


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    id: int
    role: UserRole
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceBase(BaseModel):
    invoice_code: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_type: InvoiceType = InvoiceType.VAT_SPECIAL
    
    buyer_name: Optional[str] = None
    buyer_tax_number: Optional[str] = None
    buyer_bank: Optional[str] = None
    buyer_account: Optional[str] = None
    
    seller_name: Optional[str] = None
    seller_tax_number: Optional[str] = None
    seller_bank: Optional[str] = None
    seller_account: Optional[str] = None
    
    total_amount: Optional[float] = None
    tax_amount: Optional[float] = None
    amount_without_tax: Optional[float] = None
    
    invoice_date: Optional[datetime] = None
    issuance_date: Optional[datetime] = None
    
    expense_category: Optional[str] = None
    remarks: Optional[str] = None
    reimbursement_id: Optional[str] = None


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    expense_category: Optional[str] = None
    review_status: Optional[ReviewStatus] = None
    remarks: Optional[str] = None


class InvoiceResponse(InvoiceBase):
    id: int
    verification_status: VerificationStatus
    review_status: ReviewStatus
    
    file_path: Optional[str] = None
    file_type: Optional[str] = None
    
    is_blacklisted: bool
    blacklisted_reason: Optional[str] = None
    exception_reason: Optional[str] = None
    
    submitter_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class VerificationRequest(BaseModel):
    invoice_code: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: Optional[float] = None
    tax_number: Optional[str] = None


class VerificationResult(BaseModel):
    is_valid: bool
    status: VerificationStatus
    message: str
    exception_reason: Optional[str] = None
    merchant_blacklisted: bool = False
    blacklist_reason: Optional[str] = None


class DuplicateCheckResult(BaseModel):
    is_duplicate: bool
    original_invoice_id: Optional[int] = None
    original_submission_date: Optional[datetime] = None
    submission_count: int = 0
    original_invoice: Optional[dict] = None


class TitleValidationRequest(BaseModel):
    buyer_name: Optional[str] = None
    buyer_tax_number: Optional[str] = None
    seller_name: Optional[str] = None
    seller_tax_number: Optional[str] = None


class TitleValidationResult(BaseModel):
    is_valid: bool
    buyer_match: bool
    seller_match: bool
    warnings: List[str] = []


class BlacklistMerchantBase(BaseModel):
    merchant_name: str
    tax_number: Optional[str] = None
    reason: Optional[str] = None
    risk_level: Optional[str] = None


class BlacklistMerchantCreate(BlacklistMerchantBase):
    pass


class BlacklistMerchantResponse(BlacklistMerchantBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class BatchTaskBase(BaseModel):
    task_name: str
    invoice_ids: List[int]


class BatchTaskCreate(BatchTaskBase):
    pass


class BatchTaskResponse(BaseModel):
    id: int
    task_id: str
    task_name: str
    status: str
    total_count: int
    processed_count: int
    success_count: int
    failed_count: int
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class MonthlySummaryRequest(BaseModel):
    year: int
    month: int


class MonthlySummaryResponse(BaseModel):
    year: int
    month: int
    total_invoices: int
    total_amount: float
    total_tax: float
    pending_review_count: int
    approved_count: int
    rejected_count: int
    suspicious_count: int
    by_category: dict


class ExportRequest(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    review_status: Optional[ReviewStatus] = None
    verification_status: Optional[VerificationStatus] = None
    expense_category: Optional[str] = None
    format: str = "excel"


class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str


class LoginRequest(BaseModel):
    username: str
    password: str


class VerificationRecordResponse(BaseModel):
    id: int
    invoice_id: int
    action: str
    status: str
    result: Optional[str] = None
    error_message: Optional[str] = None
    request_data: Optional[dict] = None
    response_data: Optional[dict] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class InvoiceUploadResponse(BaseModel):
    success: bool
    is_duplicate: bool = False
    is_new: bool = True
    invoice: Optional[dict] = None
    duplicate_info: Optional[dict] = None
    verification_result: Optional[dict] = None
    action_records: Optional[List[dict]] = None
    message: str = ""
    
    class Config:
        from_attributes = True
