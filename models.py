from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from database import Base


class InvoiceType(str, enum.Enum):
    VAT_SPECIAL = "vat_special"
    VAT_NORMAL = "vat_normal"
    TRAIN_TICKET = "train_ticket"
    TAXI_RECEIPT = "taxi_receipt"
    OTHER = "other"


class VerificationStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    VERIFIED = "verified"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    SUSPICIOUS = "suspicious"


class ReviewStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEED_INFO = "need_info"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    FINANCE = "finance"
    EMPLOYEE = "employee"
    AUDITOR = "auditor"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    role = Column(Enum(UserRole), default=UserRole.EMPLOYEE)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="submitter")
    verification_records = relationship("VerificationRecord", back_populates="user")


class BlacklistMerchant(Base):
    __tablename__ = "blacklist_merchants"

    id = Column(Integer, primary_key=True, index=True)
    merchant_name = Column(String(255), nullable=False, index=True)
    tax_number = Column(String(50))
    reason = Column(Text)
    risk_level = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True)


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_code = Column(String(20), index=True)
    invoice_number = Column(String(20), index=True)
    invoice_type = Column(Enum(InvoiceType), default=InvoiceType.VAT_SPECIAL)
    
    buyer_name = Column(String(255))
    buyer_tax_number = Column(String(50))
    buyer_bank = Column(String(100))
    buyer_account = Column(String(50))
    
    seller_name = Column(String(255), index=True)
    seller_tax_number = Column(String(50), index=True)
    seller_bank = Column(String(100))
    seller_account = Column(String(50))
    
    total_amount = Column(Float)
    tax_amount = Column(Float)
    amount_without_tax = Column(Float)
    
    invoice_date = Column(DateTime, index=True)
    issuance_date = Column(DateTime)
    
    verification_status = Column(Enum(VerificationStatus), default=VerificationStatus.PENDING)
    review_status = Column(Enum(ReviewStatus), default=ReviewStatus.PENDING_REVIEW)
    
    expense_category = Column(String(50))
    file_path = Column(String(500))
    file_type = Column(String(10))
    
    remarks = Column(Text)
    exception_reason = Column(Text)
    
    reimbursement_id = Column(String(50), index=True)
    submitter_id = Column(Integer, ForeignKey("users.id"))
    submitter = relationship("User", back_populates="invoices")
    
    is_blacklisted = Column(Boolean, default=False)
    blacklisted_reason = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    verification_records = relationship("VerificationRecord", back_populates="invoice")


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), index=True)
    invoice = relationship("Invoice", back_populates="verification_records")
    
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User", back_populates="verification_records")
    
    action = Column(String(50))
    status = Column(String(20))
    result = Column(Text)
    error_message = Column(Text)
    
    request_data = Column(JSON)
    response_data = Column(JSON)
    
    ip_address = Column(String(50))
    user_agent = Column(String(255))
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class BatchTask(Base):
    __tablename__ = "batch_tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), unique=True, index=True, nullable=False)
    task_name = Column(String(255))
    
    status = Column(String(20), default="pending")
    total_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    
    invoice_ids = Column(JSON, default=list)
    
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    
    result_file_path = Column(String(500))
    error_log = Column(Text)
