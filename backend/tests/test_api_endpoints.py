import io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Product, Sale, BillImport, StockMovement
from app.main import app

VALID_CSV = (
    "bill_number,sku,quantity,unit_price,customer_name,payment_method\n"
    "INV-API-01,BV-1001,2,28,Aman,UPI\n"
    "INV-API-01,BV-1002,1,56,Aman,UPI\n"
)


@pytest.fixture
def test_client():
    """Provides a TestClient with an isolated in-memory database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = TestingSessionLocal()
    # Seed products
    p1 = Product(sku="BV-1001", name="Tata Salt 1 kg", price=28.0, cost_price=22.0, stock_qty=50.0)
    p2 = Product(sku="BV-1002", name="Amul Butter 100 g", price=56.0, cost_price=48.0, stock_qty=20.0)
    db.add_all([p1, p2])
    db.commit()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield client

    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_upload_valid_csv(test_client):
    """POST /api/bill-imports/upload successfully registers a preview in VALIDATED status."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    response = test_client.post("/api/bill-imports/upload", files=files)

    assert response.status_code == 201
    data = response.json()
    assert data["import_id"] is not None
    assert data["status"] == "VALIDATED"
    assert data["can_confirm"] is True
    assert data["total_items"] == 2
    assert data["total_amount"] == 112.0
    assert data["source_bill_number"] == "INV-API-01"


def test_upload_invalid_file_extension(test_client):
    """Uploading non-CSV file returns 400 Bad Request."""
    files = {"file": ("malicious.exe", io.BytesIO(b"binary content"), "application/octet-stream")}
    response = test_client.post("/api/bill-imports/upload", files=files)

    assert response.status_code == 400
    assert "Only CSV files" in response.json()["detail"]


def test_upload_oversized_file(test_client):
    """Uploading a file exceeding 5MB returns 413 Entity Too Large."""
    oversized = b"a" * (6 * 1024 * 1024)  # 6 MB
    files = {"file": ("huge.csv", io.BytesIO(oversized), "text/csv")}
    response = test_client.post("/api/bill-imports/upload", files=files)

    assert response.status_code == 413
    assert "exceeds maximum permitted size" in response.json()["detail"]


def test_get_preview_by_id(test_client):
    """GET /api/bill-imports/{id} retrieves existing preview details."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    upload_res = test_client.post("/api/bill-imports/upload", files=files)
    import_id = upload_res.json()["import_id"]

    res = test_client.get(f"/api/bill-imports/{import_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["import_id"] == import_id
    assert data["status"] == "VALIDATED"
    assert len(data["preview_data"]["items"]) == 2


def test_store_isolation_in_api(test_client):
    """Requesting an import from another store returns 403 Forbidden."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    upload_res = test_client.post("/api/bill-imports/upload", files=files, data={"store_id": "STORE_1"})
    import_id = upload_res.json()["import_id"]

    # Attempt access with a different store_id
    res = test_client.get(f"/api/bill-imports/{import_id}?store_id=STORE_2")
    assert res.status_code == 403
    assert "Access denied" in res.json()["detail"]


