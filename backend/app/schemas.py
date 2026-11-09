from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku: str
    name: str
    category: str
    unit: str
    price: float
    cost_price: float
    stock_qty: float
    reorder_level: float
    is_active: bool = True
    store_id: str = "DEFAULT"


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=160)
    category: str = "General"
    unit: str = "piece"
    price: float = Field(ge=0)
    cost_price: float = Field(default=0, ge=0)
    stock_qty: float = Field(default=0, ge=0)
    reorder_level: float = Field(default=5, ge=0)
    is_active: bool = True


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    category: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    cost_price: Optional[float] = Field(default=None, ge=0)
    reorder_level: Optional[float] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class StockAdjustRequest(BaseModel):
    quantity_change: float
    reason: str = Field(default="MANUAL_ADJUSTMENT")  # RESTOCK, DAMAGE, RETURN, MANUAL_ADJUSTMENT, PURCHASE
    notes: Optional[str] = None


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    quantity_change: float
    balance_after: float
    reason: str
    bill_import_id: Optional[int] = None
    sale_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: str


class SaleItemCreate(BaseModel):
    sku: str
    quantity: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)


class SaleCreate(BaseModel):
    bill_number: Optional[str] = None
    customer_name: Optional[str] = "Walk-in Customer"
    customer_id: Optional[int] = None
    payment_method: str = "UPI"
    business_account_id: Optional[int] = None
    discount: float = Field(default=0.0, ge=0)
    items: List[SaleItemCreate] = Field(min_length=1)


class SaleItemOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: float
    unit_price: float
    unit_cost: Optional[float] = None
    line_total: float


class SaleDetailOut(BaseModel):
    id: int
    bill_number: str
    customer_name: Optional[str]
    customer_id: Optional[int] = None
    payment_method: str
    business_account_id: Optional[int] = None
    status: str
    total: float
    discount: float = 0.0
    created_at: str
    items: List[SaleItemOut]


class SaleOut(BaseModel):
    id: int
    bill_number: str
    customer_name: Optional[str]
    payment_method: str
    status: str
    total: float
    discount: float = 0.0
    created_at: str


# ==============================================================================
# FINANCIAL INTELLIGENCE & PROFIT SCHEMAS (PHASE 1)
# ==============================================================================

class FinanceMetricsOut(BaseModel):
    period: str
    start_date: str
    end_date: str
    gross_revenue: float
    total_discounts: float
    total_refunds: float
    net_sales: float
    cogs: float
    gross_profit: float
    operating_expenses: float
    net_profit: float
    gross_profit_margin: float  # Percentage
    net_profit_margin: float    # Percentage
    total_sales_count: int
    has_estimated_cost_records: bool
    inventory_cost_valuation: float
    inventory_selling_valuation: float
    outstanding_customer_dues: float


class PeriodTrendPoint(BaseModel):
    date: str
    label: str
    gross_sales: float
    net_sales: float
    cogs: float
    gross_profit: float
    expenses: float
    net_profit: float


class ProductProfitRanking(BaseModel):
    product_id: int
    sku: str
    name: str
    category: str
    quantity_sold: float
    sales_amount: float
    cogs: float
    gross_profit: float
    margin_percentage: float


class CategoryExpenseBreakdown(BaseModel):
    category: str
    total_amount: float
    percentage: float
    count: int


class FinanceAnalyticsOut(BaseModel):
    period: str
    trend: List[PeriodTrendPoint]
    best_selling_products: List[ProductProfitRanking]
    highest_profit_products: List[ProductProfitRanking]
    low_margin_products: List[ProductProfitRanking]
    expense_breakdown: List[CategoryExpenseBreakdown]


class InventoryValuationOut(BaseModel):
    total_items_count: int
    total_stock_units: float
    total_cost_valuation: float
    total_selling_valuation: float
    potential_gross_profit: float
    potential_margin_percentage: float
    low_stock_items_count: int


# ==============================================================================
# EXPENSE MANAGEMENT SCHEMAS (PHASE 2)
# ==============================================================================

