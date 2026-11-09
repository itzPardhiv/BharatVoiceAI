import re
import pytest
from decimal import Decimal
from app.bill_import_service import (
    compute_file_hash,
    compute_canonical_fingerprint,
    parse_csv_content,
    BillParseResult,
    ParsedLineItem,
    ValidationError,
)

HEX_64_REGEX = re.compile(r"^[0-9a-f]{64}$")

# ==========================================
# 1. Tests for compute_file_hash
# ==========================================

def test_file_hash_identical_bytes():
    """Identical bytes must produce identical SHA-256 hashes."""
    data1 = b"bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,2,28\n"
    data2 = b"bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,2,28\n"

    h1 = compute_file_hash(data1)
    h2 = compute_file_hash(data2)

    assert h1 == h2
    assert HEX_64_REGEX.match(h1)

def test_file_hash_single_byte_change():
    """A single byte difference must yield a completely different hash."""
    data1 = b"bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,2,28\n"
    data2 = b"bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,3,28\n"

    h1 = compute_file_hash(data1)
    h2 = compute_file_hash(data2)

    assert h1 != h2

def test_file_hash_different_line_endings():
    """CRLF vs LF changes the file bytes, so the file hash must differ."""
    crlf_bytes = b"bill_number,sku,quantity,unit_price\r\nBV-9001,BV-1001,2,28\r\n"
    lf_bytes = b"bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,2,28\n"

    h_crlf = compute_file_hash(crlf_bytes)
    h_lf = compute_file_hash(lf_bytes)

    assert h_crlf != h_lf

def test_file_hash_utf8_bom_difference():
    """Raw bytes with a UTF-8 BOM must yield a different file hash than bytes without BOM."""
    no_bom = b"bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,2,28\n"
    with_bom = b"\xef\xbb\xbf" + no_bom

    h_no_bom = compute_file_hash(no_bom)
    h_with_bom = compute_file_hash(with_bom)

    assert h_no_bom != h_with_bom

def test_file_hash_string_input_compatibility():
    """String input encodes to UTF-8 bytes and returns valid 64-char hex."""
    text = "bill_number,sku,quantity,unit_price\nBV-9001,BV-1001,2,28\n"
    h_str = compute_file_hash(text)
    h_bytes = compute_file_hash(text.encode("utf-8"))

    assert h_str == h_bytes
    assert HEX_64_REGEX.match(h_str)

# ==========================================
# 2. Tests for compute_canonical_fingerprint
# ==========================================

def test_canonical_fingerprint_identical_content():
    """Identical parsed bills produce the exact same canonical fingerprint."""
    csv1 = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-1003,3,34\n"
    )
    csv2 = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-1003,3,34\n"
    )

    bill1 = parse_csv_content(csv1)
    bill2 = parse_csv_content(csv2)

    fp1 = compute_canonical_fingerprint(bill1)
    fp2 = compute_canonical_fingerprint(bill2)

    assert fp1 is not None
    assert fp1 == fp2
    assert HEX_64_REGEX.match(fp1)

def test_canonical_fingerprint_reordered_rows():
    """Reordering items in the CSV does NOT change the canonical fingerprint."""
    csv_order_a = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
        "BV-9001,BV-1003,3,34\n"
    )
    csv_order_b = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1003,3,34\n"
        "BV-9001,BV-1001,2,28\n"
    )

    bill_a = parse_csv_content(csv_order_a)
    bill_b = parse_csv_content(csv_order_b)

    fp_a = compute_canonical_fingerprint(bill_a)
    fp_b = compute_canonical_fingerprint(bill_b)

    assert fp_a == fp_b

def test_canonical_fingerprint_changes_on_quantity_or_price():
    """Modifying quantity, price, or SKU changes the canonical fingerprint."""
    csv_base = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
    )
    csv_diff_qty = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,5,28\n"
    )
    csv_diff_price = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,30\n"
    )
    csv_diff_sku = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1002,2,28\n"
    )

    fp_base = compute_canonical_fingerprint(parse_csv_content(csv_base))
    fp_qty = compute_canonical_fingerprint(parse_csv_content(csv_diff_qty))
    fp_price = compute_canonical_fingerprint(parse_csv_content(csv_diff_price))
    fp_sku = compute_canonical_fingerprint(parse_csv_content(csv_diff_sku))

    assert fp_base != fp_qty
    assert fp_base != fp_price
    assert fp_base != fp_sku

