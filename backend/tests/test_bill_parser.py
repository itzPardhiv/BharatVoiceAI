import pytest
from decimal import Decimal
import sqlite3
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Product, Sale, SaleItem, BillImport, StockMovement
from app.bill_import_service import parse_csv_content, validate_skus_and_stock

@pytest.fixture
def test_db():
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

    # Seed test catalogue items matching real starter data
    p1 = Product(sku="BV-1001", name="Tata Salt 1 kg", unit="pack", price=28.0, stock_qty=42.0)
    p2 = Product(sku="BV-1002", name="Aashirvaad Atta 5 kg", unit="bag", price=285.0, stock_qty=8.0)
    p3 = Product(sku="BV-1003", name="Amul Gold Milk 500 ml", unit="packet", price=34.0, stock_qty=24.0)
    p4 = Product(sku="BV-1006", name="Toor Dal 1 kg", unit="pack", price=165.0, stock_qty=3.0) # low stock: 3

    session.add_all([p1, p2, p3, p4])
    session.commit()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()

def test_valid_bill_totaling_158(test_db):
    """Test parsing and SKU matching for the documented BV-9001 example totaling ₹158."""
    csv_text = (
        "bill_number,sku,quantity,unit_price,customer_name,payment_method,line_total,bill_total\n"
        "BV-9001,BV-1001,2,28,Rahul Sharma,UPI,56.00,158.00\n"
        "BV-9001,BV-1003,3,34,Rahul Sharma,UPI,102.00,158.00\n"
    )

    parse_result = parse_csv_content(csv_text)
    assert parse_result.is_valid is True
    assert parse_result.bill_number == "BV-9001"
    assert parse_result.customer_name == "Rahul Sharma"
    assert parse_result.payment_method == "UPI"
    assert len(parse_result.items) == 2
    assert parse_result.calculated_total == Decimal("158.00")
    assert parse_result.declared_total == Decimal("158.00")
    assert len(parse_result.errors) == 0

    # Validate against DB
    matched_result = validate_skus_and_stock(parse_result, test_db)
    assert matched_result.is_valid is True
    assert len(matched_result.errors) == 0
    assert matched_result.items[0].product_name == "Tata Salt 1 kg"
    assert matched_result.items[1].product_name == "Amul Gold Milk 500 ml"

def test_utf8_bom_and_whitespace(test_db):
    """Test that UTF-8 with BOM and leading/trailing whitespace around fields are handled cleanly."""
    csv_bytes = (
        b"\xef\xbb\xbf bill_number , sku , quantity , unit_price \n"
        b" BV-9001 , BV-1001 ,  2  ,  28 \n"
    )

    parse_result = parse_csv_content(csv_bytes)
    assert parse_result.is_valid is True
    assert parse_result.bill_number == "BV-9001"
    assert parse_result.items[0].sku == "BV-1001"
    assert parse_result.items[0].quantity == Decimal("2")
    assert parse_result.items[0].unit_price == Decimal("28")
    assert parse_result.calculated_total == Decimal("56.00")

def test_missing_or_duplicate_headers():
    """Test detection of missing required headers and duplicate header names."""
    # Missing unit_price
    missing_csv = "bill_number,sku,quantity\nBV-9001,BV-1001,2\n"
    res1 = parse_csv_content(missing_csv)
    assert res1.is_valid is False
    assert any(e.code == "MISSING_HEADER" and e.field == "unit_price" for e in res1.errors)

    # Duplicate header 'sku'
    duplicate_header_csv = "bill_number,sku,quantity,unit_price,sku\nBV-9001,BV-1001,2,28,BV-1001\n"
    res2 = parse_csv_content(duplicate_header_csv)
    assert res2.is_valid is False
    assert any(e.code == "DUPLICATE_HEADER" for e in res2.errors)

    # Unsupported header
    unsupported_csv = "bill_number,sku,quantity,unit_price,some_unknown_field\nBV-9001,BV-1001,2,28,xyz\n"
    res3 = parse_csv_content(unsupported_csv)
    assert res3.is_valid is False
    assert any(e.code == "UNSUPPORTED_HEADER" for e in res3.errors)

def test_empty_file_and_malformed_rows():
    """Test handling of completely empty files and malformed rows."""
    # Completely empty
    res1 = parse_csv_content("")
    assert res1.is_valid is False
    assert any(e.code == "EMPTY_FILE" for e in res1.errors)

    # Only headers, no data
    res2 = parse_csv_content("bill_number,sku,quantity,unit_price\n")
    assert res2.is_valid is False
    assert any(e.code == "EMPTY_FILE" for e in res2.errors)

    # Malformed row with missing columns
    malformed_csv = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2\n"  # only 3 columns instead of 4
    )
    res3 = parse_csv_content(malformed_csv)
    assert res3.is_valid is False
    assert any(e.code == "MALFORMED_ROW" for e in res3.errors)

def test_invalid_zero_and_negative_quantities():
    """Test rejection of non-numeric, zero, and negative quantities."""
    csv_text = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,0,28\n"       # zero
        "BV-9001,BV-1002,-5,285\n"     # negative
        "BV-9001,BV-1003,abc,34\n"     # non-numeric
    )
    res = parse_csv_content(csv_text)
    assert res.is_valid is False
    assert len(res.errors) >= 3
    assert all(e.code == "INVALID_QUANTITY" for e in res.errors)

