import pytest
import sqlite3
from decimal import Decimal
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock
from sqlalchemy.exc import SQLAlchemyError

from app.database import Base
from app.models import Product, Sale, SaleItem, BillImport, StockMovement
from app.bill_import_service import (
    check_bill_duplicate_status,
    DuplicateClassification,
    DuplicateCheckResult,
)


@pytest.fixture
def db_session():
    """Provides an isolated, in-memory SQLite database session with FKs enabled."""
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

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ==============================================================================
# 1. No matches returns NEW_IMPORT
# ==============================================================================
def test_no_matches_returns_new_import(db_session):
    """When no matching records exist in the database, NEW_IMPORT is returned."""
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="1111111111111111111111111111111111111111111111111111111111111111",
        canonical_fingerprint="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        source_bill_number="BV-NEW-001",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
    )

    assert result.classification == DuplicateClassification.NEW_IMPORT
    assert result.classification == "NEW_IMPORT"
    assert result.is_blocked is False
    assert result.existing_import_id is None
    assert result.existing_status is None
    assert result.action == "PROCEED"
    assert result.match_type is None
    assert result.matched_by == []


# ==============================================================================
# 2. Exact file hash matching a confirmed import returns ALREADY_CONFIRMED
# ==============================================================================
def test_exact_file_hash_matching_confirmed_returns_already_confirmed(db_session):
    """Exact file hash match to a confirmed record blocks re-import even with different/absent bill number."""
    confirmed_import = BillImport(
        source_filename="confirmed_batch.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="2222222222222222222222222222222222222222222222222222222222222222",
        canonical_fingerprint="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        source_bill_number="BV-CONFIRMED-01",
        status="CONFIRMED",
        total_items=2,
        total_amount=150.0,
    )
    db_session.add(confirmed_import)
    db_session.commit()

    # Case A: Same file hash with different bill number
    res_diff_bill = check_bill_duplicate_status(
        db=db_session,
        file_hash="2222222222222222222222222222222222222222222222222222222222222222",
        source_bill_number="BV-NEW-NUMBER",
    )
    assert res_diff_bill.classification == DuplicateClassification.ALREADY_CONFIRMED
    assert res_diff_bill.is_blocked is True
    assert res_diff_bill.existing_import_id == confirmed_import.id
    assert res_diff_bill.existing_status == "CONFIRMED"
    assert res_diff_bill.action == "BLOCK"
    assert "file_hash" in res_diff_bill.matched_by

    # Case B: Same file hash with missing (None) bill number
    res_none_bill = check_bill_duplicate_status(
        db=db_session,
        file_hash="2222222222222222222222222222222222222222222222222222222222222222",
        source_bill_number=None,
    )
    assert res_none_bill.classification == DuplicateClassification.ALREADY_CONFIRMED
    assert res_none_bill.is_blocked is True
    assert res_none_bill.existing_import_id == confirmed_import.id


# ==============================================================================
# 3. Same source bill number with different file hashes is detected
# ==============================================================================
def test_same_source_bill_number_with_different_file_hash_detected(db_session):
    """Detects duplicate source bill number even when uploaded file bytes/hash differ."""
    confirmed_import = BillImport(
        source_filename="original_bill.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="3333333333333333333333333333333333333333333333333333333333333333",
        source_bill_number="BV-9001",
        status="CONFIRMED",
        total_items=1,
        total_amount=50.0,
    )
    db_session.add(confirmed_import)
    db_session.commit()

    # Re-upload same bill number with a different file hash
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="4444444444444444444444444444444444444444444444444444444444444444",
        source_bill_number="BV-9001",
    )

    assert result.classification == DuplicateClassification.ALREADY_CONFIRMED
    assert result.is_blocked is True
    assert result.existing_import_id == confirmed_import.id
    assert result.match_type == "SOURCE_BILL_DUPLICATE"
    assert "source_bill_number" in result.matched_by
    assert "file_hash" not in result.matched_by


