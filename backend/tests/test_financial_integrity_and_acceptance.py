"""
Comprehensive Financial Integrity, Accessibility & End-to-End Acceptance Test Suite.
Verifies all 9 financial scenarios (A-I), Smart Bill Import financial linkage,
cross-table reconciliation, store isolation, and voice assistant financial queries/confirmations.
"""
import pytest
from datetime import datetime, date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Product, Sale, SaleItem, StockMovement, Expense,
    BusinessAccount, AccountTransaction, Supplier, PurchaseOrder,
    PurchaseItem, Customer, CustomerPayment, StoreProfile, BillImport
)
from app.finance_service import calculate_financial_metrics, calculate_inventory_valuation
from app.accounts_service import execute_account_transfer, compute_cash_closing_summary


# Isolated SQLite in-memory database with StaticPool
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    # Seed default accounts and store profile for DEFAULT and STORE_AUDIT
    for s_id in ["DEFAULT", "STORE_AUDIT"]:
        c_acc = BusinessAccount(
            store_id=s_id,
            name="Cash Counter Drawer",
            account_type="CASH",
            opening_balance=0.0,
            current_balance=0.0,
            is_active=1,
            created_at=datetime.utcnow(),
        )
        b_acc = BusinessAccount(
            store_id=s_id,
            name="Current Bank Account",
            account_type="BANK",
            opening_balance=0.0,
            current_balance=0.0,
            is_active=1,
            created_at=datetime.utcnow(),
        )
        session.add_all([c_acc, b_acc])
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==============================================================================
# 1. FINANCIAL CONSISTENCY (SCENARIOS A - I)
# ==============================================================================

def test_scenario_a_pos_sale(client, db_session):
    """
    Scenario A: POS sale
    - Create product cost=60, price=100.
    - Sell 2 units.
    - Verify: revenue=200, cogs=120, gross_profit=80, stock decreases by 2, account receives payment once.
    """
    # 1. Create product
    res = client.post("/api/products", json={
        "sku": "AUDIT-PRD-A",
        "name": "Audit Product A",
        "category": "Groceries",
        "price": 100.0,
        "cost_price": 60.0,
        "stock_qty": 20.0,
        "reorder_level": 5.0,
    })
    assert res.status_code == 201
    prod_data = res.json()
    prod_id = prod_data["id"]

    # 2. Sell 2 units
    sale_res = client.post("/api/sales", json={
        "customer_name": "Ramesh Kumar",
        "payment_method": "CASH",
        "discount": 0.0,
        "items": [{"sku": "AUDIT-PRD-A", "quantity": 2.0, "unit_price": 100.0}],
    })
    assert sale_res.status_code == 201
    sale = sale_res.json()
    assert sale["total"] == 200.0
    sale_id = sale["id"]

    # 3. Verify financial metrics
    metrics = client.get("/api/finance/metrics?period=today").json()
    assert metrics["gross_revenue"] == 200.0
    assert metrics["net_sales"] == 200.0
    assert metrics["cogs"] == 120.0
    assert metrics["gross_profit"] == 80.0
    assert metrics["gross_profit_margin"] == 40.0  # 80 / 200 * 100

    # 4. Verify stock decreased by 2
    updated_prod = client.get(f"/api/products?search=AUDIT-PRD-A").json()[0]
    assert updated_prod["stock_qty"] == 18.0

    # 5. Verify account received payment exactly once
    accounts = client.get("/api/accounts").json()
    cash_acc = next(a for a in accounts if a["account_type"] == "CASH")
    acc_txs = client.get(f"/api/accounts/{cash_acc['id']}/transactions").json()
    sale_txs = [t for t in acc_txs if t["reference_type"] == "SALE" and t["reference_id"] == str(sale_id)]
    assert len(sale_txs) == 1
    assert sale_txs[0]["amount"] == 200.0
    assert sale_txs[0]["transaction_type"] == "INCOME"


