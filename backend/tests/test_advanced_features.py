"""
Comprehensive test suite for BharatVoice AI Advanced Retail, Financial Management,
Business Accounts, Expenses, Suppliers, Customers, and Voice Intelligence.
"""
import pytest
import sqlite3
from decimal import Decimal
from datetime import datetime, date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.database import Base, get_db
from app.models import (
    Product,
    Sale,
    SaleItem,
    Expense,
    BusinessAccount,
    AccountTransaction,
    Supplier,
    PurchaseOrder,
    PurchaseItem,
    Customer,
    CustomerPayment,
    StoreProfile,
)
from app.main import app
from app.finance_service import (
    calculate_financial_metrics,
    calculate_financial_analytics,
    calculate_inventory_valuation,
)
from app.accounts_service import (
    execute_account_transfer,
    compute_cash_closing_summary,
    record_account_transaction,
)


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    # Seed initial test data
    p1 = Product(
        sku="TEST-SKU-1",
        name="Masala Chai 250g",
        category="Beverages",
        unit="pack",
        price=100.0,
        cost_price=70.0,
        stock_qty=50.0,
        reorder_level=10.0,
        is_active=1,
        store_id="DEFAULT",
    )
    p2 = Product(
        sku="TEST-SKU-2",
        name="Basmati Rice 1kg",
        category="Grains",
        unit="kg",
        price=80.0,
        cost_price=50.0,
        stock_qty=100.0,
        reorder_level=20.0,
        is_active=1,
        store_id="DEFAULT",
    )
    session.add_all([p1, p2])

    cash_acc = BusinessAccount(
        store_id="DEFAULT",
        name="Cash Counter Drawer",
        account_type="CASH",
        opening_balance=2000.0,
        current_balance=2000.0,
        is_active=1,
    )
    bank_acc = BusinessAccount(
        store_id="DEFAULT",
        name="Current Bank Account",
        account_type="BANK",
        opening_balance=10000.0,
        current_balance=10000.0,
        is_active=1,
    )
    session.add_all([cash_acc, bank_acc])
    session.commit()

    yield session

    session.close()


@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


# ==============================================================================
# 1. FINANCIAL INTELLIGENCE & PROFIT TESTS (PHASE 1)
# ==============================================================================

def test_financial_metrics_calculation_and_precision(test_db):
    """Verifies Net Sales = Gross - Discount, COGS, Gross Profit, Operating Expenses, Net Profit, and Margins."""
    now = datetime.utcnow()
    # Create a completed sale
    sale = Sale(
        bill_number="BV-FIN-01",
        customer_name="Ramesh Kumar",
        payment_method="Cash",
        total=500.0,
        discount=20.0,  # 500 gross, 20 discount -> Net sales = 480
        store_id="DEFAULT",
        status="Completed",
        created_at=now,
    )
    test_db.add(sale)
    test_db.flush()

    # Line item 1: 3 units of TEST-SKU-1 (sold @ 100, cost 70)
    it1 = SaleItem(
        sale_id=sale.id,
        product_id=1,
        product_name="Masala Chai 250g",
        quantity=3.0,
        unit_price=100.0,
        unit_cost=70.0,
        line_total=300.0,
    )
    # Line item 2: 2.5 units of TEST-SKU-2 (sold @ 80, cost 50)
    it2 = SaleItem(
        sale_id=sale.id,
        product_id=2,
        product_name="Basmati Rice 1kg",
        quantity=2.5,
        unit_price=80.0,
        unit_cost=50.0,
        line_total=200.0,
    )
    test_db.add_all([it1, it2])

    # Record an active operating expense
    exp = Expense(
        store_id="DEFAULT",
        category="Electricity",
        amount=50.0,
        expense_date=now,
        payment_method="Cash",
        description="Shop lighting bill",
        status="ACTIVE",
    )
    test_db.add(exp)
    test_db.commit()

    metrics = calculate_financial_metrics(test_db, store_id="DEFAULT", period="today")

    assert metrics["gross_revenue"] == 500.0
    assert metrics["total_discounts"] == 20.0
    assert metrics["net_sales"] == 480.0
    # COGS = (3 * 70) + (2.5 * 50) = 210 + 125 = 335.0
    assert metrics["cogs"] == 335.0
    # Gross Profit = 480 - 335 = 145.0
    assert metrics["gross_profit"] == 145.0
    assert metrics["operating_expenses"] == 50.0
    # Net Profit = 145 - 50 = 95.0
    assert metrics["net_profit"] == 95.0
    # Gross Margin = (145 / 480) * 100 = 30.21%
    assert round(metrics["gross_profit_margin"], 2) == 30.21
    # Net Margin = (95 / 480) * 100 = 19.79%
    assert round(metrics["net_profit_margin"], 2) == 19.79