# ==============================================================================
# 4. A pending or validated preview is identified
# ==============================================================================
def test_pending_preview_is_identified(db_session):
    """When a pending preview exists for a file/bill, return PENDING_IMPORT_EXISTS to resume draft."""
    pending_import = BillImport(
        source_filename="pending_bill.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="5555555555555555555555555555555555555555555555555555555555555555",
        source_bill_number="BV-PEND-100",
        status="PENDING",
        total_items=1,
        total_amount=75.0,
    )
    db_session.add(pending_import)
    db_session.commit()

    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="5555555555555555555555555555555555555555555555555555555555555555",
        source_bill_number="BV-PEND-100",
    )

    assert result.classification == DuplicateClassification.PENDING_IMPORT_EXISTS
    assert result.is_blocked is True
    assert result.existing_import_id == pending_import.id
    assert result.existing_status == "PENDING"
    assert result.action == "RESUME_PREVIEW"


def test_validated_preview_is_identified(db_session):
    """When a validated preview exists for a file/bill, return PENDING_IMPORT_EXISTS."""
    validated_import = BillImport(
        source_filename="validated_bill.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="6666666666666666666666666666666666666666666666666666666666666666",
        source_bill_number="BV-VAL-200",
        status="VALIDATED",
        total_items=3,
        total_amount=210.0,
    )
    db_session.add(validated_import)
    db_session.commit()

    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="6666666666666666666666666666666666666666666666666666666666666666",
        source_bill_number="BV-VAL-200",
    )

    assert result.classification == DuplicateClassification.PENDING_IMPORT_EXISTS
    assert result.is_blocked is True
    assert result.existing_import_id == validated_import.id
    assert result.existing_status == "VALIDATED"
    assert result.action == "RESUME_PREVIEW"


# ==============================================================================
# 5. Rejected attempts are reported distinctly
# ==============================================================================
def test_rejected_attempts_reported_distinctly(db_session):
    """Prior rejected imports are identified distinctly and do not hard-block duplicate lookup."""
    rejected_import = BillImport(
        source_filename="rejected_bill.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="7777777777777777777777777777777777777777777777777777777777777777",
        source_bill_number="BV-REJ-300",
        status="REJECTED",
        error_message="Price mismatch on row 2",
    )
    db_session.add(rejected_import)
    db_session.commit()

    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="7777777777777777777777777777777777777777777777777777777777777777",
        source_bill_number="BV-REJ-300",
    )

    assert result.classification == DuplicateClassification.PREVIOUS_IMPORT_REJECTED
    assert result.is_blocked is False
    assert result.existing_import_id == rejected_import.id
    assert result.existing_status == "REJECTED"
    assert result.action == "ALLOW_RETRY"


# ==============================================================================
# 6. Failed attempts are reported distinctly
# ==============================================================================
def test_failed_attempts_reported_distinctly(db_session):
    """Prior failed imports are identified distinctly and do not hard-block duplicate lookup."""
    failed_import = BillImport(
        source_filename="failed_bill.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="8888888888888888888888888888888888888888888888888888888888888888",
        source_bill_number="BV-FAIL-400",
        status="FAILED",
        error_message="System error during processing",
    )
    db_session.add(failed_import)
    db_session.commit()

    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="8888888888888888888888888888888888888888888888888888888888888888",
        source_bill_number="BV-FAIL-400",
    )

    assert result.classification == DuplicateClassification.PREVIOUS_IMPORT_FAILED
    assert result.is_blocked is False
    assert result.existing_import_id == failed_import.id
    assert result.existing_status == "FAILED"
    assert result.action == "ALLOW_RETRY"


# ==============================================================================
# 7. Different stores can reuse the same bill number
# ==============================================================================
def test_different_stores_can_reuse_same_bill_number(db_session):
    """Bill number reuse across different store IDs is valid and yields NEW_IMPORT."""
    store1_import = BillImport(
        source_filename="store1.csv",
        store_id="STORE_DELHI",
        source_system="CSV_UPLOAD",
        file_hash="9999999999999999999999999999999999999999999999999999999999999999",
        source_bill_number="INV-COMMON-500",
        status="CONFIRMED",
    )
    db_session.add(store1_import)
    db_session.commit()

    # Query for STORE_MUMBAI with the same bill number and different file hash
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        source_bill_number="INV-COMMON-500",
        store_id="STORE_MUMBAI",
        source_system="CSV_UPLOAD",
    )

    assert result.classification == DuplicateClassification.NEW_IMPORT
    assert result.is_blocked is False
    assert result.existing_import_id is None