def test_invalid_prices_and_inconsistent_totals():
    """Test rejection of negative prices and mismatching line/bill totals."""
    # Negative price
    csv_neg_price = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,-28\n"
    )
    res1 = parse_csv_content(csv_neg_price)
    assert res1.is_valid is False
    assert any(e.code == "INVALID_PRICE" for e in res1.errors)

    # Line total mismatch: 2 * 28 = 56, but declared 50
    csv_line_mismatch = (
        "bill_number,sku,quantity,unit_price,line_total\n"
        "BV-9001,BV-1001,2,28,50.00\n"
    )
    res2 = parse_csv_content(csv_line_mismatch)
    assert res2.is_valid is False
    assert any(e.code == "LINE_TOTAL_MISMATCH" for e in res2.errors)

    # Bill total mismatch: sum is 56, declared is 100
    csv_bill_mismatch = (
        "bill_number,sku,quantity,unit_price,bill_total\n"
        "BV-9001,BV-1001,2,28,100.00\n"
    )
    res3 = parse_csv_content(csv_bill_mismatch)
    assert res3.is_valid is False
    assert any(e.code == "BILL_TOTAL_MISMATCH" for e in res3.errors)

    # Inconsistent bill total across rows
    csv_inconsistent_bill = (
        "bill_number,sku,quantity,unit_price,bill_total\n"
        "BV-9001,BV-1001,2,28,100.00\n"
        "BV-9001,BV-1003,1,34,120.00\n"
    )
    res4 = parse_csv_content(csv_inconsistent_bill)
    assert res4.is_valid is False
    assert any(e.code == "INCONSISTENT_BILL_TOTAL" for e in res4.errors)

def test_mixed_bill_numbers():
    """Test rejection of files containing more than one bill number."""
    csv_mixed = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9002,BV-1003,1,34\n"
    )
    res = parse_csv_content(csv_mixed)
    assert res.is_valid is False
    assert any(e.code == "MIXED_BILL_NUMBERS" for e in res.errors)

def test_unknown_sku_reporting(test_db):
    """Test that an unknown SKU is detected and reported without modifying the DB."""
    csv_text = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-9999,1,50\n"  # BV-9999 does not exist
    )
    parse_result = parse_csv_content(csv_text)
    assert parse_result.is_valid is True

    matched = validate_skus_and_stock(parse_result, test_db)
    assert matched.is_valid is False
    assert any(e.code == "UNKNOWN_SKU" and "BV-9999" in e.message for e in matched.errors)

    # Ensure product count in DB did NOT change
    assert test_db.query(Product).count() == 4

def test_duplicate_sku_rows_aggregation_and_conflict(test_db):
    """Test duplicate SKU handling: compatible rows aggregate, conflicting prices fail."""
    # Compatible duplicate SKU: same SKU BV-1001, same unit price ₹28 on both rows
    csv_compatible = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-1001,3,28\n"
    )
    res_comp = parse_csv_content(csv_compatible)
    matched_comp = validate_skus_and_stock(res_comp, test_db)
    assert matched_comp.is_valid is True
    assert len(matched_comp.items) == 1
    assert matched_comp.items[0].quantity == Decimal("5")  # 2 + 3 aggregated
    assert matched_comp.items[0].line_total == Decimal("140.00")
    assert any(w.code == "AGGREGATED_SKU_ROWS" for w in matched_comp.warnings)

    # Conflicting duplicate SKU: same SKU BV-1001, but different unit prices (28 vs 30)
    csv_conflict = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-1001,3,30\n"
    )
    res_conf = parse_csv_content(csv_conflict)
    matched_conf = validate_skus_and_stock(res_conf, test_db)
    assert matched_conf.is_valid is False
    assert any(e.code == "DUPLICATE_SKU_CONFLICT" for e in matched_conf.errors)

def test_insufficient_stock_warning(test_db):
    """Test that requesting quantity exceeding current stock produces a preview warning."""
    # BV-1006 stock_qty is 3.0 in test_db. Request 10.
    csv_text = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1006,10,165\n"
    )
    res = parse_csv_content(csv_text)
    matched = validate_skus_and_stock(res, test_db)

    # Parsing is structurally valid, but raises a stock warning
    assert matched.is_valid is True
    assert any(w.code == "INSUFFICIENT_STOCK" for w in matched.warnings)
    # Ensure product stock was NOT deducted
    p = test_db.query(Product).filter(Product.sku == "BV-1006").first()
    assert p.stock_qty == 3.0

def test_invariance_no_database_changes_during_parsing(test_db):
    """Verify that parsing and validation do not write Sales, SaleItems, or alter Product stock."""
    csv_text = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-1003,3,34\n"
    )
    sales_before = test_db.query(Sale).count()
    items_before = test_db.query(SaleItem).count()
    imports_before = test_db.query(BillImport).count()
    movements_before = test_db.query(StockMovement).count()

    p_salt = test_db.query(Product).filter(Product.sku == "BV-1001").first()
    stock_before = p_salt.stock_qty

    res = parse_csv_content(csv_text)
    matched = validate_skus_and_stock(res, test_db)

    assert matched.is_valid is True
    assert test_db.query(Sale).count() == sales_before
    assert test_db.query(SaleItem).count() == items_before
    assert test_db.query(BillImport).count() == imports_before
    assert test_db.query(StockMovement).count() == movements_before
    assert p_salt.stock_qty == stock_before