def test_confirm_import_and_idempotency(test_client):
    """POST /api/bill-imports/{id}/confirm creates sale, and repeated calls are safe."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    upload_res = test_client.post("/api/bill-imports/upload", files=files)
    import_id = upload_res.json()["import_id"]

    # First confirmation
    res1 = test_client.post(f"/api/bill-imports/{import_id}/confirm")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "CONFIRMED"
    assert data1["sale_id"] > 0
    assert data1["already_confirmed"] is False

    # Second confirmation (idempotent no-op)
    res2 = test_client.post(f"/api/bill-imports/{import_id}/confirm")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["already_confirmed"] is True
    assert data2["sale_id"] == data1["sale_id"]


def test_reject_preview_in_api(test_client):
    """POST /api/bill-imports/{id}/reject marks preview as REJECTED."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    upload_res = test_client.post("/api/bill-imports/upload", files=files)
    import_id = upload_res.json()["import_id"]

    reject_res = test_client.post(
        f"/api/bill-imports/{import_id}/reject",
        json={"reason": "Incorrect items listed"},
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "REJECTED"

    # Attempting to confirm a rejected bill returns 400
    confirm_res = test_client.post(f"/api/bill-imports/{import_id}/confirm")
    assert confirm_res.status_code == 400


def test_list_imports_endpoint(test_client):
    """GET /api/bill-imports returns historical list with pagination."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    test_client.post("/api/bill-imports/upload", files=files)

    res = test_client.get("/api/bill-imports?limit=10")
    assert res.status_code == 200
    items = res.json()
    assert len(items) >= 1
    assert items[0]["source_bill_number"] == "INV-API-01"


def test_authentication_enforcement_and_unauthorized_rejection(test_client, monkeypatch):
    """When BHARATVOICE_STORE_API_KEYS is configured, unauthenticated requests are rejected with 401."""
    monkeypatch.setenv("BHARATVOICE_STORE_API_KEYS", '{"key_store1": "STORE_1", "key_store2": "STORE_2"}')

    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}

    # 1. Unauthenticated request without header -> 401 Unauthorized
    res_no_auth = test_client.post("/api/bill-imports/upload", files=files)
    assert res_no_auth.status_code == 401
    assert "Authentication required" in res_no_auth.json()["detail"]

    # 2. Request with invalid key -> 401 Unauthorized
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    res_bad_key = test_client.post("/api/bill-imports/upload", files=files, headers={"X-Store-API-Key": "invalid_key"})
    assert res_bad_key.status_code == 401
    assert "Invalid or unrecognized" in res_bad_key.json()["detail"]

    # 3. Valid key for STORE_1 -> 201 Created, bound to STORE_1
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    res_valid = test_client.post("/api/bill-imports/upload", files=files, headers={"X-Store-API-Key": "key_store1"})
    assert res_valid.status_code == 201
    assert res_valid.json()["store_id"] == "STORE_1"


def test_cross_store_access_denied_with_authenticated_credentials(test_client, monkeypatch):
    """Caller authenticated for STORE_1 cannot read, confirm, or reject STORE_2's imports."""
    monkeypatch.setenv("BHARATVOICE_STORE_API_KEYS", '{"key_store1": "STORE_1", "key_store2": "STORE_2"}')

    # Upload bill as STORE_2
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    res = test_client.post("/api/bill-imports/upload", files=files, headers={"X-Store-API-Key": "key_store2"})
    assert res.status_code == 201
    import_id = res.json()["import_id"]

    # Store 1 attempts to read Store 2 import -> 403 Forbidden
    read_res = test_client.get(f"/api/bill-imports/{import_id}", headers={"X-Store-API-Key": "key_store1"})
    assert read_res.status_code == 403
    assert "Access denied" in read_res.json()["detail"]

    # Store 1 attempts to confirm Store 2 import -> 403 Forbidden
    confirm_res = test_client.post(f"/api/bill-imports/{import_id}/confirm", headers={"X-Store-API-Key": "key_store1"})
    assert confirm_res.status_code == 403
    assert "Access denied" in confirm_res.json()["detail"]

    # Store 1 attempts to reject Store 2 import -> 403 Forbidden
    reject_res = test_client.post(f"/api/bill-imports/{import_id}/reject", headers={"X-Store-API-Key": "key_store1"})
    assert reject_res.status_code == 403
    assert "Access denied" in reject_res.json()["detail"]


def test_reupload_confirmed_bill_returns_conflict(test_client):
    """Re-uploading an already confirmed bill returns 409 Conflict with ALREADY_CONFIRMED."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    upload_res = test_client.post("/api/bill-imports/upload", files=files)
    import_id = upload_res.json()["import_id"]

    # Confirm the bill
    confirm_res = test_client.post(f"/api/bill-imports/{import_id}/confirm")
    assert confirm_res.status_code == 200

    # Re-upload the exact same file
    files2 = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    reupload_res = test_client.post("/api/bill-imports/upload", files=files2)
    assert reupload_res.status_code == 409
    assert reupload_res.json()["duplicate_status"]["classification"] == "ALREADY_CONFIRMED"
    assert reupload_res.json()["duplicate_status"]["is_blocked"] is True


def test_corrupted_persisted_preview_fails_cleanly(test_client):
    """If preview json data is corrupted in the database, confirmation returns 400."""
    files = {"file": ("bill.csv", io.BytesIO(VALID_CSV.encode("utf-8")), "text/csv")}
    upload_res = test_client.post("/api/bill-imports/upload", files=files)
    import_id = upload_res.json()["import_id"]

    # Corrupt raw_data_json directly in DB via dependency override or query
    from app.database import get_db
    # Get current session from override
    db_gen = test_client.app.dependency_overrides[get_db]()
    db = next(db_gen)
    imp = db.query(BillImport).filter(BillImport.id == import_id).first()
    imp.raw_data_json = "{not valid json!"
    db.commit()

    confirm_res = test_client.post(f"/api/bill-imports/{import_id}/confirm")
    assert confirm_res.status_code == 400
    assert "malformed" in confirm_res.json()["detail"].lower()