def test_canonical_fingerprint_changes_on_source_bill_number():
    """Different bill numbers with identical items produce different fingerprints."""
    csv_bill_1 = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9001,BV-1001,2,28\n"
    )
    csv_bill_2 = (
        "bill_number,sku,quantity,unit_price\n"
        "BV-9002,BV-1001,2,28\n"
    )

    fp1 = compute_canonical_fingerprint(parse_csv_content(csv_bill_1))
    fp2 = compute_canonical_fingerprint(parse_csv_content(csv_bill_2))

    assert fp1 != fp2

def test_canonical_fingerprint_ignores_volatile_metadata():
    """Changing payment_method or customer_name does not alter canonical fingerprint."""
    csv1 = (
        "bill_number,sku,quantity,unit_price,customer_name,payment_method\n"
        "BV-9001,BV-1001,2,28,Customer A,Cash\n"
    )
    csv2 = (
        "bill_number,sku,quantity,unit_price,customer_name,payment_method\n"
        "BV-9001,BV-1001,2,28,Customer B,UPI\n"
    )

    fp1 = compute_canonical_fingerprint(parse_csv_content(csv1))
    fp2 = compute_canonical_fingerprint(parse_csv_content(csv2))

    # The canonical transaction identity is governed by scoped bill items and totals
    assert fp1 == fp2

def test_canonical_fingerprint_monetary_normalization():
    """Decimals like 28, 28.0, and 28.00 format identically as 28.00."""
    item1 = ParsedLineItem(
        row_number=2,
        sku="BV-1001",
        quantity=Decimal("2"),
        unit_price=Decimal("28.0"),
        line_total=Decimal("56.00"),
    )
    item2 = ParsedLineItem(
        row_number=2,
        sku="BV-1001",
        quantity=Decimal("2.0"),
        unit_price=Decimal("28.00"),
        line_total=Decimal("56"),
    )

    b1 = BillParseResult(
        is_valid=True,
        bill_number="BV-9001",
        items=[item1],
        calculated_total=Decimal("56.0"),
    )
    b2 = BillParseResult(
        is_valid=True,
        bill_number="BV-9001",
        items=[item2],
        calculated_total=Decimal("56.00"),
    )

    fp1 = compute_canonical_fingerprint(b1)
    fp2 = compute_canonical_fingerprint(b2)

    assert fp1 == fp2

def test_canonical_fingerprint_missing_bill_number():
    """When bill number is None, fingerprint is computed with source_bill_number: null."""
    item = ParsedLineItem(
        row_number=2,
        sku="BV-1001",
        quantity=Decimal("2"),
        unit_price=Decimal("28.00"),
        line_total=Decimal("56.00"),
    )
    bill = BillParseResult(
        is_valid=True,
        bill_number=None,
        items=[item],
        calculated_total=Decimal("56.00"),
    )
    fp = compute_canonical_fingerprint(bill)
    assert fp is not None
    assert HEX_64_REGEX.match(fp)

def test_canonical_fingerprint_invalid_input_returns_none():
    """Invalid bill results or bills with errors return None."""
    # 1. is_valid = False
    invalid_bill = BillParseResult(
        is_valid=False,
        bill_number="BV-9001",
        items=[],
        calculated_total=Decimal("0.00"),
    )
    assert compute_canonical_fingerprint(invalid_bill) is None

    # 2. Has errors
    bill_with_err = BillParseResult(
        is_valid=True,
        bill_number="BV-9001",
        items=[ParsedLineItem(row_number=2, sku="BV-1001", quantity=Decimal(1), unit_price=Decimal(10), line_total=Decimal(10))],
        calculated_total=Decimal("10.00"),
        errors=[ValidationError(code="UNKNOWN_SKU", message="Not found")],
    )
    assert compute_canonical_fingerprint(bill_with_err) is None

    # 3. No items
    empty_items_bill = BillParseResult(
        is_valid=True,
        bill_number="BV-9001",
        items=[],
        calculated_total=Decimal("0.00"),
    )
    assert compute_canonical_fingerprint(empty_items_bill) is None