def test_zero_denominator_safety(test_db):
    """When sales are zero, gross and net profit margins must return 0.0 without division error."""
    metrics = calculate_financial_metrics(test_db, store_id="DEFAULT", period="today")
    assert metrics["gross_revenue"] == 0.0
    assert metrics["net_sales"] == 0.0
    assert metrics["gross_profit"] == 0.0
    assert metrics["gross_profit_margin"] == 0.0
    assert metrics["net_profit_margin"] == 0.0


def test_historical_cost_snapshot_invariance(test_db, client):
    """Verifies that changing today's product cost_price does NOT alter historical sale profit."""
    # Create sale via POS endpoint
    res = client.post("/api/sales", json={
        "bill_number": "BV-SNAP-01",
        "items": [{"sku": "TEST-SKU-1", "quantity": 2.0}]
    })
    assert res.status_code == 201

    # Check metrics: cost was 70 * 2 = 140, sale was 200 -> GP = 60
    m1 = calculate_financial_metrics(test_db, store_id="DEFAULT", period="today")
    assert m1["cogs"] == 140.0
    assert m1["gross_profit"] == 60.0

    # Now update Product 1 cost price to 95 (supplier price hiked)
    p = test_db.query(Product).filter(Product.sku == "TEST-SKU-1").first()
    p.cost_price = 95.0
    test_db.commit()

    # Verify historical metrics DID NOT CHANGE
    m2 = calculate_financial_metrics(test_db, store_id="DEFAULT", period="today")
    assert m2["cogs"] == 140.0
    assert m2["gross_profit"] == 60.0


def test_inventory_valuation_metrics(test_db):
    """Calculates total stock units, cost valuation, and retail selling value."""
    val = calculate_inventory_valuation(test_db, store_id="DEFAULT")
    # p1: 50 * 70 = 3500 cost, 50 * 100 = 5000 selling
    # p2: 100 * 50 = 5000 cost, 100 * 80 = 8000 selling
    # Total units = 150, Total cost = 8500, Total selling = 13000
    assert val["total_stock_units"] == 150.0
    assert val["total_cost_valuation"] == 8500.0
    assert val["total_selling_valuation"] == 13000.0
    assert val["potential_gross_profit"] == 4500.0


# ==============================================================================
# 2. EXPENSE MANAGEMENT TESTS (PHASE 2)
# ==============================================================================

def test_expense_crud_and_reversal_audit(client, test_db):
    """Tests creating, editing, reversing an expense, and audit history."""
    # 1. Create expense
    res = client.post("/api/expenses", json={
        "category": "Rent",
        "amount": 1500.0,
        "payment_method": "Cash",
        "description": "Shop monthly rent advance",
        "business_account_id": 1,
    })
    assert res.status_code == 201
    exp_id = res.json()["id"]

    # Check cash account deducted
    cash = test_db.query(BusinessAccount).filter(BusinessAccount.id == 1).first()
    assert cash.current_balance == 500.0  # 2000 - 1500

    # 2. Update expense description
    res_up = client.put(f"/api/expenses/{exp_id}", json={
        "description": "Shop monthly rent paid in full"
    })
    assert res_up.status_code == 200
    assert res_up.json()["description"] == "Shop monthly rent paid in full"

    # 3. Reverse expense safely with audit reason
    res_rev = client.post(f"/api/expenses/{exp_id}/reverse", json={
        "reason": "Accidental duplicate entry"
    })
    assert res_rev.status_code == 200
    assert res_rev.json()["status"] == "REVERSED"
    assert res_rev.json()["reversed_reason"] == "Accidental duplicate entry"

    # Check cash account restored
    test_db.refresh(cash)
    assert cash.current_balance == 2000.0


# ==============================================================================
# 3. BUSINESS ACCOUNTS & CASHBOOK TESTS (PHASE 3)
# ==============================================================================

def test_atomic_account_transfer_and_balance_protection(test_db):
    """Tests atomic transfer between accounts and insufficient funds rejection."""
    # Transfer 500 from cash (2000) to bank (10000)
    res = execute_account_transfer(
        db=test_db,
        from_account_id=1,
        to_account_id=2,
        amount=500.0,
        notes="Bank deposit from counter",
        store_id="DEFAULT",
    )
    assert res["success"] is True
    assert res["from_account"]["new_balance"] == 1500.0
    assert res["to_account"]["new_balance"] == 10500.0

    # Attempt transfer exceeding available funds (attempt 5000 from 1500)
    with pytest.raises(Exception) as exc:
        execute_account_transfer(
            db=test_db,
            from_account_id=1,
            to_account_id=2,
            amount=5000.0,
            store_id="DEFAULT",
        )
    assert "Insufficient funds" in str(exc.value)