def test_different_stores_file_hash_isolation(db_session):
    """Exact file hash in STORE_A does not block a distinct STORE_B."""
    store_a_import = BillImport(
        source_filename="store_a.csv",
        store_id="STORE_A",
        source_system="CSV_UPLOAD",
        file_hash="ffff1111ffff1111ffff1111ffff1111ffff1111ffff1111ffff1111ffff1111",
        status="CONFIRMED",
    )
    db_session.add(store_a_import)
    db_session.commit()

    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="ffff1111ffff1111ffff1111ffff1111ffff1111ffff1111ffff1111ffff1111",
        store_id="STORE_B",
    )
    assert result.classification == DuplicateClassification.NEW_IMPORT
    assert result.is_blocked is False


# ==============================================================================
# 8. Different source systems can reuse the same bill number
# ==============================================================================
def test_different_source_systems_can_reuse_same_bill_number(db_session):
    """Bill number reuse across different source systems within same store yields NEW_IMPORT."""
    csv_import = BillImport(
        source_filename="csv_import.csv",
        store_id="STORE_1",
        source_system="CSV_UPLOAD",
        file_hash="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        source_bill_number="INV-SYS-600",
        status="CONFIRMED",
    )
    db_session.add(csv_import)
    db_session.commit()

    # Query for EXTERNAL_API source system with same bill number and different file hash
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        source_bill_number="INV-SYS-600",
        store_id="STORE_1",
        source_system="EXTERNAL_API",
    )

    assert result.classification == DuplicateClassification.NEW_IMPORT
    assert result.is_blocked is False
    assert result.existing_import_id is None


# ==============================================================================
# 9. Canonical content match does not hard-block different bill numbers
# ==============================================================================
def test_canonical_content_match_does_not_hard_block_different_bill_numbers(db_session):
    """Identical item content with different bill numbers is advisory only (not blocked)."""
    existing_import = BillImport(
        source_filename="batch1.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        canonical_fingerprint="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        source_bill_number="BILL-SERIES-001",
        status="CONFIRMED",
    )
    db_session.add(existing_import)
    db_session.commit()

    # Incoming import has same canonical fingerprint, but different file hash and different bill number
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        canonical_fingerprint="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        source_bill_number="BILL-SERIES-002",
    )

    assert result.classification == DuplicateClassification.CANONICAL_CONTENT_MATCH
    assert result.is_blocked is False
    assert result.existing_import_id == existing_import.id
    assert result.existing_status == "CONFIRMED"
    assert result.action == "PROCEED_WITH_ADVISORY"
    assert "legitimate repeat sale" in result.message


# ==============================================================================
# 10. Missing source bill numbers do not cause unrelated transactions to be blocked
# ==============================================================================
def test_missing_source_bill_numbers_content_match_not_blocked(db_session):
    """When source bill number is missing (None or empty), content match remains advisory."""
    walkin1 = BillImport(
        source_filename="walkin_1.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="1010101010101010101010101010101010101010101010101010101010101010",
        canonical_fingerprint="2020202020202020202020202020202020202020202020202020202020202020",
        source_bill_number=None,
        status="CONFIRMED",
    )
    db_session.add(walkin1)
    db_session.commit()

    # Incoming import without bill number and with same items
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="3030303030303030303030303030303030303030303030303030303030303030",
        canonical_fingerprint="2020202020202020202020202020202020202020202020202020202020202020",
        source_bill_number=None,
    )

    assert result.classification == DuplicateClassification.CANONICAL_CONTENT_MATCH
    assert result.is_blocked is False
    assert result.action == "PROCEED_WITH_ADVISORY"
    assert "source_bill_number" not in result.matched_by