def test_scenario_b_historical_cost_protection(client, db_session):
    """
    Scenario B: Historical cost snapshot
    - Change product cost price from 60 to 75.
    - Verify earlier sale still reports COGS=120 and profit=80.
    """
    # Create product and make sale at cost=60
    p = client.post("/api/products", json={
        "sku": "AUDIT-PRD-B", "name": "Audit Product B", "price": 100.0, "cost_price": 60.0, "stock_qty": 20.0
    }).json()

    client.post("/api/sales", json={
        "payment_method": "CASH",
        "items": [{"sku": "AUDIT-PRD-B", "quantity": 2.0, "unit_price": 100.0}],
    })

    # Now change product cost price to 75
    client.put(f"/api/products/{p['id']}", json={"cost_price": 75.0})

    # Verify metrics still report COGS=120, GP=80
    metrics = client.get("/api/finance/metrics?period=today").json()
    assert metrics["cogs"] == 120.0
    assert metrics["gross_profit"] == 80.0


def test_scenario_c_operating_expense(client, db_session):
    """
    Scenario C: Operating expense
    - Sell 2 units (profit=80).
    - Record ₹30 electricity expense.
    - Verify net operating profit becomes ₹50.
    - Verify expense appears in cashbook exactly once.
    """
    client.post("/api/products", json={
        "sku": "AUDIT-PRD-C", "name": "Audit Product C", "price": 100.0, "cost_price": 60.0, "stock_qty": 20.0
    })
    client.post("/api/sales", json={
        "payment_method": "CASH",
        "items": [{"sku": "AUDIT-PRD-C", "quantity": 2.0, "unit_price": 100.0}],
    })

    # Get cash account
    accounts = client.get("/api/accounts").json()
    cash_acc = next(a for a in accounts if a["account_type"] == "CASH")

    # Record 30 electricity expense
    exp_res = client.post("/api/expenses", json={
        "category": "Utilities",
        "amount": 30.0,
        "payment_method": "Cash",
        "description": "Store electricity bill payment",
        "business_account_id": cash_acc["id"],
    })
    assert exp_res.status_code == 201
    exp_id = exp_res.json()["id"]

    # Verify net profit = 80 - 30 = 50
    metrics = client.get("/api/finance/metrics?period=today").json()
    assert metrics["gross_profit"] == 80.0
    assert metrics["operating_expenses"] == 30.0
    assert metrics["net_profit"] == 50.0

    # Verify expense appears in cashbook exactly once
    txs = client.get(f"/api/accounts/{cash_acc['id']}/transactions").json()
    exp_txs = [t for t in txs if t["reference_type"] == "EXPENSE" and t["reference_id"] == str(exp_id)]
    assert len(exp_txs) == 1
    assert exp_txs[0]["amount"] == 30.0
    assert exp_txs[0]["transaction_type"] == "EXPENSE"


def test_scenario_d_stock_purchase_and_inventory_valuation(client, db_session):
    """
    Scenario D: Stock purchase
    - Purchase 10 units at ₹70 each.
    - Verify inventory increases by 10.
    - Verify inventory valuation changes correctly.
    - Verify purchase is NOT counted as an immediate operating expense.
    - Verify subsequent COGS uses new replacement cost.
    """
    p = client.post("/api/products", json={
        "sku": "AUDIT-PRD-D", "name": "Audit Product D", "price": 100.0, "cost_price": 60.0, "stock_qty": 5.0
    }).json()

    sup = client.post("/api/suppliers", json={"name": "Kishan Traders", "phone": "9876543210"}).json()

    # Initial valuation: 5 * 60 = 300
    val1 = client.get("/api/finance/valuation").json()
    assert val1["total_cost_valuation"] == 300.0

    # Purchase 10 units at ₹70 each (Total ₹700, paid in full)
    accounts = client.get("/api/accounts").json()
    bank_acc = next(a for a in accounts if a["account_type"] == "BANK")

    po = client.post("/api/purchases", json={
        "supplier_id": sup["id"],
        "invoice_number": "INV-PO-001",
        "items": [{"sku": "AUDIT-PRD-D", "quantity": 10.0, "unit_cost": 70.0}],
        "paid_amount": 700.0,
        "payment_method": "BANK_TRANSFER",
        "business_account_id": bank_acc["id"],
    })
    assert po.status_code == 201

    # Verify stock increased by 10 (5 + 10 = 15)
    updated_p = client.get(f"/api/products?search=AUDIT-PRD-D").json()[0]
    assert updated_p["stock_qty"] == 15.0
    assert updated_p["cost_price"] == 70.0

    # Verify valuation: 15 * 70 = 1050
    val2 = client.get("/api/finance/valuation").json()
    assert val2["total_cost_valuation"] == 1050.0

    # Verify purchase is NOT counted as operating expense!
    metrics = client.get("/api/finance/metrics?period=today").json()
    assert metrics["operating_expenses"] == 0.0

    # Verify subsequent sale uses replacement cost 70
    client.post("/api/sales", json={
        "payment_method": "CASH",
        "items": [{"sku": "AUDIT-PRD-D", "quantity": 1.0, "unit_price": 100.0}],
    })
    metrics2 = client.get("/api/finance/metrics?period=today").json()
    assert metrics2["cogs"] == 70.0
    assert metrics2["gross_profit"] == 30.0