def test_daily_cash_closing_summary(test_db):
    """Tests daily cash closing reconciliation."""
    summary = compute_cash_closing_summary(test_db, store_id="DEFAULT")
    assert summary["opening_cash"] == 2000.0
    assert summary["actual_current_balance"] == 2000.0


# ==============================================================================
# 4. SUPPLIERS & PURCHASES TESTS (PHASE 4)
# ==============================================================================

def test_supplier_and_purchase_order_flow(client, test_db):
    """Creates a supplier, places purchase order, updates stock atomically, and records payable."""
    # 1. Create supplier
    sup_res = client.post("/api/suppliers", json={
        "name": "Tata Consumer Wholesale",
        "contact_person": "Venkatesh Rao",
        "phone": "9876543210",
        "gstin": "36AABCT1332L1Z5"
    })
    assert sup_res.status_code == 201
    sup_id = sup_res.json()["id"]

    # 2. Record purchase of 20 packs of TEST-SKU-1 @ 65.0 (Total: 1300, Paid: 500)
    po_res = client.post("/api/purchases", json={
        "supplier_id": sup_id,
        "invoice_number": "TATA-INV-9901",
        "items": [{"sku": "TEST-SKU-1", "quantity": 20.0, "unit_cost": 65.0}],
        "paid_amount": 500.0,
        "payment_method": "BANK_TRANSFER",
        "business_account_id": 2,
    })
    assert po_res.status_code == 201
    po_data = po_res.json()
    assert po_data["total_amount"] == 1300.0
    assert po_data["paid_amount"] == 500.0
    assert po_data["payment_status"] == "PARTIAL"

    # Verify stock increased from 50 to 70
    p1 = test_db.query(Product).filter(Product.sku == "TEST-SKU-1").first()
    assert p1.stock_qty == 70.0
    assert p1.cost_price == 65.0  # Cost updated

    # Verify supplier balance is 1300 - 500 = 800
    sup = test_db.query(Supplier).filter(Supplier.id == sup_id).first()
    assert sup.outstanding_balance == 800.0


# ==============================================================================
# 5. CUSTOMER ACCOUNT MANAGEMENT TESTS (PHASE 5)
# ==============================================================================

def test_customer_management_and_ledger_payment(client, test_db):
    """Creates a customer, records payment, and verifies running balance."""
    # 1. Create customer
    c_res = client.post("/api/customers", json={
        "name": "Ananya Sharma",
        "phone": "9123456789"
    })
    assert c_res.status_code == 201
    cust_id = c_res.json()["id"]

    # Manually add credit balance
    cust = test_db.query(Customer).filter(Customer.id == cust_id).first()
    cust.outstanding_balance = 500.0
    test_db.commit()

    # 2. Record customer payment of 200
    pay_res = client.post(f"/api/customers/{cust_id}/payment", json={
        "amount": 200.0,
        "payment_method": "UPI",
        "business_account_id": 1,
    })
    assert pay_res.status_code == 201
    test_db.refresh(cust)
    assert cust.outstanding_balance == 300.0


# ==============================================================================
# 6. VOICE INTENTS FOR FINANCIAL & RETAIL OPERATIONS (PHASE 6)
# ==============================================================================

def test_voice_financial_and_accessibility_intents(client):
    """Tests natural language financial queries and confirmation actions."""
    # 1. Profit intent
    profit_res = client.post("/api/voice/process", json={"query": "how much profit did I make today?"})
    assert profit_res.status_code == 200
    assert profit_res.json()["intent"] == "CHECK_PROFIT"
    assert "gross profit" in profit_res.json()["reply"]

    # 2. Cash balance intent
    cash_res = client.post("/api/voice/process", json={"query": "how much money is in my cash account?"})
    assert cash_res.status_code == 200
    assert cash_res.json()["intent"] == "CHECK_CASH_BALANCE"
    assert "cash counter drawer currently holds" in cash_res.json()["reply"]

    # 3. Expense intent with confirmation guard
    exp_prompt = client.post("/api/voice/process", json={
        "query": "create an expense of 450 rupees for electricity",
        "confirmed": False
    })
    assert exp_prompt.status_code == 200
    assert exp_prompt.json()["action_required"] is True
    assert "Please confirm" in exp_prompt.json()["reply"]

    # Execute with confirmed=True
    exp_exec = client.post("/api/voice/process", json={
        "query": "create an expense of 450 rupees for electricity",
        "confirmed": True
    })
    assert exp_exec.status_code == 200
    assert exp_exec.json()["action_required"] is False
    assert "Successfully recorded an expense" in exp_exec.json()["reply"]
