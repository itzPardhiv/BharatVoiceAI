import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import io

from app.database import Base, get_db
from app.models import Product, Sale, SaleItem, StockMovement, BillImport
from app.main import app

CSV_BILL_CONTENT = (
    "bill_number,sku,quantity,unit_price,customer_name,payment_method\n"
    "INV-E2E-999,SKU-E2E-1,3,150.00,Sunil Sharma,UPI\n"
    "INV-E2E-999,SKU-E2E-2,2,80.00,Sunil Sharma,UPI\n"
)

@pytest.fixture
def e2e_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)

    yield test_client, TestingSession

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_complete_autonomous_retail_lifecycle(e2e_client):
    client, Session = e2e_client

    # 1. Start application & verify API health
    health_res = client.get("/api/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"

    # 2. Create products
    p1 = client.post("/api/products", json={
        "sku": "SKU-E2E-1",
        "name": "Basmati Rice 5kg",
        "category": "Groceries",
        "unit": "pack",
        "price": 150.0,
        "cost_price": 120.0,
        "stock_qty": 10.0,
        "reorder_level": 4.0
    }).json()
    assert p1["id"] is not None

    p2 = client.post("/api/products", json={
        "sku": "SKU-E2E-2",
        "name": "Sunflower Oil 1L",
        "category": "Groceries",
        "unit": "litre",
        "price": 80.0,
        "cost_price": 60.0,
        "stock_qty": 5.0,
        "reorder_level": 2.0
    }).json()
    assert p2["id"] is not None

    # 3. Add inventory via Stock Adjustment
    adjust_res = client.post(f"/api/products/{p1['id']}/stock-adjust", json={
        "quantity_change": 15.0,
        "reason": "RESTOCK",
        "notes": "Direct distributor supply"
    })
    assert adjust_res.status_code == 200
    assert adjust_res.json()["stock_qty"] == 25.0  # 10 + 15

    # 4. Upload valid CSV bill for import preview
    files = {"file": ("retail_bill.csv", io.BytesIO(CSV_BILL_CONTENT.encode("utf-8")), "text/csv")}
    upload_res = client.post("/api/bill-imports/upload", files=files)
    assert upload_res.status_code == 201
    preview = upload_res.json()
    import_id = preview["import_id"]
    assert preview["status"] == "VALIDATED"
    assert preview["can_confirm"] is True
    assert preview["total_amount"] == 610.0  # (3 * 150) + (2 * 80) = 450 + 160 = 610

    # Verify preview created ZERO sales and deducted ZERO stock
    with Session() as s:
        assert s.query(Sale).count() == 0
        rice = s.query(Product).filter_by(sku="SKU-E2E-1").first()
        assert rice.stock_qty == 25.0

    # 5. Confirm the imported bill
    conf_res = client.post(f"/api/bill-imports/{import_id}/confirm")
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["status"] == "CONFIRMED"
    sale_id = conf_data["sale_id"]
    assert sale_id > 0

    # 6. Verify Sale and Stock Deductions
    with Session() as s:
        sale = s.query(Sale).filter_by(id=sale_id).first()
        assert sale.bill_number == "INV-E2E-999"
        assert sale.total == 610.0
        assert sale.status == "Completed"

        # Stock deducted exactly once
        rice = s.query(Product).filter_by(sku="SKU-E2E-1").first()
        oil = s.query(Product).filter_by(sku="SKU-E2E-2").first()
        assert rice.stock_qty == 22.0  # 25 - 3
        assert oil.stock_qty == 3.0    # 5 - 2

        # Stock movements recorded with negative changes
        movements = s.query(StockMovement).filter_by(bill_import_id=import_id).all()
        assert len(movements) == 2
        assert all(m.quantity_change < 0 for m in movements)
        assert all(m.reason == "BILL_IMPORT" for m in movements)

    # 7. Verify Dashboard revenue reflects imported sale
    dash_res = client.get("/api/dashboard/summary")
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    assert dash_data["today_sales"] == 610.0
    assert dash_data["today_bills"] == 1

    # 8. Re-upload the exact same bill -> Verify Duplicate Protection (ALREADY_CONFIRMED)
    files_repeat = {"file": ("retail_bill.csv", io.BytesIO(CSV_BILL_CONTENT.encode("utf-8")), "text/csv")}
    reup_res = client.post("/api/bill-imports/upload", files=files_repeat)
    assert reup_res.status_code == 409
    assert reup_res.json()["duplicate_status"]["classification"] == "ALREADY_CONFIRMED"
    assert reup_res.json()["duplicate_status"]["is_blocked"] is True

    # 9. Voice query verification on new real data
    voice_res = client.post("/api/voice/process", json={"query": "how much revenue today?"})
    assert voice_res.status_code == 200
    assert "₹610.0" in voice_res.json()["reply"]

    # Voice check stock of basmati rice
    voice_stock = client.post("/api/voice/process", json={"query": "check stock of Basmati Rice"})
    assert voice_stock.status_code == 200
    assert "22.0" in voice_stock.json()["reply"]

