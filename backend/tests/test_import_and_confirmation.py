import pytest
import sqlite3
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Product, Sale, SaleItem, BillImport, StockMovement
from app.bill_import_service import (
    register_bill_import_preview,
    get_bill_import_preview,
    confirm_bill_import,
    reject_bill_import_preview,
    list_bill_imports,
    DuplicateClassification,
)


@pytest.fixture
def db_session():
    """Provides an isolated, in-memory SQLite database session with foreign keys enabled."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()

    # Seed test products
    p1 = Product(sku="BV-1001", name="Tata Salt 1 kg", price=28.0, cost_price=22.0, stock_qty=40.0)
    p2 = Product(sku="BV-1002", name="Amul Butter 100 g", price=56.0, cost_price=48.0, stock_qty=10.0)
    session.add_all([p1, p2])
    session.commit()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


VALID_CSV = (
    "bill_number,sku,quantity,unit_price,customer_name,payment_method\n"
    "INV-9001,BV-1001,2,28,Ramesh,UPI\n"
    "INV-9001,BV-1002,1,56,Ramesh,UPI\n"
)


def test_register_valid_preview_creates_validated_record_no_sale(db_session):
    """Registering a valid CSV creates a VALIDATED BillImport record but no Sales or StockMovements."""
    res = register_bill_import_preview(
        db=db_session,
        file_content=VALID_CSV,
        filename="sales_bill.csv",
        store_id="DEFAULT",
    )

    assert res.import_id is not None
    assert res.status == "VALIDATED"
    assert res.can_confirm is True
    assert res.total_items == 2
    assert res.total_amount == 112.0  # (2*28) + (1*56)
    assert res.source_bill_number == "INV-9001"
    assert res.duplicate_status.classification == DuplicateClassification.NEW_IMPORT

    # Invariant: No Sales, SaleItems, or StockMovements created at preview
    assert db_session.query(Sale).count() == 0
    assert db_session.query(SaleItem).count() == 0
    assert db_session.query(StockMovement).count() == 0

    # Stock unchanged
    p1 = db_session.query(Product).filter_by(sku="BV-1001").first()
    assert p1.stock_qty == 40.0


def test_register_invalid_csv_creates_rejected_record(db_session):
    """CSV with unknown SKU is registered with REJECTED status and cannot be confirmed."""
    invalid_csv = (
        "bill_number,sku,quantity,unit_price\n"
        "INV-BAD-01,UNKNOWN-SKU-999,2,50\n"
    )
    res = register_bill_import_preview(
        db=db_session,
        file_content=invalid_csv,
        filename="bad_bill.csv",
    )

    assert res.import_id is not None
    assert res.status == "REJECTED"
    assert res.can_confirm is False
    assert len(res.preview_data.errors) > 0
    assert res.preview_data.errors[0]["code"] == "UNKNOWN_SKU"


def test_idempotent_registration_resumes_existing_preview(db_session):
    """Uploading the exact same active bill again returns existing preview without inserting duplicate row."""
    res1 = register_bill_import_preview(db=db_session, file_content=VALID_CSV, filename="bill1.csv")
    initial_count = db_session.query(BillImport).count()

    res2 = register_bill_import_preview(db=db_session, file_content=VALID_CSV, filename="bill1.csv")

    assert res2.import_id == res1.import_id
    assert db_session.query(BillImport).count() == initial_count  # No second row inserted!
    assert res2.duplicate_status.classification == DuplicateClassification.PENDING_IMPORT_EXISTS


def test_atomic_confirmation_creates_sale_and_deducts_stock(db_session):
    """Confirming a validated preview atomically creates Sale, SaleItems, deducts stock, and logs movements."""
    preview = register_bill_import_preview(db=db_session, file_content=VALID_CSV, filename="bill1.csv")

    conf = confirm_bill_import(db=db_session, import_id=preview.import_id)

    assert conf.status == "CONFIRMED"
    assert conf.sale_id > 0
    assert conf.bill_number == "INV-9001"
    assert conf.already_confirmed is False
    assert len(conf.deductions) == 2

    # Verify Sale in DB
    sale = db_session.query(Sale).filter_by(id=conf.sale_id).first()
    assert sale is not None
    assert sale.bill_number == "INV-9001"
    assert sale.total == 112.0
    assert sale.customer_name == "Ramesh"
    assert sale.payment_method == "UPI"

    # Verify SaleItems in DB
    items = db_session.query(SaleItem).filter_by(sale_id=sale.id).all()
    assert len(items) == 2

    # Verify stock deduction
    p1 = db_session.query(Product).filter_by(sku="BV-1001").first()
    p2 = db_session.query(Product).filter_by(sku="BV-1002").first()
    assert p1.stock_qty == 38.0  # 40 - 2
    assert p2.stock_qty == 9.0   # 10 - 1

    # Verify StockMovements in DB
    movements = db_session.query(StockMovement).filter_by(bill_import_id=preview.import_id).all()
    assert len(movements) == 2
    for m in movements:
        assert m.quantity_change < 0
        assert m.reason == "BILL_IMPORT"
        assert m.sale_id == sale.id


def test_idempotent_confirmation_does_not_deduct_stock_twice(db_session):
    """Repeatedly calling confirm on an already confirmed import is a safe idempotent no-op."""
    preview = register_bill_import_preview(db=db_session, file_content=VALID_CSV, filename="bill1.csv")

    # First confirmation
    conf1 = confirm_bill_import(db=db_session, import_id=preview.import_id)
    p1_stock_after_first = db_session.query(Product).filter_by(sku="BV-1001").first().stock_qty
    sales_count = db_session.query(Sale).count()
    movements_count = db_session.query(StockMovement).count()

    # Second confirmation
    conf2 = confirm_bill_import(db=db_session, import_id=preview.import_id)

    assert conf2.already_confirmed is True
    assert conf2.sale_id == conf1.sale_id
    assert db_session.query(Sale).count() == sales_count
    assert db_session.query(StockMovement).count() == movements_count
    assert db_session.query(Product).filter_by(sku="BV-1001").first().stock_qty == p1_stock_after_first


def test_confirmation_fails_safely_on_insufficient_stock(db_session):
    """If stock is depleted between preview and confirmation, confirmation rolls back with zero side effects."""
    preview = register_bill_import_preview(db=db_session, file_content=VALID_CSV, filename="bill1.csv")

    # Manually drop stock to 1 (bill requires 2)
    p1 = db_session.query(Product).filter_by(sku="BV-1001").first()
    p1.stock_qty = 1.0
    db_session.commit()

    with pytest.raises(ValueError, match="Insufficient stock"):
        confirm_bill_import(db=db_session, import_id=preview.import_id)

    # Invariants: Zero sales, zero stock movements, import not confirmed
    assert db_session.query(Sale).count() == 0
    assert db_session.query(StockMovement).count() == 0
    imp = db_session.query(BillImport).filter_by(id=preview.import_id).first()
    assert imp.status == "VALIDATED"  # Still validated, not confirmed


def test_reject_preview_allows_subsequent_retry(db_session):
    """Retailer rejecting a preview updates status to REJECTED and permits retrying the source bill."""
    preview = register_bill_import_preview(db=db_session, file_content=VALID_CSV, filename="bill1.csv")

    # Reject preview
    rejected = reject_bill_import_preview(db=db_session, import_id=preview.import_id, reason="Customer canceled")
    assert rejected.status == "REJECTED"

    # Now re-uploading the same bill number is permitted as a new attempt
    retry_csv = (
        "bill_number,sku,quantity,unit_price\n"
        "INV-9001,BV-1001,1,28\n"
    )
    retry_res = register_bill_import_preview(db=db_session, file_content=retry_csv, filename="bill_v2.csv")
    assert retry_res.import_id != preview.import_id
    assert retry_res.status == "VALIDATED"
    assert retry_res.duplicate_status.classification == DuplicateClassification.PREVIOUS_IMPORT_REJECTED


def test_store_isolation_enforced(db_session):
    """Accessing or confirming an import from another store raises PermissionError."""
    preview = register_bill_import_preview(
        db=db_session,
        file_content=VALID_CSV,
        filename="bill.csv",
        store_id="STORE_NORTH",
    )

    with pytest.raises(PermissionError):
        get_bill_import_preview(db=db_session, import_id=preview.import_id, store_id="STORE_SOUTH")

    with pytest.raises(PermissionError):
        confirm_bill_import(db=db_session, import_id=preview.import_id, store_id="STORE_SOUTH")

    with pytest.raises(PermissionError):
        reject_bill_import_preview(db=db_session, import_id=preview.import_id, store_id="STORE_SOUTH")

