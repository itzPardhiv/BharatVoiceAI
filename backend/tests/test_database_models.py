import pytest
import sqlite3
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models import Product, Sale, SaleItem, BillImport, StockMovement

# Fixture for an isolated in-memory SQLite database
@pytest.fixture
def db_session():
    # Use SQLite in-memory database with shared cache or direct connection
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Enable SQLite foreign key constraint enforcement
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    # Create all tables defined in Base.metadata
    Base.metadata.create_all(bind=engine)

    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()

    try:
        yield session, engine
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()

def test_table_creation(db_session):
    """Test that all tables and columns are created as expected."""
    session, engine = db_session
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    assert "products" in table_names
    assert "sales" in table_names
    assert "sale_items" in table_names
    assert "bill_imports" in table_names
    assert "stock_movements" in table_names

    # Check bill_imports columns
    import_cols = {col["name"] for col in inspector.get_columns("bill_imports")}
    expected_import_cols = {
        "id", "source_filename", "source_system", "store_id", "file_hash",
        "canonical_fingerprint", "source_bill_number", "status",
        "total_items", "total_amount", "sale_id", "raw_data_json",
        "error_message", "created_at", "confirmed_at"
    }
    assert expected_import_cols.issubset(import_cols)

    # Check stock_movements columns
    movement_cols = {col["name"] for col in inspector.get_columns("stock_movements")}
    expected_movement_cols = {
        "id", "product_id", "quantity_change", "balance_after",
        "reason", "bill_import_id", "sale_id", "notes", "created_at"
    }
    assert expected_movement_cols.issubset(movement_cols)

def test_sqlite_foreign_key_enforcement(db_session):
    """Test that SQLite foreign key constraints prevent orphan records."""
    session, engine = db_session

    # Attempt inserting a SaleItem with non-existent sale_id and product_id
    orphan_item = SaleItem(
        sale_id=999,
        product_id=999,
        product_name="Ghost Item",
        quantity=1.0,
        unit_price=10.0,
        line_total=10.0,
    )
    session.add(orphan_item)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Attempt inserting a StockMovement with non-existent product_id
    orphan_movement = StockMovement(
        product_id=999,
        quantity_change=-2.0,
        balance_after=5.0,
        reason="BILL_IMPORT",
    )
    session.add(orphan_movement)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

def test_bill_import_status_check_constraint(db_session):
    """Test that BillImport status check constraint only allows valid statuses."""
    session, engine = db_session

    # Invalid status should fail
    invalid_import = BillImport(
        source_filename="test.csv",
        file_hash="hash123",
        status="INVALID_STATUS",
        total_items=1,
        total_amount=100.0,
    )
    session.add(invalid_import)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Valid status PENDING should succeed
    valid_import = BillImport(
        source_filename="test.csv",
        file_hash="hash123",
        status="PENDING",
        total_items=1,
        total_amount=100.0,
    )
    session.add(valid_import)
    session.commit()
    assert valid_import.id is not None

def test_bill_import_nonnegative_constraints(db_session):
    """Test that total_items and total_amount cannot be negative."""
    session, engine = db_session

    # Negative total_items
    bad_items = BillImport(
        source_filename="test.csv",
        file_hash="hash_a",
        status="PENDING",
        total_items=-1,
        total_amount=50.0,
    )
    session.add(bad_items)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Negative total_amount
    bad_amount = BillImport(
        source_filename="test.csv",
        file_hash="hash_b",
        status="PENDING",
        total_items=2,
        total_amount=-10.0,
    )
    session.add(bad_amount)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