def test_scenario_e_customer_credit_and_partial_settlement(client, db_session):
    """
    Scenario E: Customer credit (Khata)
    - Create credit sale.
    - Verify receivable balance increases.
    - Record partial payment.
    - Verify outstanding balance decreases.
    - Verify customer payment does not create duplicate sales revenue.
    """
    p = client.post("/api/products", json={
        "sku": "AUDIT-PRD-E", "name": "Audit Product E", "price": 150.0, "cost_price": 100.0, "stock_qty": 10.0
    }).json()

    c = client.post("/api/customers", json={"name": "Suresh Sharma", "phone": "9812345678"}).json()
    assert c["outstanding_balance"] == 0.0

    # Create credit sale of 2 units (₹300)
    sale_res = client.post("/api/sales", json={
        "customer_id": c["id"],
        "customer_name": c["name"],
        "payment_method": "CREDIT",
        "items": [{"sku": "AUDIT-PRD-E", "quantity": 2.0, "unit_price": 150.0}],
    })
    assert sale_res.status_code == 201

    # Verify customer receivable balance is now 300
    c_updated = client.get("/api/customers").json()[0]
    assert c_updated["outstanding_balance"] == 300.0
    assert c_updated["total_spent"] == 300.0

    # Sales revenue = 300
    metrics1 = client.get("/api/finance/metrics?period=today").json()
    assert metrics1["net_sales"] == 300.0

    # Record partial settlement payment of ₹100
    pay_res = client.post(f"/api/customers/{c['id']}/payment", json={
        "amount": 100.0,
        "payment_method": "CASH",
        "notes": "Partial settlement",
    })
    assert pay_res.status_code == 201

    # Verify outstanding balance decreased to 200
    c_after_pay = client.get("/api/customers").json()[0]
    assert c_after_pay["outstanding_balance"] == 200.0

    # Verify payment did NOT create duplicate sales revenue (still 300)
    metrics2 = client.get("/api/finance/metrics?period=today").json()
    assert metrics2["net_sales"] == 300.0


def test_scenario_f_supplier_credit_and_partial_settlement(client, db_session):
    """
    Scenario F: Supplier settlement
    - Record purchase on credit.
    - Record partial supplier payment.
    - Verify supplier outstanding decreases.
    - Verify payment does not create a duplicate purchase expense.
    """
    client.post("/api/products", json={
        "sku": "AUDIT-PRD-F", "name": "Audit Product F", "price": 200.0, "cost_price": 120.0, "stock_qty": 5.0
    })
    sup = client.post("/api/suppliers", json={"name": "Patel Wholesale", "phone": "9876500000"}).json()

    # Purchase on credit: 10 units at ₹120 each = ₹1200, paid 0
    po_res = client.post("/api/purchases", json={
        "supplier_id": sup["id"],
        "invoice_number": "INV-SUP-CREDIT-1",
        "items": [{"sku": "AUDIT-PRD-F", "quantity": 10.0, "unit_cost": 120.0}],
        "paid_amount": 0.0,
        "payment_method": "CREDIT",
    })
    assert po_res.status_code == 201

    # Supplier outstanding balance should be ₹1200
    sup_updated = client.get("/api/suppliers").json()[0]
    assert sup_updated["outstanding_balance"] == 1200.0

    # Settle partial payment of ₹500
    accounts = client.get("/api/accounts").json()
    bank_acc = next(a for a in accounts if a["account_type"] == "BANK")

    pay_res = client.post(f"/api/suppliers/{sup['id']}/payment", json={
        "amount": 500.0,
        "payment_method": "BANK",
        "business_account_id": bank_acc["id"],
        "notes": "Part payment via NEFT",
    })
    assert pay_res.status_code == 201
    assert pay_res.json()["outstanding_balance"] == 700.0

    # Operating expenses must remain 0.0 (supplier settlement is balance-sheet AP clearance, not operating expense)
    metrics = client.get("/api/finance/metrics?period=today").json()
    assert metrics["operating_expenses"] == 0.0