def test_empty_or_whitespace_bill_number_never_matches_null_source_bills(db_session):
    """Empty or whitespace bill numbers are safely treated as None and do not match DB records."""
    null_bill_import = BillImport(
        source_filename="walkin.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="4040404040404040404040404040404040404040404040404040404040404040",
        source_bill_number=None,
        status="CONFIRMED",
    )
    db_session.add(null_bill_import)
    db_session.commit()

    # Pass whitespace bill number and unique file hash
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="5050505050505050505050505050505050505050505050505050505050505050",
        source_bill_number="   ",
    )

    assert result.classification == DuplicateClassification.NEW_IMPORT
    assert result.is_blocked is False


# ==============================================================================
# 11. Multiple matching records handled deterministically and safely
# ==============================================================================
def test_multiple_matching_records_confirmed_takes_precedence_over_rejected(db_session):
    """If file hash matches a rejected record but source bill matches a confirmed record, confirmed wins."""
    rejected_record = BillImport(
        source_filename="old_draft.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="6060606060606060606060606060606060606060606060606060606060606060",
        source_bill_number="BV-OLD-DRAFT",
        status="REJECTED",
    )
    confirmed_record = BillImport(
        source_filename="final_bill.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="7070707070707070707070707070707070707070707070707070707070707070",
        source_bill_number="BV-FINAL-999",
        status="CONFIRMED",
    )
    db_session.add_all([rejected_record, confirmed_record])
    db_session.commit()

    # Incoming bill has file hash matching rejected_record, but bill number matching confirmed_record
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="6060606060606060606060606060606060606060606060606060606060606060",
        source_bill_number="BV-FINAL-999",
    )

    assert result.classification == DuplicateClassification.ALREADY_CONFIRMED
    assert result.is_blocked is True
    assert result.existing_import_id == confirmed_record.id
    assert result.action == "BLOCK"


def test_multiple_matching_confirmed_records_reconciliation(db_session):
    """When file hash matches Confirmed Import 1 and source bill matches Confirmed Import 2,
    both are blocked, source bill is prioritized, and conflict is noted in message."""
    import1 = BillImport(
        source_filename="file1.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="hash_match_file_only",
        source_bill_number="BV-BILL-1",
        status="CONFIRMED",
    )
    import2 = BillImport(
        source_filename="file2.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="hash_unrelated_2",
        source_bill_number="BV-BILL-2",
        status="CONFIRMED",
    )
    db_session.add_all([import1, import2])
    db_session.commit()

    # Query with file_hash from import1, but source_bill_number from import2
    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="hash_match_file_only",
        source_bill_number="BV-BILL-2",
    )

    assert result.classification == DuplicateClassification.ALREADY_CONFIRMED
    assert result.is_blocked is True
    assert result.existing_import_id == import2.id  # Source bill identity preferred
    assert "source_bill_number" in result.matched_by
    assert "file_hash" in result.matched_by
    assert "Conflicting matches" in result.message
    assert str(import1.id) in result.message
    assert str(import2.id) in result.message


def test_multiple_matching_records_pending_takes_precedence_over_failed(db_session):
    """If file hash matches a failed record but source bill matches a pending preview, pending wins."""
    failed_record = BillImport(
        source_filename="failed.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="8080808080808080808080808080808080808080808080808080808080808080",
        source_bill_number="BV-FAILED-ITEM",
        status="FAILED",
    )
    pending_record = BillImport(
        source_filename="active_preview.csv",
        store_id="DEFAULT",
        source_system="CSV_UPLOAD",
        file_hash="9090909090909090909090909090909090909090909090909090909090909090",
        source_bill_number="BV-PENDING-ACTIVE",
        status="PENDING",
    )
    db_session.add_all([failed_record, pending_record])
    db_session.commit()

    result = check_bill_duplicate_status(
        db=db_session,
        file_hash="8080808080808080808080808080808080808080808080808080808080808080",
        source_bill_number="BV-PENDING-ACTIVE",
    )

    assert result.classification == DuplicateClassification.PENDING_IMPORT_EXISTS
    assert result.is_blocked is True
    assert result.existing_import_id == pending_record.id
    assert result.action == "RESUME_PREVIEW"


