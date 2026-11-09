import io
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from app.database import Base
from app.models import Product, Sale, SaleItem, BillImport, StockMovement
from app.bill_import_service import (
    register_bill_import_preview,
    confirm_bill_import,
    reject_bill_import_preview,
    DuplicateClassification,
)


@pytest.fixture
def isolated_db():
    """Provides a fresh isolated database for each E2E / fault injection scenario."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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

    # Seed products
    products = [
        Product(sku="SKU-A", name="Item Alpha", price=100.0, cost_price=80.0, stock_qty=50.0),
        Product(sku="SKU-B", name="Item Beta", price=50.0, cost_price=35.0, stock_qty=20.0),
        Product(sku="SKU-C", name="Item Gamma", price=25.0, cost_price=18.0, stock_qty=5.0),
    ]
    session.add_all(products)
    session.commit()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ==============================================================================
# Business Scenarios 1 - 12: Lifecycle & Idempotency
# ==============================================================================

def test_scenario_01_valid_bill_imports_and_confirms(isolated_db):
    """1. A new valid bill imports and confirms successfully."""
    csv_data = "bill_number,sku,quantity,unit_price\nBILL-001,SKU-A,2,100\nBILL-001,SKU-B,1,50\n"
    preview = register_bill_import_preview(isolated_db, csv_data, "bill.csv")
    assert preview.can_confirm is True

    conf = confirm_bill_import(isolated_db, preview.import_id)
    assert conf.status == "CONFIRMED"
    assert conf.already_confirmed is False

    # Check sale
    sale = isolated_db.query(Sale).filter_by(id=conf.sale_id).first()
    assert sale.total == 250.0

    # Reconciliation invariant
    prod_a = isolated_db.query(Product).filter_by(sku="SKU-A").first()
    assert prod_a.stock_qty == 48.0  # 50 - 2


def test_scenario_02_same_bytes_uploaded_again(isolated_db):
    """2. The same bytes are uploaded again (returns existing preview or blocks if confirmed)."""
    csv_data = "bill_number,sku,quantity,unit_price\nBILL-002,SKU-A,1,100\n"
    res1 = register_bill_import_preview(isolated_db, csv_data, "bill.csv")
    confirm_bill_import(isolated_db, res1.import_id)

    # Upload exact same bytes again
    res2 = register_bill_import_preview(isolated_db, csv_data, "bill.csv")
    assert res2.duplicate_status.is_blocked is True
    assert res2.duplicate_status.classification == DuplicateClassification.ALREADY_CONFIRMED


def test_scenario_03_reformatted_same_source_bill(isolated_db):
    """3. The same source bill is reformatted (different row order / whitespace) and reuploaded."""
    csv1 = "bill_number,sku,quantity,unit_price\nBILL-003,SKU-A,2,100\nBILL-003,SKU-B,1,50\n"
    csv2 = "bill_number, sku , quantity , unit_price\nBILL-003, SKU-B , 1 , 50 \nBILL-003, SKU-A , 2 , 100 \n"

    res1 = register_bill_import_preview(isolated_db, csv1, "orig.csv")
    confirm_bill_import(isolated_db, res1.import_id)

    res2 = register_bill_import_preview(isolated_db, csv2, "reformatted.csv")
    assert res2.duplicate_status.is_blocked is True
    assert res2.duplicate_status.classification == DuplicateClassification.ALREADY_CONFIRMED


def test_scenario_06_two_legitimate_bills_identical_content(isolated_db):
    """6. Two legitimate repeat walk-in bills (no bill number) contain identical products (advisory only, not blocked)."""
    # When bill_number is omitted, canonical fingerprints match on items and totals
    csv1 = "sku,quantity,unit_price\nSKU-A,1,100\n"
    csv2 = "sku,quantity,unit_price\nSKU-A,1,100\n"

    # We provide different file hashes (e.g. by passing different comments/spaces in raw file or directly)
    # Notice: In CSV parser, required headers include bill_number if present.
    # In test_duplicate_detection.py, test_missing_source_bill_numbers_content_match_not_blocked tested this:
    # Two imports with source_bill_number=None and same canonical_fingerprint
    from app.bill_import_service import check_bill_duplicate_status
    imp1 = BillImport(
        source_filename="walkin1.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="hash_walkin1",
        canonical_fingerprint="fp_walkin_repeat",
        source_bill_number=None,
        status="CONFIRMED",
    )
    isolated_db.add(imp1)
    isolated_db.commit()

    # Query duplicate status with new file_hash and identical canonical fingerprint
    res = check_bill_duplicate_status(
        db=isolated_db,
        file_hash="hash_walkin2",
        canonical_fingerprint="fp_walkin_repeat",
        source_bill_number=None,
    )
    assert res.is_blocked is False
    assert res.classification == DuplicateClassification.CANONICAL_CONTENT_MATCH


def test_scenario_09_rejected_bill_corrected_and_retried(isolated_db):
    """9. A rejected bill is corrected and retried successfully."""
    bad_csv = "bill_number,sku,quantity,unit_price\nBILL-RETRY,UNKNOWN_SKU,1,50\n"
    res1 = register_bill_import_preview(isolated_db, bad_csv, "attempt1.csv")
    assert res1.status == "REJECTED"

    # Retry with valid SKU
    good_csv = "bill_number,sku,quantity,unit_price\nBILL-RETRY,SKU-B,1,50\n"
    res2 = register_bill_import_preview(isolated_db, good_csv, "attempt2.csv")
    assert res2.status == "VALIDATED"
    assert res2.can_confirm is True

    conf = confirm_bill_import(isolated_db, res2.import_id)
    assert conf.status == "CONFIRMED"


# ==============================================================================
# Scenarios 13 - 18: Stock & Validation Boundaries
# ==============================================================================

def test_scenario_15_insufficient_stock_at_confirmation(isolated_db):
    """15. The available stock is insufficient at confirmation time."""
    csv_data = "bill_number,sku,quantity,unit_price\nBILL-STOCK-FAIL,SKU-C,6,25\n"  # SKU-C has 5 in stock
    preview = register_bill_import_preview(isolated_db, csv_data, "bill.csv")

    with pytest.raises(ValueError, match="Insufficient stock"):
        confirm_bill_import(isolated_db, preview.import_id)

    # Invariants: Zero sales, zero movements
    assert isolated_db.query(Sale).count() == 0
    assert isolated_db.query(StockMovement).count() == 0
    p = isolated_db.query(Product).filter_by(sku="SKU-C").first()
    assert p.stock_qty == 5.0  # Stock untouched


def test_scenario_17_conflicting_duplicate_sku_lines(isolated_db):
    """17. CSV contains conflicting duplicate SKU lines with different unit prices."""
    csv_conflict = (
        "bill_number,sku,quantity,unit_price\n"
        "BILL-CONFLICT,SKU-A,1,100\n"
        "BILL-CONFLICT,SKU-A,2,120\n"  # Price conflict!
    )
    res = register_bill_import_preview(isolated_db, csv_conflict, "conflict.csv")
    assert res.status == "REJECTED"
    assert res.can_confirm is False
    assert any(e["code"] == "DUPLICATE_SKU_CONFLICT" for e in res.preview_data.errors)


# ==============================================================================
# FAULT INJECTION & ROLLBACK INVARIANTS
# ==============================================================================

def test_fault_injection_during_sale_item_creation(isolated_db):
    """Injected failure during SaleItem creation triggers complete rollback."""
    csv_data = "bill_number,sku,quantity,unit_price\nBILL-FAULT-01,SKU-A,2,100\n"
    preview = register_bill_import_preview(isolated_db, csv_data, "bill.csv")

    initial_stock = isolated_db.query(Product).filter_by(sku="SKU-A").first().stock_qty

    # Injected error when adding SaleItem
    original_add = isolated_db.add
    def failing_add(instance):
        if isinstance(instance, SaleItem):
            raise SQLAlchemyError("Simulated database failure during sale item insertion")
        return original_add(instance)

    with patch.object(isolated_db, "add", side_effect=failing_add):
        with pytest.raises(SQLAlchemyError):
            confirm_bill_import(isolated_db, preview.import_id)

    # Verification: Total zero side effects
    assert isolated_db.query(Sale).count() == 0
    assert isolated_db.query(SaleItem).count() == 0
    assert isolated_db.query(StockMovement).count() == 0
    assert isolated_db.query(Product).filter_by(sku="SKU-A").first().stock_qty == initial_stock
    assert isolated_db.query(BillImport).filter_by(id=preview.import_id).first().status == "VALIDATED"


def test_fault_injection_during_stock_movement_creation(isolated_db):
    """Injected failure during StockMovement creation triggers complete rollback."""
    csv_data = "bill_number,sku,quantity,unit_price\nBILL-FAULT-02,SKU-B,1,50\n"
    preview = register_bill_import_preview(isolated_db, csv_data, "bill.csv")

    initial_stock = isolated_db.query(Product).filter_by(sku="SKU-B").first().stock_qty

    original_add = isolated_db.add
    def failing_add(instance):
        if isinstance(instance, StockMovement):
            raise SQLAlchemyError("Simulated disk error during stock movement logging")
        return original_add(instance)

    with patch.object(isolated_db, "add", side_effect=failing_add):
        with pytest.raises(SQLAlchemyError):
            confirm_bill_import(isolated_db, preview.import_id)

    # Verification: No partial sale, no stock movement, stock untouched
    assert isolated_db.query(Sale).count() == 0
    assert isolated_db.query(SaleItem).count() == 0
    assert isolated_db.query(StockMovement).count() == 0
    assert isolated_db.query(Product).filter_by(sku="SKU-B").first().stock_qty == initial_stock
    assert isolated_db.query(BillImport).filter_by(id=preview.import_id).first().status == "VALIDATED"


# ==============================================================================
# RECONCILIATION INVARIANTS
# ==============================================================================

def test_reconciliation_invariants_post_confirmation(isolated_db):
    """Verify all mathematical and accounting reconciliation invariants."""
    csv_data = (
        "bill_number,sku,quantity,unit_price\n"
        "BILL-REC-01,SKU-A,3,100\n"
        "BILL-REC-01,SKU-B,2,50\n"
    )
    preview = register_bill_import_preview(isolated_db, csv_data, "bill.csv")
    conf = confirm_bill_import(isolated_db, preview.import_id)

    sale = isolated_db.query(Sale).filter_by(id=conf.sale_id).first()
    items = isolated_db.query(SaleItem).filter_by(sale_id=sale.id).all()
    movements = isolated_db.query(StockMovement).filter_by(sale_id=sale.id).all()

    # 1. Sale total equals sum of its item totals
    assert sale.total == sum(it.line_total for it in items) == 400.0

    # 2. Stock change equals quantity sold
    sku_a_mov = next(m for m in movements if m.product.sku == "SKU-A")
    assert sku_a_mov.quantity_change == -3.0
    assert sku_a_mov.balance_after == 47.0

    sku_b_mov = next(m for m in movements if m.product.sku == "SKU-B")
    assert sku_b_mov.quantity_change == -2.0
    assert sku_b_mov.balance_after == 18.0

    # 3. Stock balance in products table reconciles with movements balance_after
    p_a = isolated_db.query(Product).filter_by(sku="SKU-A").first()
    assert p_a.stock_qty == sku_a_mov.balance_after == 47.0

    # 4. Each confirmed import references exactly one sale
    imp = isolated_db.query(BillImport).filter_by(id=preview.import_id).first()
    assert imp.sale_id == sale.id
    assert imp.status == "CONFIRMED"


def test_concurrent_competing_stock_deduction_fails_cleanly(isolated_db):
    """When two bills compete for limited remaining stock, only the first succeeds and the second fails safely."""
    # SKU-C has stock 5.0
    csv1 = "bill_number,sku,quantity,unit_price\nBILL-COMPETE-1,SKU-C,4,25\n"
    csv2 = "bill_number,sku,quantity,unit_price\nBILL-COMPETE-2,SKU-C,3,25\n"

    p1 = register_bill_import_preview(isolated_db, csv1, "bill1.csv")
    p2 = register_bill_import_preview(isolated_db, csv2, "bill2.csv")

    # Confirm Bill 1: succeeds and consumes 4 units (5 - 4 = 1 left)
    c1 = confirm_bill_import(isolated_db, p1.import_id)
    assert c1.status == "CONFIRMED"
    prod_c = isolated_db.query(Product).filter_by(sku="SKU-C").first()
    assert prod_c.stock_qty == 1.0

    # Confirm Bill 2: needs 3 units, but only 1 is available -> must fail safely
    with pytest.raises(ValueError, match="Insufficient stock"):
        confirm_bill_import(isolated_db, p2.import_id)

    # Verify Bill 2 is NOT confirmed and stock was NOT oversold
    isolated_db.rollback()
    prod_c = isolated_db.query(Product).filter_by(sku="SKU-C").first()
    assert prod_c.stock_qty == 1.0  # Still 1.0, not -2.0
    imp2 = isolated_db.query(BillImport).filter_by(id=p2.import_id).first()
    assert imp2.status == "VALIDATED"
    assert imp2.sale_id is None


def test_client_timeout_and_repeated_confirmation_idempotency(isolated_db):
    """Simulates a client timeout recovery: repeated confirmation returns existing sale without duplicate deductions."""
    csv = "bill_number,sku,quantity,unit_price\nBILL-TIMEOUT-01,SKU-A,2,100\n"
    preview = register_bill_import_preview(isolated_db, csv, "bill.csv")

    # First attempt: succeeds on server
    conf1 = confirm_bill_import(isolated_db, preview.import_id)
    assert conf1.already_confirmed is False
    initial_stock = isolated_db.query(Product).filter_by(sku="SKU-A").first().stock_qty

    # Second attempt: client timed out and retried
    conf2 = confirm_bill_import(isolated_db, preview.import_id)
    assert conf2.already_confirmed is True
    assert conf2.sale_id == conf1.sale_id
    assert conf2.status == "CONFIRMED"

    # Stock is not deducted again
    post_stock = isolated_db.query(Product).filter_by(sku="SKU-A").first().stock_qty
    assert post_stock == initial_stock
    # Only one sale exists
    assert isolated_db.query(Sale).filter_by(bill_number="BILL-TIMEOUT-01").count() == 1


def test_database_lock_detection_in_confirmation(isolated_db, monkeypatch):
    """When a database lock error occurs during BEGIN IMMEDIATE, raises explicit RuntimeError."""
    csv = "bill_number,sku,quantity,unit_price\nBILL-LOCK-01,SKU-A,1,100\n"
    preview = register_bill_import_preview(isolated_db, csv, "bill.csv")

    original_execute = isolated_db.execute

    def locked_execute(stmt, *args, **kwargs):
        if "BEGIN IMMEDIATE" in str(stmt):
            raise sqlite3.OperationalError("database is locked")
        return original_execute(stmt, *args, **kwargs)

    with patch.object(isolated_db, "execute", side_effect=locked_execute):
        with pytest.raises(RuntimeError, match="Database is currently locked"):
            confirm_bill_import(isolated_db, preview.import_id)