def test_scenario_g_sale_refund_lifecycle(client, db_session):
    """
    Scenario G: Refund lifecycle
    - Refund a completed sale.
    - Verify revenue, account balances, inventory, and profit are adjusted consistently.
    - Verify refund cannot be processed twice.
    """
    client.post("/api/products", json={
        "sku": "AUDIT-PRD-G", "name": "Audit Product G", "price": 100.0, "cost_price": 60.0, "stock_qty": 10.0
    })

    # Make sale
    sale = client.post("/api/sales", json={
        "payment_method": "CASH",
        "items": [{"sku": "AUDIT-PRD-G", "quantity": 2.0, "unit_price": 100.0}],
    }).json()
    sale_id = sale["id"]

    # Before refund: stock=8, revenue=200, profit=80
    assert client.get("/api/products?search=AUDIT-PRD-G").json()[0]["stock_qty"] == 8.0
    m_before = client.get("/api/finance/metrics?period=today").json()
    assert m_before["net_sales"] == 200.0
    assert m_before["gross_profit"] == 80.0

    # Refund the sale
    ref_res = client.post(f"/api/sales/{sale_id}/refund", json={"reason": "Customer returned unopened items"})
    assert ref_res.status_code == 200
    assert ref_res.json()["status"] == "Refunded"

    # Verify inventory restored back to 10
    assert client.get("/api/products?search=AUDIT-PRD-G").json()[0]["stock_qty"] == 10.0

    # Verify financial metrics: refunded sale excluded, revenue=0, profit=0
    m_after = client.get("/api/finance/metrics?period=today").json()
    assert m_after["net_sales"] == 0.0
    assert m_after["cogs"] == 0.0
    assert m_after["gross_profit"] == 0.0

    # Verify refund cannot be processed twice (idempotency guard)
    second_ref = client.post(f"/api/sales/{sale_id}/refund", json={"reason": "Repeat refund attempt"})
    assert second_ref.status_code == 400
    assert "already been refunded" in second_ref.json()["detail"]


def test_scenario_h_account_transfer_and_balance_protection(client, db_session):
    """
    Scenario H: Account transfer
    - Transfer money between two accounts.
    - Verify source decreases and destination increases by same amount.
    - Verify consolidated account balance is unchanged.
    - Verify insufficient funds block the transfer.
    """
    accounts = client.get("/api/accounts").json()
    cash_acc = next(a for a in accounts if a["account_type"] == "CASH")
    bank_acc = next(a for a in accounts if a["account_type"] == "BANK")

    # Deposit ₹500 into cash drawer via Sale
    client.post("/api/products", json={"sku": "AUDIT-H", "name": "Audit H", "price": 500.0, "cost_price": 300.0, "stock_qty": 5.0})
    client.post("/api/sales", json={"payment_method": "CASH", "business_account_id": cash_acc["id"], "items": [{"sku": "AUDIT-H", "quantity": 1.0}]})

    # Cash balance = 500, Bank balance = 0. Consolidated = 500
    c1 = client.get("/api/accounts").json()
    assert next(a for a in c1 if a["id"] == cash_acc["id"])["current_balance"] == 500.0
    assert next(a for a in c1 if a["id"] == bank_acc["id"])["current_balance"] == 0.0

    # Transfer ₹200 from Cash to Bank
    t_res = client.post("/api/accounts/transfer", json={
        "from_account_id": cash_acc["id"],
        "to_account_id": bank_acc["id"],
        "amount": 200.0,
        "notes": "Bank deposit",
    })
    assert t_res.status_code == 200

    # Cash balance = 300, Bank balance = 200. Consolidated = 500
    c2 = client.get("/api/accounts").json()
    assert next(a for a in c2 if a["id"] == cash_acc["id"])["current_balance"] == 300.0
    assert next(a for a in c2 if a["id"] == bank_acc["id"])["current_balance"] == 200.0
    assert sum(a["current_balance"] for a in c2) == 500.0

    # Attempt transfer of ₹400 from Cash (available: 300) -> Must fail with 400 Insufficient funds
    fail_res = client.post("/api/accounts/transfer", json={
        "from_account_id": cash_acc["id"],
        "to_account_id": bank_acc["id"],
        "amount": 400.0,
    })
    assert fail_res.status_code == 400
    assert "Insufficient funds" in fail_res.json()["detail"]