def test_bill_import_unique_sale_link(db_session):
    """Test that a Sale can be linked to at most one BillImport."""
    session, engine = db_session

    sale = Sale(
        bill_number="BILL-100",
        customer_name="Aman",
        payment_method="UPI",
        total=150.0,
    )
    session.add(sale)
    session.commit()

    import1 = BillImport(
        source_filename="file1.csv",
        file_hash="hash1",
        status="CONFIRMED",
        sale_id=sale.id,
    )
    session.add(import1)
    session.commit()

    # Second import attempting to link to the same sale_id must fail
    import2 = BillImport(
        source_filename="file2.csv",
        file_hash="hash2",
        status="CONFIRMED",
        sale_id=sale.id,
    )
    session.add(import2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

def test_bill_import_source_bill_uniqueness_and_nulls(db_session):
    """Test that (store_id, source_system, source_bill_number) is unique, but null bill numbers are allowed."""
    session, engine = db_session

    imp1 = BillImport(
        source_filename="file1.csv",
        store_id="STORE_1",
        source_system="CSV_UPLOAD",
        source_bill_number="INV-001",
        file_hash="hash1",
    )
    session.add(imp1)
    session.commit()

    # Same store, same system, same bill number -> must fail
    imp2 = BillImport(
        source_filename="file2.csv",
        store_id="STORE_1",
        source_system="CSV_UPLOAD",
        source_bill_number="INV-001",
        file_hash="hash2",
    )
    session.add(imp2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Same bill number, but DIFFERENT store_id -> should succeed!
    imp_diff_store = BillImport(
        source_filename="file3.csv",
        store_id="STORE_2",
        source_system="CSV_UPLOAD",
        source_bill_number="INV-001",
        file_hash="hash3",
    )
    session.add(imp_diff_store)
    session.commit()
    assert imp_diff_store.id is not None

    # Missing/null bill numbers: multiple files with no bill number must be allowed
    imp_null1 = BillImport(
        source_filename="nobill1.csv",
        store_id="STORE_1",
        source_system="CSV_UPLOAD",
        source_bill_number=None,
        file_hash="hash_null1",
    )
    imp_null2 = BillImport(
        source_filename="nobill2.csv",
        store_id="STORE_1",
        source_system="CSV_UPLOAD",
        source_bill_number=None,
        file_hash="hash_null2",
    )
    session.add_all([imp_null1, imp_null2])
    session.commit()
    assert imp_null1.id is not None
    assert imp_null2.id is not None

def test_stock_movement_nonzero_and_relationships(db_session):
    """Test StockMovement constraints and relationship navigation."""
    session, engine = db_session

    prod = Product(
        sku="SKU-1",
        name="Test Item",
        category="General",
        price=50.0,
        stock_qty=20.0,
    )
    sale = Sale(
        bill_number="BILL-200",
        total=50.0,
    )
    session.add_all([prod, sale])
    session.commit()

    imp = BillImport(
        source_filename="bill.csv",
        file_hash="hash_abc",
        status="CONFIRMED",
        sale_id=sale.id,
    )
    session.add(imp)
    session.commit()

    # Zero quantity change must fail
    zero_mov = StockMovement(
        product_id=prod.id,
        quantity_change=0.0,
        balance_after=20.0,
        reason="BILL_IMPORT",
        bill_import_id=imp.id,
        sale_id=sale.id,
    )
    session.add(zero_mov)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Valid stock deduction
    valid_mov = StockMovement(
        product_id=prod.id,
        quantity_change=-2.0,
        balance_after=18.0,
        reason="BILL_IMPORT",
        bill_import_id=imp.id,
        sale_id=sale.id,
        notes="Imported from bill.csv",
    )
    session.add(valid_mov)
    session.commit()

    # Check relationships
    assert valid_mov.product.sku == "SKU-1"
    assert valid_mov.bill_import.file_hash == "hash_abc"
    assert valid_mov.sale.bill_number == "BILL-200"
    assert len(imp.stock_movements) == 1
    assert imp.stock_movements[0].quantity_change == -2.0
    assert sale.bill_import.id == imp.id

def test_application_engine_foreign_keys_pragma():
    """Verify that the application's actual engine has foreign_keys PRAGMA enabled on connect."""
    from app.database import engine as app_engine
    with app_engine.connect() as conn:
        fk_status = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert fk_status == 1

def test_stock_movement_audit_constraints(db_session):
    """Test balance_after >= 0 and bill_import_id requirement when reason is BILL_IMPORT."""
    session, engine = db_session

    prod = Product(
        sku="SKU-AUDIT",
        name="Audit Item",
        price=10.0,
        stock_qty=10.0,
    )
    session.add(prod)
    session.commit()

    # 1. Negative balance_after must fail
    negative_balance_mov = StockMovement(
        product_id=prod.id,
        quantity_change=-15.0,
        balance_after=-5.0,
        reason="MANUAL_ADJUSTMENT",
    )
    session.add(negative_balance_mov)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 2. reason='BILL_IMPORT' without bill_import_id must fail
    missing_import_id_mov = StockMovement(
        product_id=prod.id,
        quantity_change=-2.0,
        balance_after=8.0,
        reason="BILL_IMPORT",
        bill_import_id=None,
    )
    session.add(missing_import_id_mov)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 3. reason='MANUAL_ADJUSTMENT' without bill_import_id must succeed
    manual_mov = StockMovement(
        product_id=prod.id,
        quantity_change=-1.0,
        balance_after=9.0,
        reason="MANUAL_ADJUSTMENT",
        bill_import_id=None,
    )
    session.add(manual_mov)
    session.commit()
    assert manual_mov.id is not None

def test_file_hash_and_fingerprint_are_not_db_unique_constraints(db_session):
    """Confirm file_hash and canonical_fingerprint permit re-uploads at DB level.
    Duplicate prevention for repeated file uploads is an application/service layer check."""
    session, engine = db_session

    imp1 = BillImport(
        source_filename="attempt1.csv",
        file_hash="same_file_sha256",
        canonical_fingerprint="same_canonical_fp",
        status="REJECTED",
    )
    imp2 = BillImport(
        source_filename="attempt2.csv",
        file_hash="same_file_sha256",
        canonical_fingerprint="same_canonical_fp",
        status="PENDING",
    )
    session.add_all([imp1, imp2])
    session.commit()
    assert imp1.id is not None
    assert imp2.id is not None

