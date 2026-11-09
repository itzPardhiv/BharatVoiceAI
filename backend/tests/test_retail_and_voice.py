import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Product, Sale, SaleItem, StockMovement
from app.main import app

@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = TestingSessionLocal()
    p1 = Product(sku="BV-TEST-01", name="Product Alpha", price=100.0, cost_price=75.0, stock_qty=50.0, reorder_level=10.0, is_active=1)
    p2 = Product(sku="BV-TEST-02", name="Product Beta", price=50.0, cost_price=30.0, stock_qty=5.0, reorder_level=10.0, is_active=1)
    db.add_all([p1, p2])
    db.commit()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)

    yield test_client

    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_product_lifecycle_create_update_deactivate(client):
    # 1. Create product
    payload = {
        "sku": "BV-NEW-01",
        "name": "New Cooking Oil 1L",
        "category": "Groceries",
        "unit": "litre",
        "price": 180.0,
        "cost_price": 140.0,
        "stock_qty": 25.0,
        "reorder_level": 5.0,
    }
    create_res = client.post("/api/products", json=payload)
    assert create_res.status_code == 201
    prod = create_res.json()
    prod_id = prod["id"]
    assert prod["sku"] == "BV-NEW-01"
    assert prod["is_active"] is True

    # 2. Duplicate SKU rejected
    dup_res = client.post("/api/products", json=payload)
    assert dup_res.status_code == 409

    # 3. Update product
    update_res = client.put(f"/api/products/{prod_id}", json={"price": 195.0, "name": "Premium Cooking Oil 1L"})
    assert update_res.status_code == 200
    assert update_res.json()["price"] == 195.0
    assert update_res.json()["name"] == "Premium Cooking Oil 1L"

    # 4. Deactivate product
    del_res = client.delete(f"/api/products/{prod_id}")
    assert del_res.status_code == 200
    assert del_res.json()["is_active"] is False


def test_stock_adjust_and_movements_history(client):
    # Get product 1
    p1_res = client.get("/api/products")
    p1 = p1_res.json()[0]
    initial_stock = p1["stock_qty"]

    # 1. Add stock
    adjust_res = client.post(
        f"/api/products/{p1['id']}/stock-adjust",
        json={"quantity_change": 20.0, "reason": "RESTOCK", "notes": "Wholesale delivery"}
    )
    assert adjust_res.status_code == 200
    assert adjust_res.json()["stock_qty"] == initial_stock + 20.0

    # 2. Deduct stock
    deduct_res = client.post(
        f"/api/products/{p1['id']}/stock-adjust",
        json={"quantity_change": -5.0, "reason": "DAMAGE", "notes": "Broken seal"}
    )
    assert deduct_res.status_code == 200
    assert deduct_res.json()["stock_qty"] == initial_stock + 15.0

    # 3. Reject negative stock
    over_deduct = client.post(
        f"/api/products/{p1['id']}/stock-adjust",
        json={"quantity_change": -500.0, "reason": "DAMAGE"}
    )
    assert over_deduct.status_code == 400
    assert "negative stock" in over_deduct.json()["detail"]

    # 4. Check movements list
    mov_res = client.get(f"/api/inventory/movements?product_id={p1['id']}")
    assert mov_res.status_code == 200
    movements = mov_res.json()
    assert len(movements) == 2
    assert movements[0]["reason"] == "DAMAGE"
    assert movements[1]["reason"] == "RESTOCK"


def test_direct_sale_creation_and_inventory_deduction(client):
    # Sale with two items
    sale_payload = {
        "bill_number": "INV-DIRECT-001",
        "customer_name": "Ramesh Kumar",
        "payment_method": "Cash",
        "items": [
            {"sku": "BV-TEST-01", "quantity": 2, "unit_price": 100.0},
            {"sku": "BV-TEST-02", "quantity": 1, "unit_price": 50.0},
        ]
    }
    res = client.post("/api/sales", json=sale_payload)
    assert res.status_code == 201
    sale_data = res.json()
    assert sale_data["total"] == 250.0
    assert len(sale_data["items"]) == 2

    # Check stock deduction
    p1 = client.get("/api/products").json()[0]
    assert p1["stock_qty"] == 48.0  # 50 - 2

    # Attempt to oversell
    oversell_payload = {
        "items": [{"sku": "BV-TEST-02", "quantity": 100}]
    }
    over_res = client.post("/api/sales", json=oversell_payload)
    assert over_res.status_code == 400
    assert "Insufficient stock" in over_res.json()["detail"]

    # Fetch sale detail by ID
    get_sale_res = client.get(f"/api/sales/{sale_data['id']}")
    assert get_sale_res.status_code == 200
    assert get_sale_res.json()["bill_number"] == "INV-DIRECT-001"


def test_voice_assistant_commands_and_safety(client):
    # 1. Voice revenue query
    rev_res = client.post("/api/voice/process", json={"query": "what is today's sales?"})
    assert rev_res.status_code == 200
    assert rev_res.json()["intent"] == "CHECK_REVENUE"
    assert "Today's total sales are" in rev_res.json()["reply"]

    # 2. Voice stock query
    stock_res = client.post("/api/voice/process", json={"query": "check stock of Product Alpha"})
    assert stock_res.status_code == 200
    assert stock_res.json()["intent"] == "CHECK_STOCK"
    assert "Current stock for Product Alpha" in stock_res.json()["reply"]

    # 3. Voice low stock query
    low_res = client.post("/api/voice/process", json={"query": "which items are low in stock?"})
    assert low_res.status_code == 200
    assert low_res.json()["intent"] == "LOW_STOCK"

    # 4. Voice adjust stock without confirmation -> requires confirmation
    adjust_prompt = client.post("/api/voice/process", json={"query": "add 10 packs of Product Alpha", "confirmed": False})
    assert adjust_prompt.status_code == 200
    assert adjust_prompt.json()["action_required"] is True
    assert "Please confirm" in adjust_prompt.json()["reply"]

    # 5. Voice adjust stock with confirmed=True -> executes safely
    adjust_exec = client.post("/api/voice/process", json={"query": "add 10 packs of Product Alpha", "confirmed": True})
    assert adjust_exec.status_code == 200
    assert adjust_exec.json()["action_required"] is False
    assert "Successfully added" in adjust_exec.json()["reply"]

    # 6. Voice service status
    status_res = client.get("/api/voice/status")
    assert status_res.status_code == 200
    assert len(status_res.json()["supported_languages"]) >= 6