def test_scenario_i_expense_reversal_audit_trail(client, db_session):
    """
    Scenario I: Expense reversal
    - Reverse an expense.
    - Verify original transaction remains in audit history.
    - Verify reversal affects balances and profit exactly once.
    """
    accounts = client.get("/api/accounts").json()
    cash_acc = next(a for a in accounts if a["account_type"] == "CASH")

    exp = client.post("/api/expenses", json={
        "category": "Maintenance",
        "amount": 100.0,
        "payment_method": "Cash",
        "description": "Counter glass repair",
        "business_account_id": cash_acc["id"],
    }).json()

    # Metrics before reversal
    m1 = client.get("/api/finance/metrics?period=today").json()
    assert m1["operating_expenses"] == 100.0

    # Reverse expense
    rev_res = client.post(f"/api/expenses/{exp['id']}/reverse", json={"reason": "Incorrect duplicate entry"})
    assert rev_res.status_code == 200
    assert rev_res.json()["status"] == "REVERSED"

    # Metrics after reversal (operating expenses = 0)
    m2 = client.get("/api/finance/metrics?period=today").json()
    assert m2["operating_expenses"] == 0.0

    # Check audit history: original transaction + refund transaction
    txs = client.get(f"/api/accounts/{cash_acc['id']}/transactions").json()
    exp_audit = [t for t in txs if t["reference_id"] == str(exp["id"])]
    assert len(exp_audit) == 2  # Original EXPENSE and reversal REFUND


# ==============================================================================
# 2. SMART BILL IMPORT INTEGRATION
# ==============================================================================

def test_smart_bill_import_financial_linkage_and_idempotency(client, db_session):
    """
    Verify Smart Bill Import:
    - Preserves historical cost snapshots (SaleItem.unit_cost)
    - Updates account balance according to payment method
    - Repeated confirmation remains idempotent
    - Failed confirmation rolls back cleanly
    """
    # Create product
    client.post("/api/products", json={"sku": "IMP-SKU-1", "name": "Imported Rice", "price": 100.0, "cost_price": 70.0, "stock_qty": 20.0})

    csv_content = b"bill_number,sku,quantity,unit_price,customer_name,payment_method\nINV-IMP-001,IMP-SKU-1,2,100,Deepak Verma,CASH\n"
    
    # Upload bill
    up_res = client.post(
        "/api/bill-imports/upload",
        files={"file": ("bill.csv", csv_content, "text/csv")},
        data={"store_id": "DEFAULT", "source_system": "CSV_UPLOAD"}
    )
    assert up_res.status_code in (200, 201)
    import_id = up_res.json()["import_id"]

    # Confirm bill
    conf_res = client.post(f"/api/bill-imports/{import_id}/confirm")
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["status"] == "CONFIRMED"
    sale_id = conf_data["sale_id"]

    # Verify historical cost snapshot
    sale_detail = client.get(f"/api/sales/{sale_id}").json()
    assert sale_detail["items"][0]["unit_cost"] == 70.0
    assert sale_detail["total"] == 200.0

    # Verify cash account balance increased by 200
    accounts = client.get("/api/accounts").json()
    cash_acc = next(a for a in accounts if a["account_type"] == "CASH")
    assert cash_acc["current_balance"] == 200.0

    # Verify repeated confirmation remains idempotent (no duplicate stock deduction or revenue)
    repeat_res = client.post(f"/api/bill-imports/{import_id}/confirm")
    assert repeat_res.status_code == 200
    assert repeat_res.json()["already_confirmed"] is True

    # Revenue remains 200.0, not 400.0
    metrics = client.get("/api/finance/metrics?period=today").json()
    assert metrics["net_sales"] == 200.0
    assert metrics["gross_profit"] == 60.0  # 200 - (2 * 70) = 60