class ExpenseCreate(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    amount: float = Field(gt=0)
    expense_date: Optional[str] = None
    payment_method: str = "Cash"
    description: str = Field(min_length=1)
    vendor_name: Optional[str] = None
    reference_number: Optional[str] = None
    receipt_url: Optional[str] = None
    business_account_id: Optional[int] = None


class ExpenseUpdate(BaseModel):
    category: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    expense_date: Optional[str] = None
    payment_method: Optional[str] = None
    description: Optional[str] = None
    vendor_name: Optional[str] = None
    reference_number: Optional[str] = None
    receipt_url: Optional[str] = None
    business_account_id: Optional[int] = None


class ExpenseReverse(BaseModel):
    reason: str = Field(min_length=3)


class ExpenseOut(BaseModel):
    id: int
    store_id: str
    category: str
    amount: float
    expense_date: str
    payment_method: str
    description: str
    vendor_name: Optional[str] = None
    reference_number: Optional[str] = None
    receipt_url: Optional[str] = None
    business_account_id: Optional[int] = None
    business_account_name: Optional[str] = None
    status: str
    reversed_reason: Optional[str] = None
    reversed_at: Optional[str] = None
    created_at: str


# ==============================================================================
# BUSINESS ACCOUNTS & CASHBOOK SCHEMAS (PHASE 3)
# ==============================================================================

class BusinessAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    account_type: str = Field(default="CASH")  # CASH, BANK, UPI, WALLET, OTHER
    opening_balance: float = Field(default=0.0)


class BusinessAccountUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


class BusinessAccountOut(BaseModel):
    id: int
    store_id: str
    name: str
    account_type: str
    opening_balance: float
    current_balance: float
    is_active: bool
    created_at: str


class AccountTransactionOut(BaseModel):
    id: int
    store_id: str
    account_id: int
    account_name: Optional[str] = None
    transaction_type: str
    amount: float
    running_balance: float
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    description: Optional[str] = None
    counterpart_account_id: Optional[int] = None
    counterpart_account_name: Optional[str] = None
    is_reversed: bool = False
    created_at: str


class AccountTransferRequest(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: float = Field(gt=0)
    notes: Optional[str] = None


class CashClosingSummaryOut(BaseModel):
    date: str
    opening_cash: float
    cash_sales: float
    cash_expenses: float
    cash_transfers_in: float
    cash_transfers_out: float
    expected_closing_cash: float
    actual_current_balance: float
    total_sales_all_modes: float


# ==============================================================================
# SUPPLIERS & PURCHASES SCHEMAS (PHASE 4)
# ==============================================================================

class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    is_active: Optional[bool] = None


class SupplierOut(BaseModel):
    id: int
    store_id: str
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    outstanding_balance: float
    is_active: bool
    created_at: str


class PurchaseItemCreate(BaseModel):
    sku: str
    quantity: float = Field(gt=0)
    unit_cost: float = Field(ge=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    invoice_number: str = Field(min_length=1, max_length=100)
    purchase_date: Optional[str] = None
    items: List[PurchaseItemCreate] = Field(min_length=1)
    paid_amount: float = Field(default=0.0, ge=0)
    payment_method: str = "BANK_TRANSFER"
    business_account_id: Optional[int] = None
    notes: Optional[str] = None


class PurchaseItemOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: float
    unit_cost: float
    line_total: float


class PurchaseOrderOut(BaseModel):
    id: int
    store_id: str
    supplier_id: int
    supplier_name: Optional[str] = None
    invoice_number: str
    purchase_date: str
    status: str
    total_amount: float
    paid_amount: float
    payment_status: str
    payment_method: str
    notes: Optional[str] = None
    created_at: str
    items: List[PurchaseItemOut]


class SupplierPaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_method: str = "CASH"
    business_account_id: Optional[int] = None
    notes: Optional[str] = None


class SupplierPaymentOut(BaseModel):
    supplier_id: int
    name: str
    amount_paid: float
    outstanding_balance: float
    payment_method: str
    business_account_id: Optional[int] = None
    notes: Optional[str] = None


class SaleRefundCreate(BaseModel):
    reason: Optional[str] = "Customer refund requested"


# ==============================================================================
# CUSTOMER MANAGEMENT SCHEMAS (PHASE 5)
# ==============================================================================

class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class CustomerOut(BaseModel):
    id: int
    store_id: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    outstanding_balance: float
    total_spent: float
    created_at: str


class CustomerPaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_method: str = "CASH"
    business_account_id: Optional[int] = None
    notes: Optional[str] = None


class CustomerPaymentOut(BaseModel):
    id: int
    store_id: str
    customer_id: int
    amount: float
    payment_method: str
    notes: Optional[str] = None
    created_at: str


# ==============================================================================
# STORE PROFILE & SETTINGS SCHEMAS (PHASE 7)
# ==============================================================================

class StoreProfileUpdate(BaseModel):
    store_name: Optional[str] = None
    owner_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    currency_symbol: Optional[str] = None
    currency_code: Optional[str] = None
    default_tax_rate: Optional[float] = Field(default=None, ge=0)
    invoice_footer: Optional[str] = None
    accessibility_preferences_json: Optional[str] = None


class StoreProfileOut(BaseModel):
    id: int
    store_id: str
    store_name: str
    owner_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    currency_symbol: str
    currency_code: str
    default_tax_rate: float
    invoice_footer: Optional[str] = None
    accessibility_preferences_json: Optional[str] = None
    updated_at: str
