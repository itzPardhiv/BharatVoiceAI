from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Text,
    CheckConstraint,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(160), nullable=False, index=True)
    category = Column(String(100), nullable=False, default="General")
    unit = Column(String(30), nullable=False, default="piece")
    price = Column(Float, nullable=False, default=0.0)
    cost_price = Column(Float, nullable=False, default=0.0)
    stock_qty = Column(Float, nullable=False, default=0.0)
    reorder_level = Column(Float, nullable=False, default=5.0)
    is_active = Column(Integer, nullable=False, default=1)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    bill_number = Column(String(50), unique=True, index=True, nullable=False)
    customer_name = Column(String(160), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    payment_method = Column(String(30), nullable=False, default="UPI")
    business_account_id = Column(Integer, ForeignKey("business_accounts.id"), nullable=True, index=True)
    status = Column(String(30), nullable=False, default="Completed")
    total = Column(Float, nullable=False, default=0.0)
    discount = Column(Float, nullable=False, default=0.0)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    bill_import = relationship("BillImport", back_populates="sale", uselist=False)
    customer = relationship("Customer", back_populates="sales")
    business_account = relationship("BusinessAccount")


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    product_name = Column(String(160), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    unit_cost = Column(Float, nullable=True)  # Snapshot of Product.cost_price at time of sale
    line_total = Column(Float, nullable=False)

    sale = relationship("Sale", back_populates="items")
    product = relationship("Product")


class BillImport(Base):
    __tablename__ = "bill_imports"

    id = Column(Integer, primary_key=True, index=True)
    source_filename = Column(String(255), nullable=False)
    source_system = Column(String(50), nullable=False, default="CSV_UPLOAD")
    store_id = Column(String(50), nullable=False, default="DEFAULT")
    file_hash = Column(String(64), nullable=False, index=True)
    canonical_fingerprint = Column(String(64), nullable=True, index=True)
    source_bill_number = Column(String(100), nullable=True, index=True)
    status = Column(String(30), nullable=False, default="PENDING")
    total_items = Column(Integer, nullable=False, default=0)
    total_amount = Column(Float, nullable=False, default=0.0)
    sale_id = Column(Integer, ForeignKey("sales.id"), unique=True, nullable=True)
    raw_data_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'VALIDATED', 'CONFIRMED', 'REJECTED', 'FAILED')",
            name="ck_bill_imports_status",
        ),
        CheckConstraint("total_items >= 0", name="ck_bill_imports_total_items"),
        CheckConstraint("total_amount >= 0", name="ck_bill_imports_total_amount"),
        Index(
            "uq_bill_imports_active_source_bill",
            "store_id",
            "source_system",
            "source_bill_number",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'VALIDATED', 'CONFIRMED') AND source_bill_number IS NOT NULL"),
        ),
        Index("ix_bill_imports_canonical", "store_id", "canonical_fingerprint"),
    )

    sale = relationship("Sale", back_populates="bill_import")
    stock_movements = relationship("StockMovement", back_populates="bill_import")


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    quantity_change = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    reason = Column(String(50), nullable=False, default="BILL_IMPORT")
    bill_import_id = Column(Integer, ForeignKey("bill_imports.id"), nullable=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True, index=True)
    notes = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity_change != 0", name="ck_stock_movements_qty_nonzero"),
        CheckConstraint("balance_after >= 0", name="ck_stock_movements_balance_nonnegative"),
        CheckConstraint(
            "reason != 'BILL_IMPORT' OR bill_import_id IS NOT NULL",
            name="ck_stock_movements_bill_import_ref",
        ),
    )

    product = relationship("Product")
    bill_import = relationship("BillImport", back_populates="stock_movements")
    sale = relationship("Sale")


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    category = Column(String(50), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    expense_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    payment_method = Column(String(30), nullable=False, default="Cash")
    description = Column(Text, nullable=False)
    vendor_name = Column(String(160), nullable=True)
    reference_number = Column(String(100), nullable=True)
    receipt_url = Column(String(255), nullable=True)
    business_account_id = Column(Integer, ForeignKey("business_accounts.id"), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="ACTIVE")
    reversed_reason = Column(Text, nullable=True)
    reversed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint("status IN ('ACTIVE', 'REVERSED')", name="ck_expenses_status"),
    )

    business_account = relationship("BusinessAccount")