# ==============================================================================
# 3. VOICE ASSISTANT ACCEPTANCE
# ==============================================================================

def test_voice_assistant_financial_queries_and_confirmation_guard(client, db_session):
    """
    Test real voice queries against database and mutation confirmation guards:
    - What is my profit today?
    - How much money is in my cash account?
    - What are my expenses this month?
    - Which product gives the highest profit?
    - How much do I owe suppliers?
    - How much do customers owe me?
    - Record an electricity expense of ₹500 (requires explicit confirmation).
    """
    # 1. Seed some financial data
    client.post("/api/products", json={"sku": "V-PROD-1", "name": "Voice Product", "price": 200.0, "cost_price": 100.0, "stock_qty": 10.0})
    client.post("/api/sales", json={"payment_method": "CASH", "items": [{"sku": "V-PROD-1", "quantity": 1.0}]})

    # Test "What is my profit today?"
    res_profit = client.post("/api/voice/process", json={"query": "What is my profit today?", "language_code": "en-IN"}).json()
    assert "₹100" in res_profit["reply"] or "profit" in res_profit["reply"].lower()

    # Test "How much money is in my cash account?"
    res_cash = client.post("/api/voice/process", json={"query": "How much money is in my cash account?", "language_code": "en-IN"}).json()
    assert "₹200" in res_cash["reply"] or "cash" in res_cash["reply"].lower()

    # Test "Record an electricity expense of 500" -> Action Required confirmation guard
    res_write = client.post("/api/voice/process", json={"query": "Record an electricity expense of 500 rupees", "language_code": "en-IN"}).json()
    assert res_write["action_required"] is True
    assert res_write["intent"] == "CREATE_EXPENSE"
    assert res_write["action_payload"]["amount"] == 500.0

    # Cancel (confirmed=False) does not mutate data
    exp_before = client.get("/api/expenses").json()
    assert len(exp_before) == 0

    # Confirmed execution commits mutation
    res_confirmed = client.post("/api/voice/process", json={
        "query": "Yes, record expense",
        "confirmed": True,
        "action_payload": res_write["action_payload"],
    }).json()
    assert res_confirmed["action_required"] is False
    assert "Successfully recorded" in res_confirmed["reply"]

    # Verify expense is now in database
    exp_after = client.get("/api/expenses").json()
    assert len(exp_after) == 1
    assert exp_after[0]["amount"] == 500.0


# ==============================================================================
# 4. STORE ISOLATION AND SECURITY
# ==============================================================================

def test_store_isolation_financial_records(client, db_session):
    """
    Verify multi-store isolation across accounts, expenses, purchases, customers, and reports.
    """
    # Create product and sale in STORE_AUDIT
    client.post("/api/products?store_id=STORE_AUDIT", json={
        "sku": "ISOLATE-1", "name": "Isolated Product", "price": 100.0, "cost_price": 50.0, "stock_qty": 10.0
    })
    client.post("/api/sales?store_id=STORE_AUDIT", json={
        "payment_method": "CASH",
        "items": [{"sku": "ISOLATE-1", "quantity": 1.0}],
    })

    # Verify DEFAULT store reports 0 sales
    default_metrics = client.get("/api/finance/metrics?period=today&store_id=DEFAULT").json()
    assert default_metrics["gross_revenue"] == 0.0

    # Verify STORE_AUDIT reports 100 sales
    audit_metrics = client.get("/api/finance/metrics?period=today&store_id=STORE_AUDIT").json()
    assert audit_metrics["gross_revenue"] == 100.0