# ==============================================================================
# 12. Database lookup failure raises explicit error; never returns NEW_IMPORT
# ==============================================================================
def test_database_lookup_failure_raises_explicit_error(db_session):
    """Database query failure must raise an exception and never be swallowed into NEW_IMPORT."""
    mock_db = MagicMock()
    mock_db.query.side_effect = SQLAlchemyError("Simulated database failure")

    with pytest.raises(SQLAlchemyError):
        check_bill_duplicate_status(
            db=mock_db,
            file_hash="1122334455667788112233445566778811223344556677881122334455667788",
            source_bill_number="BV-FAIL",
        )


def test_malformed_input_raises_value_error(db_session):
    """Empty or non-string file_hash raises ValueError, never returning NEW_IMPORT."""
    with pytest.raises(ValueError):
        check_bill_duplicate_status(db=db_session, file_hash="")

    with pytest.raises(ValueError):
        check_bill_duplicate_status(db=db_session, file_hash=None)

    with pytest.raises(ValueError):
        check_bill_duplicate_status(db=db_session, file_hash="   ")


# ==============================================================================
# 13. Function performs no inserts, updates, deletes, commits, or stock changes
# ==============================================================================
def test_duplicate_lookup_is_strictly_read_only(db_session):
    """Verify that calling check_bill_duplicate_status performs zero mutations or stock changes."""
    # Setup initial records
    product = Product(
        sku="TEST-SKU-99",
        name="Paracetamol 500mg",
        price=30.0,
        stock_qty=100.0,
    )
    sale = Sale(
        bill_number="INV-AUDIT-01",
        total=60.0,
    )
    db_session.add_all([product, sale])
    db_session.commit()

    sale_item = SaleItem(
        sale_id=sale.id,
        product_id=product.id,
        product_name=product.name,
        quantity=2.0,
        unit_price=30.0,
        line_total=60.0,
    )
    bill_import = BillImport(
        source_filename="audit.csv",
        file_hash="aaaabbbbccccddddaaaabbbbccccddddaaaabbbbccccddddaaaabbbbccccdddd",
        source_bill_number="INV-AUDIT-01",
        status="CONFIRMED",
        sale_id=sale.id,
    )
    db_session.add_all([sale_item, bill_import])
    db_session.commit()

    stock_movement = StockMovement(
        product_id=product.id,
        quantity_change=-2.0,
        balance_after=98.0,
        reason="BILL_IMPORT",
        bill_import_id=bill_import.id,
        sale_id=sale.id,
    )
    db_session.add(stock_movement)
    db_session.commit()

    # Capture state before lookups
    prod_count_before = db_session.query(Product).count()
    sale_count_before = db_session.query(Sale).count()
    item_count_before = db_session.query(SaleItem).count()
    import_count_before = db_session.query(BillImport).count()
    mov_count_before = db_session.query(StockMovement).count()

    initial_stock = db_session.query(Product).filter_by(sku="TEST-SKU-99").first().stock_qty

    # Run check_bill_duplicate_status multiple times across different scenarios
    check_bill_duplicate_status(db_session, file_hash="aaaabbbbccccddddaaaabbbbccccddddaaaabbbbccccddddaaaabbbbccccdddd")
    check_bill_duplicate_status(db_session, file_hash="unmatched_hash_1", source_bill_number="INV-AUDIT-01")
    check_bill_duplicate_status(db_session, file_hash="unmatched_hash_2", source_bill_number="BRAND_NEW_BILL")
    check_bill_duplicate_status(db_session, file_hash="unmatched_hash_3", canonical_fingerprint="some_fp")

    # Assert no pending session modifications
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0

    # Assert table counts are completely unchanged
    assert db_session.query(Product).count() == prod_count_before
    assert db_session.query(Sale).count() == sale_count_before
    assert db_session.query(SaleItem).count() == item_count_before
    assert db_session.query(BillImport).count() == import_count_before
    assert db_session.query(StockMovement).count() == mov_count_before

    # Assert stock quantity is completely unaltered
    final_stock = db_session.query(Product).filter_by(sku="TEST-SKU-99").first().stock_qty
    assert final_stock == initial_stock == 100.0