class BusinessAccount(Base):
    __tablename__ = "business_accounts"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    name = Column(String(100), nullable=False)
    account_type = Column(String(30), nullable=False, default="CASH")
    opening_balance = Column(Float, nullable=False, default=0.0)
    current_balance = Column(Float, nullable=False, default=0.0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("account_type IN ('CASH', 'BANK', 'UPI', 'WALLET', 'OTHER')", name="ck_business_accounts_type"),
    )

    transactions = relationship(
        "AccountTransaction",
        primaryjoin="BusinessAccount.id==AccountTransaction.account_id",
        back_populates="account",
        order_by="desc(AccountTransaction.created_at)",
    )


class AccountTransaction(Base):
    __tablename__ = "account_transactions"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    account_id = Column(Integer, ForeignKey("business_accounts.id"), nullable=False, index=True)
    transaction_type = Column(String(30), nullable=False)
    amount = Column(Float, nullable=False)
    running_balance = Column(Float, nullable=False)
    reference_type = Column(String(50), nullable=True)
    reference_id = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    counterpart_account_id = Column(Integer, ForeignKey("business_accounts.id"), nullable=True)
    is_reversed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('INCOME', 'EXPENSE', 'TRANSFER_IN', 'TRANSFER_OUT', 'REFUND', 'ADJUSTMENT')",
            name="ck_account_transactions_type",
        ),
    )

    account = relationship("BusinessAccount", foreign_keys=[account_id], back_populates="transactions")
    counterpart_account = relationship("BusinessAccount", foreign_keys=[counterpart_account_id])


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    name = Column(String(160), nullable=False, index=True)
    contact_person = Column(String(100), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    gstin = Column(String(30), nullable=True)
    outstanding_balance = Column(Float, nullable=False, default=0.0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    purchases = relationship("PurchaseOrder", back_populates="supplier")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    invoice_number = Column(String(100), nullable=False, index=True)
    purchase_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(30), nullable=False, default="CONFIRMED")
    total_amount = Column(Float, nullable=False, default=0.0)
    paid_amount = Column(Float, nullable=False, default=0.0)
    payment_status = Column(String(20), nullable=False, default="PAID")
    payment_method = Column(String(30), nullable=False, default="BANK_TRANSFER")
    business_account_id = Column(Integer, ForeignKey("business_accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('DRAFT', 'CONFIRMED', 'CANCELLED')", name="ck_purchase_orders_status"),
        CheckConstraint("payment_status IN ('PAID', 'PARTIAL', 'UNPAID')", name="ck_purchase_orders_payment_status"),
        UniqueConstraint("store_id", "supplier_id", "invoice_number", name="uq_purchase_orders_invoice"),
    )

    supplier = relationship("Supplier", back_populates="purchases")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")
    business_account = relationship("BusinessAccount")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    product_name = Column(String(160), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_cost = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)

    purchase = relationship("PurchaseOrder", back_populates="items")
    product = relationship("Product")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    name = Column(String(160), nullable=False, index=True)
    phone = Column(String(20), nullable=True, index=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    outstanding_balance = Column(Float, nullable=False, default=0.0)
    total_spent = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    sales = relationship("Sale", back_populates="customer")
    payments = relationship("CustomerPayment", back_populates="customer")


class CustomerPayment(Base):
    __tablename__ = "customer_payments"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, default="DEFAULT", index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(30), nullable=False, default="CASH")
    business_account_id = Column(Integer, ForeignKey("business_accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_customer_payments_amount_positive"),
    )

    customer = relationship("Customer", back_populates="payments")
    business_account = relationship("BusinessAccount")


class StoreProfile(Base):
    __tablename__ = "store_profiles"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(String(50), unique=True, index=True, nullable=False, default="DEFAULT")
    store_name = Column(String(160), nullable=False, default="BharatVoice Retail Store")
    owner_name = Column(String(160), nullable=True, default="Store Owner")
    phone = Column(String(20), nullable=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    gstin = Column(String(30), nullable=True)
    currency_symbol = Column(String(10), nullable=False, default="₹")
    currency_code = Column(String(10), nullable=False, default="INR")
    default_tax_rate = Column(Float, nullable=False, default=0.0)
    invoice_footer = Column(Text, nullable=True, default="Thank you for shopping with us!")
    accessibility_preferences_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
