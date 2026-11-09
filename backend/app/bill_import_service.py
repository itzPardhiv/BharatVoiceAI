import csv
import io
import os
import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
from typing import List, Dict, Optional, Set, Tuple, Union, Any
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .models import Product, BillImport, Sale, SaleItem, StockMovement
from .accounts_service import get_or_create_default_account, record_account_transaction

# Currency precision definition: 2 decimal places (paise)
CURRENCY_PRECISION = Decimal("0.01")

def quantize_currency(amount: Decimal) -> Decimal:
    """Round monetary amounts to 2 decimal places using standard ROUND_HALF_UP."""
    return amount.quantize(CURRENCY_PRECISION, rounding=ROUND_HALF_UP)

REQUIRED_HEADERS: Set[str] = {"bill_number", "sku", "quantity", "unit_price"}
OPTIONAL_HEADERS: Set[str] = {"customer_name", "payment_method", "line_total", "bill_total"}
ALLOWED_HEADERS: Set[str] = REQUIRED_HEADERS.union(OPTIONAL_HEADERS)


class ValidationError(BaseModel):
    row_number: Optional[int] = None
    field: Optional[str] = None
    code: str
    message: str


class ValidationWarning(BaseModel):
    row_number: Optional[int] = None
    field: Optional[str] = None
    code: str
    message: str


class ParsedLineItem(BaseModel):
    row_number: int
    sku: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    current_stock: Optional[float] = None


class BillParseResult(BaseModel):
    is_valid: bool
    bill_number: Optional[str] = None
    customer_name: Optional[str] = None
    payment_method: Optional[str] = None
    items: List[ParsedLineItem] = Field(default_factory=list)
    calculated_total: Decimal = Decimal("0.00")
    declared_total: Optional[Decimal] = None
    errors: List[ValidationError] = Field(default_factory=list)
    warnings: List[ValidationWarning] = Field(default_factory=list)


def parse_csv_content(csv_input: Union[str, bytes]) -> BillParseResult:
    """
    Parses and syntactically validates CSV sales bill content.
    Does NOT connect to the database or modify records.
    """
    errors: List[ValidationError] = []
    warnings: List[ValidationWarning] = []

    # 1. Handle encoding & UTF-8 BOM
    if isinstance(csv_input, bytes):
        try:
            # utf-8-sig transparently strips UTF-8 BOM (\xef\xbb\xbf) if present
            text_content = csv_input.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            errors.append(ValidationError(
                code="INVALID_ENCODING",
                message=f"File encoding is invalid: {exc.reason}. Please upload valid UTF-8 text.",
            ))
            return BillParseResult(is_valid=False, errors=errors)
    else:
        # String input might also begin with BOM character \ufeff
        text_content = csv_input.lstrip("\ufeff")

    stripped = text_content.strip()
    if not stripped:
        errors.append(ValidationError(
            code="EMPTY_FILE",
            message="The uploaded CSV file is empty.",
        ))
        return BillParseResult(is_valid=False, errors=errors)

    # 2. Parse CSV rows
    csv_file = io.StringIO(text_content)
    reader = csv.reader(csv_file)

    try:
        raw_headers = next(reader, None)
    except csv.Error as e:
        errors.append(ValidationError(
            code="MALFORMED_CSV",
            message=f"Unable to read CSV headers: {str(e)}",
        ))
        return BillParseResult(is_valid=False, errors=errors)

    if not raw_headers:
        errors.append(ValidationError(
            code="EMPTY_FILE",
            message="CSV file does not contain a header line.",
        ))
        return BillParseResult(is_valid=False, errors=errors)

    # 3. Validate headers
    normalized_headers: List[str] = [h.strip().lower() for h in raw_headers]

    # Check for duplicate headers
    seen_headers: Set[str] = set()
    for col in normalized_headers:
        if not col:
            errors.append(ValidationError(
                code="EMPTY_HEADER",
                message="CSV contains an empty column header.",
            ))
            return BillParseResult(is_valid=False, errors=errors)
        if col in seen_headers:
            errors.append(ValidationError(
                code="DUPLICATE_HEADER",
                field=col,
                message=f"Duplicate column header '{col}' detected in CSV.",
            ))
            return BillParseResult(is_valid=False, errors=errors)
        seen_headers.add(col)

    # Check for required headers
    missing_headers = REQUIRED_HEADERS - seen_headers
    if missing_headers:
        for missing in sorted(missing_headers):
            errors.append(ValidationError(
                code="MISSING_HEADER",
                field=missing,
                message=f"Required column header '{missing}' is missing.",
            ))
        return BillParseResult(is_valid=False, errors=errors)

    # Check for unsupported headers
    unsupported_headers = seen_headers - ALLOWED_HEADERS
    if unsupported_headers:
        for unsupported in sorted(unsupported_headers):
            errors.append(ValidationError(
                code="UNSUPPORTED_HEADER",
                field=unsupported,
                message=f"Column header '{unsupported}' is not recognized.",
            ))
        return BillParseResult(is_valid=False, errors=errors)

    header_indices: Dict[str, int] = {h: idx for idx, h in enumerate(normalized_headers)}

    bill_number: Optional[str] = None
    customer_name: Optional[str] = None
    payment_method: Optional[str] = None
    declared_bill_total: Optional[Decimal] = None
    items: List[ParsedLineItem] = []
    calculated_total = Decimal("0.00")

    row_count = 0

    # 4. Iterate and validate each data row
    for row_idx, row in enumerate(reader, start=2):
        # Skip completely blank lines
        if not row or all(c.strip() == "" for c in row):
            continue

        row_count += 1

        if len(row) != len(normalized_headers):
            errors.append(ValidationError(
                row_number=row_idx,
                code="MALFORMED_ROW",
                message=f"Row has {len(row)} values, but header specifies {len(normalized_headers)} columns.",
            ))
            continue

        # Extract values
        row_bill_num = row[header_indices["bill_number"]].strip()
        row_sku = row[header_indices["sku"]].strip()
        row_qty_str = row[header_indices["quantity"]].strip()
        row_price_str = row[header_indices["unit_price"]].strip()

        # Bill Number validation
        if not row_bill_num:
            errors.append(ValidationError(
                row_number=row_idx,
                field="bill_number",
                code="MISSING_VALUE",
                message="bill_number cannot be empty.",
            ))
        elif bill_number is None:
            bill_number = row_bill_num
        elif row_bill_num != bill_number:
            errors.append(ValidationError(
                row_number=row_idx,
                field="bill_number",
                code="MIXED_BILL_NUMBERS",
                message=f"Row has bill_number '{row_bill_num}', expected '{bill_number}'. A file can only contain one bill.",
            ))

        # SKU validation
        if not row_sku:
            errors.append(ValidationError(
                row_number=row_idx,
                field="sku",
                code="MISSING_VALUE",
                message="sku cannot be empty.",
            ))

        # Quantity validation
        qty_val: Optional[Decimal] = None
        try:
            qty_val = Decimal(row_qty_str)
            if qty_val <= Decimal("0"):
                errors.append(ValidationError(
                    row_number=row_idx,
                    field="quantity",
                    code="INVALID_QUANTITY",
                    message=f"quantity must be greater than zero, found '{row_qty_str}'.",
                ))
                qty_val = None
        except (InvalidOperation, TypeError):
            errors.append(ValidationError(
                row_number=row_idx,
                field="quantity",
                code="INVALID_QUANTITY",
                message=f"quantity '{row_qty_str}' is not a valid number.",
            ))

        # Unit Price validation
        price_val: Optional[Decimal] = None
        try:
            price_val = Decimal(row_price_str)
            if price_val < Decimal("0"):
                errors.append(ValidationError(
                    row_number=row_idx,
                    field="unit_price",
                    code="INVALID_PRICE",
                    message=f"unit_price cannot be negative, found '{row_price_str}'.",
                ))
                price_val = None
        except (InvalidOperation, TypeError):
            errors.append(ValidationError(
                row_number=row_idx,
                field="unit_price",
                code="INVALID_PRICE",
                message=f"unit_price '{row_price_str}' is not a valid number.",
            ))

        # Calculate line total
        line_total_val: Optional[Decimal] = None
        if qty_val is not None and price_val is not None:
            calculated_line_total = quantize_currency(qty_val * price_val)

            # Optional declared line_total check
            if "line_total" in header_indices:
                decl_line_str = row[header_indices["line_total"]].strip()
                if decl_line_str:
                    try:
                        decl_line_total = quantize_currency(Decimal(decl_line_str))
                        if decl_line_total != calculated_line_total:
                            errors.append(ValidationError(
                                row_number=row_idx,
                                field="line_total",
                                code="LINE_TOTAL_MISMATCH",
                                message=(
                                    f"Declared line_total ₹{decl_line_total} does not match "
                                    f"quantity ({qty_val}) × unit_price (₹{price_val}) = ₹{calculated_line_total}."
                                ),
                            ))
                        line_total_val = calculated_line_total
                    except (InvalidOperation, TypeError):
                        errors.append(ValidationError(
                            row_number=row_idx,
                            field="line_total",
                            code="INVALID_TOTAL",
                            message=f"line_total '{decl_line_str}' is not a valid number.",
                        ))
                else:
                    line_total_val = calculated_line_total
            else:
                line_total_val = calculated_line_total

        # Optional customer_name
        if "customer_name" in header_indices:
            c_name = row[header_indices["customer_name"]].strip()
            if c_name and customer_name is None:
                customer_name = c_name

        # Optional payment_method
        if "payment_method" in header_indices:
            p_method = row[header_indices["payment_method"]].strip()
            if p_method and payment_method is None:
                payment_method = p_method

        # Optional bill_total check
        if "bill_total" in header_indices:
            b_total_str = row[header_indices["bill_total"]].strip()
            if b_total_str:
                try:
                    curr_decl_total = quantize_currency(Decimal(b_total_str))
                    if declared_bill_total is None:
                        declared_bill_total = curr_decl_total
                    elif declared_bill_total != curr_decl_total:
                        errors.append(ValidationError(
                            row_number=row_idx,
                            field="bill_total",
                            code="INCONSISTENT_BILL_TOTAL",
                            message=(
                                f"Inconsistent bill_total declared on row {row_idx}: "
                                f"₹{curr_decl_total} differs from earlier row value ₹{declared_bill_total}."
                            ),
                        ))
                except (InvalidOperation, TypeError):
                    errors.append(ValidationError(
                        row_number=row_idx,
                        field="bill_total",
                        code="INVALID_TOTAL",
                        message=f"bill_total '{b_total_str}' is not a valid number.",
                    ))

        # Add item if valid
        if qty_val is not None and price_val is not None and line_total_val is not None and row_sku:
            items.append(ParsedLineItem(
                row_number=row_idx,
                sku=row_sku,
                quantity=qty_val,
                unit_price=price_val,
                line_total=line_total_val,
            ))
            calculated_total += line_total_val

    if row_count == 0:
        errors.append(ValidationError(
            code="EMPTY_FILE",
            message="CSV contains headers but no data rows.",
        ))

    calculated_total = quantize_currency(calculated_total)

    # Check declared bill_total against calculated total sum
    if declared_bill_total is not None and not errors:
        if declared_bill_total != calculated_total:
            errors.append(ValidationError(
                field="bill_total",
                code="BILL_TOTAL_MISMATCH",
                message=(
                    f"Declared bill_total ₹{declared_bill_total} does not match "
                    f"calculated sum of items ₹{calculated_total}."
                ),
            ))

    is_valid = len(errors) == 0

    return BillParseResult(
        is_valid=is_valid,
        bill_number=bill_number,
        customer_name=customer_name or "Walk-in Customer",
        payment_method=payment_method or "UPI",
        items=items,
        calculated_total=calculated_total,
        declared_total=declared_bill_total,
        errors=errors,
        warnings=warnings,
    )


def validate_skus_and_stock(
    parse_result: BillParseResult,
    db: Session,
) -> BillParseResult:
    """
    Validates parsed line items against the catalogue Product table.
    - Matches SKUs to existing products.
    - Detects duplicate SKU rows: aggregates if unit prices match, reports conflict error if unit prices differ.
    - Checks stock availability and issues warnings if requested quantity exceeds stock.
    - NEVER creates products or updates stock.
    """
    if not parse_result.items or not parse_result.is_valid:
        return parse_result

    errors = list(parse_result.errors)
    warnings = list(parse_result.warnings)

    # 1. Fetch products matching these SKUs
    skus_in_bill = {item.sku for item in parse_result.items}
    db_products = db.query(Product).filter(Product.sku.in_(skus_in_bill)).all()
    product_map: Dict[str, Product] = {p.sku: p for p in db_products}

    # 2. Duplicate SKU aggregation & conflict policy
    # Group items by SKU to inspect row counts and unit prices
    sku_groups: Dict[str, List[ParsedLineItem]] = {}
    for item in parse_result.items:
        sku_groups.setdefault(item.sku, []).append(item)

    processed_items: List[ParsedLineItem] = []

    for sku, group in sku_groups.items():
        # Check unknown SKU
        if sku not in product_map:
            for item in group:
                errors.append(ValidationError(
                    row_number=item.row_number,
                    field="sku",
                    code="UNKNOWN_SKU",
                    message=f"Product with SKU '{sku}' does not exist in the catalogue.",
                ))
            continue

        prod = product_map[sku]

        if len(group) == 1:
            item = group[0]
            item.product_id = prod.id
            item.product_name = prod.name
            item.current_stock = prod.stock_qty
            processed_items.append(item)
        else:
            # Duplicate SKU rows detected!
            # Check if unit prices are identical
            first_price = group[0].unit_price
            has_price_conflict = any(it.unit_price != first_price for it in group[1:])

            if has_price_conflict:
                row_numbers_str = ", ".join(str(it.row_number) for it in group)
                errors.append(ValidationError(
                    code="DUPLICATE_SKU_CONFLICT",
                    field="sku",
                    message=(
                        f"SKU '{sku}' appears {len(group)} times on rows ({row_numbers_str}) "
                        f"with conflicting unit prices. Line items must have identical unit prices to aggregate."
                    ),
                ))
            else:
                # Compatible: aggregate quantities
                total_qty = sum(it.quantity for it in group)
                agg_line_total = quantize_currency(total_qty * first_price)
                row_numbers_str = ", ".join(str(it.row_number) for it in group)

                aggregated_item = ParsedLineItem(
                    row_number=group[0].row_number,
                    sku=sku,
                    quantity=total_qty,
                    unit_price=first_price,
                    line_total=agg_line_total,
                    product_id=prod.id,
                    product_name=prod.name,
                    current_stock=prod.stock_qty,
                )
                processed_items.append(aggregated_item)
                warnings.append(ValidationWarning(
                    code="AGGREGATED_SKU_ROWS",
                    field="sku",
                    message=(
                        f"SKU '{sku}' appeared {len(group)} times (rows {row_numbers_str}) "
                        f"with identical unit price ₹{first_price}. Aggregated to quantity {total_qty}."
                    ),
                ))

    # 3. Check stock availability for matched products
    for item in processed_items:
        if item.product_id is not None:
            prod = product_map[item.sku]
            if Decimal(str(prod.stock_qty)) < item.quantity:
                warnings.append(ValidationWarning(
                    row_number=item.row_number,
                    field="quantity",
                    code="INSUFFICIENT_STOCK",
                    message=(
                        f"Insufficient stock for '{prod.name}' ({prod.sku}). "
                        f"Requested: {item.quantity} {prod.unit}, Available: {prod.stock_qty} {prod.unit}."
                    ),
                ))

    is_valid = len(errors) == 0

    return BillParseResult(
        is_valid=is_valid,
        bill_number=parse_result.bill_number,
        customer_name=parse_result.customer_name,
        payment_method=parse_result.payment_method,
        items=processed_items if is_valid else parse_result.items,
        calculated_total=parse_result.calculated_total,
        declared_total=parse_result.declared_total,
        errors=errors,
        warnings=warnings,
    )


def compute_file_hash(content: Union[bytes, str]) -> str:
    """
    Computes a deterministic SHA-256 hexadecimal digest from raw file bytes.

    Rules:
    - If bytes are provided, hashes them exactly as received without decoding or modifying.
    - If a string is provided for backward compatibility, encodes it as UTF-8 bytes and
      hashes the resulting bytes.
    - Does not strip BOM, normalize line endings, trim whitespace, or inspect contents.
    - Never incorporates filenames, timestamps, or system identifiers.
    - Returns a lowercase 64-character hexadecimal SHA-256 string.
    """
    if isinstance(content, bytes):
        raw_bytes = content
    elif isinstance(content, str):
        raw_bytes = content.encode("utf-8")
    else:
        raise TypeError(f"content must be bytes or str, got {type(content).__name__}")

    return hashlib.sha256(raw_bytes).hexdigest().lower()


def compute_canonical_fingerprint(
    bill: BillParseResult,
    store_id: str = "DEFAULT",
    source_system: str = "CSV_UPLOAD",
) -> Optional[str]:
    """
    Computes a deterministic canonical SHA-256 fingerprint from validated bill content.

    Participating Fields:
    1. store_id: Normalized store scope string.
    2. source_system: Normalized source system string.
    3. source_bill_number: Normalized string, or None if the bill has no bill number.
    4. items: Deterministically sorted list of product line items.
       - sku: Original SKU code (stripped of leading/trailing whitespace).
       - quantity: Normalized decimal string (e.g. '2' or '2.5').
       - unit_price: Standardized 2-decimal monetary string (e.g. '28.00').
       - line_total: Standardized 2-decimal monetary string (e.g. '56.00').
       Items are sorted by (sku, unit_price, quantity) to guarantee that CSV row order
       does not alter the fingerprint.
    5. calculated_total: Standardized 2-decimal monetary string (sum of line totals).

    Excluded Fields:
    - filename, timestamps, database IDs, preview tokens, and volatile metadata are EXCLUDED.
    - customer_name and payment_method are EXCLUDED to prevent duplicate-check evasion.

    Validation Rules:
    - If the bill is marked invalid (bill.is_valid is False) or has validation errors, returns None.
    - If the bill contains 0 items, returns None.

    Notice:
    A canonical fingerprint is a duplicate detection signal, not an unconditional guarantee
    that two bills represent the same transaction (especially if bill_number is missing).
    """
    if not bill.is_valid or not bill.items or len(bill.errors) > 0:
        return None

    normalized_items = []
    for item in bill.items:
        norm_qty = f"{item.quantity.normalize():f}" if "." in f"{item.quantity:f}" else f"{item.quantity:f}"
        norm_price = f"{quantize_currency(item.unit_price):.2f}"
        norm_line_total = f"{quantize_currency(item.line_total):.2f}"

        normalized_items.append({
            "sku": item.sku.strip(),
            "quantity": norm_qty,
            "unit_price": norm_price,
            "line_total": norm_line_total,
        })

    normalized_items.sort(key=lambda x: (x["sku"], x["unit_price"], x["quantity"]))

    canonical_payload = {
        "store_id": store_id.strip() if store_id else "DEFAULT",
        "source_system": source_system.strip() if source_system else "CSV_UPLOAD",
        "source_bill_number": bill.bill_number.strip() if bill.bill_number else None,
        "items": normalized_items,
        "calculated_total": f"{quantize_currency(bill.calculated_total):.2f}",
    }

    canonical_json = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest().lower()


class DuplicateClassification(str, Enum):
    NEW_IMPORT = "NEW_IMPORT"
    EXACT_FILE_DUPLICATE = "EXACT_FILE_DUPLICATE"
    SOURCE_BILL_DUPLICATE = "SOURCE_BILL_DUPLICATE"
    CANONICAL_CONTENT_MATCH = "CANONICAL_CONTENT_MATCH"
    PENDING_IMPORT_EXISTS = "PENDING_IMPORT_EXISTS"
    PREVIOUS_IMPORT_REJECTED = "PREVIOUS_IMPORT_REJECTED"
    PREVIOUS_IMPORT_FAILED = "PREVIOUS_IMPORT_FAILED"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"


class DuplicateCheckResult(BaseModel):
    """
    Structured outcome of duplicate detection check.

    Fields:
    - classification: Primary categorization code (from DuplicateClassification).
    - is_blocked: Whether creating/processing a new import must be blocked.
    - existing_import_id: Primary matched BillImport.id, or None if no match.
    - existing_status: Status of the matched BillImport, or None.
    - message: Human/developer-readable explanation of why this classification was assigned.
    - match_type: High-level match mechanism (EXACT_FILE_DUPLICATE, SOURCE_BILL_DUPLICATE, CANONICAL_CONTENT_MATCH, or None).
    - matched_by: List of field names that triggered a match (e.g. ["file_hash", "source_bill_number"]).
    - action: Recommended caller action (BLOCK, RESUME_PREVIEW, ALLOW_RETRY, PROCEED_WITH_ADVISORY, PROCEED).
    """
    classification: DuplicateClassification
    is_blocked: bool
    existing_import_id: Optional[int] = None
    existing_status: Optional[str] = None
    message: str
    match_type: Optional[str] = None
    matched_by: List[str] = Field(default_factory=list)
    action: str


def check_bill_duplicate_status(
    db: Session,
    file_hash: str,
    canonical_fingerprint: Optional[str] = None,
    source_bill_number: Optional[str] = None,
    store_id: str = "DEFAULT",
    source_system: str = "CSV_UPLOAD",
) -> DuplicateCheckResult:
    """
    Read-only duplicate lookup service evaluating incoming bill import data against existing records.

    Objective:
    Classifies existing imports accurately WITHOUT creating, modifying, committing, or deleting any database
    records or altering stock levels.

    Precedence and Decision Rules:
    1. Confirmed Imports (Highest Precedence - Hard Block):
       - If any record matching source bill (scoped to store_id and source_system) is CONFIRMED, return ALREADY_CONFIRMED (is_blocked=True).
       - If any record matching exact file_hash (scoped to store_id) is CONFIRMED, return ALREADY_CONFIRMED (is_blocked=True),
         even if source_bill_number is absent or differs. Never authorize duplicate sales or stock deduction.
    2. Pending and Validated Previews (High Precedence - Preview Conflict Block):
       - If a matching file or source bill has status PENDING or VALIDATED, return PENDING_IMPORT_EXISTS (is_blocked=True).
       - Signals caller to resume the existing preview rather than creating a new draft.
    3. Rejected and Failed Imports (Distinct Reporting - Non-Blocking at Lookup Level):
       - If a matching record was REJECTED, return PREVIOUS_IMPORT_REJECTED (is_blocked=False).
       - If a matching record was FAILED, return PREVIOUS_IMPORT_FAILED (is_blocked=False).
       - Does not inherently block a new retry attempt in business logic.
       - KNOWN LIMITATION: Because of the existing DB constraint `uq_bill_imports_source_bill` on
         (store_id, source_system, source_bill_number), re-inserting the same bill number without
         prior record cleanup or status update will cause an IntegrityError in the next phase.
    4. Canonical Content Matches (Supporting Evidence Only - Advisory):
       - If only canonical_fingerprint matches (scoped to store_id and source_system), return CANONICAL_CONTENT_MATCH (is_blocked=False).
       - Content match alone does not block transactions; distinct bill numbers with identical items can be legitimate repeat sales.
    5. No Match:
       - If all lookups find no match, return NEW_IMPORT (is_blocked=False).
       - Database exceptions or malformed inputs are NEVER converted to NEW_IMPORT.

    Concurrency Note:
    This function is strictly read-only. It provides no concurrency or locking guarantees; concurrent
    inserts can only be serialized via database-level unique constraints and transactions in mutation phases.
    """
    if not file_hash or not isinstance(file_hash, str) or not file_hash.strip():
        raise ValueError("file_hash must be a non-empty string.")

    norm_file_hash = file_hash.strip().lower()
    norm_store_id = store_id.strip() if store_id and store_id.strip() else "DEFAULT"
    norm_source_system = source_system.strip() if source_system and source_system.strip() else "CSV_UPLOAD"
    norm_source_bill_number = source_bill_number.strip() if source_bill_number and source_bill_number.strip() else None
    norm_canonical_fp = canonical_fingerprint.strip().lower() if canonical_fingerprint and canonical_fingerprint.strip() else None

    # 1. Query exact file hash matches (scoped to store_id)
    file_matches: List[BillImport] = (
        db.query(BillImport)
        .filter(
            BillImport.store_id == norm_store_id,
            BillImport.file_hash == norm_file_hash,
        )
        .order_by(BillImport.id.desc())
        .all()
    )

    # 2. Query source bill matches (scoped to store_id and source_system; strictly ignore None/empty)
    source_bill_matches: List[BillImport] = []
    if norm_source_bill_number is not None:
        source_bill_matches = (
            db.query(BillImport)
            .filter(
                BillImport.store_id == norm_store_id,
                BillImport.source_system == norm_source_system,
                BillImport.source_bill_number == norm_source_bill_number,
            )
            .order_by(BillImport.id.desc())
            .all()
        )

    # 3. Query canonical fingerprint matches (scoped to store_id and source_system)
    canonical_matches: List[BillImport] = []
    if norm_canonical_fp is not None:
        canonical_matches = (
            db.query(BillImport)
            .filter(
                BillImport.store_id == norm_store_id,
                BillImport.source_system == norm_source_system,
                BillImport.canonical_fingerprint == norm_canonical_fp,
            )
            .order_by(BillImport.id.desc())
            .all()
        )

    # --- Rule 1: Confirmed Imports (Hard Block) ---
    confirmed_source = next((imp for imp in source_bill_matches if imp.status == "CONFIRMED"), None)
    confirmed_file = next((imp for imp in file_matches if imp.status == "CONFIRMED"), None)

    if confirmed_source is not None or confirmed_file is not None:
        if confirmed_source is not None and confirmed_file is not None:
            if confirmed_source.id == confirmed_file.id:
                return DuplicateCheckResult(
                    classification=DuplicateClassification.ALREADY_CONFIRMED,
                    is_blocked=True,
                    existing_import_id=confirmed_source.id,
                    existing_status="CONFIRMED",
                    message=(
                        f"Bill '{norm_source_bill_number}' with identical file hash was already confirmed "
                        f"(Import ID: {confirmed_source.id}). Re-import blocked to prevent duplicate sales/stock deduction."
                    ),
                    match_type="EXACT_FILE_DUPLICATE",
                    matched_by=["source_bill_number", "file_hash"],
                    action="BLOCK",
                )
            else:
                return DuplicateCheckResult(
                    classification=DuplicateClassification.ALREADY_CONFIRMED,
                    is_blocked=True,
                    existing_import_id=confirmed_source.id,
                    existing_status="CONFIRMED",
                    message=(
                        f"Conflicting matches: Source bill '{norm_source_bill_number}' was already confirmed "
                        f"(Import ID: {confirmed_source.id}), and file hash matches another confirmed import "
                        f"(Import ID: {confirmed_file.id}). Re-import blocked."
                    ),
                    match_type="SOURCE_BILL_DUPLICATE",
                    matched_by=["source_bill_number", "file_hash"],
                    action="BLOCK",
                )
        elif confirmed_source is not None:
            return DuplicateCheckResult(
                classification=DuplicateClassification.ALREADY_CONFIRMED,
                is_blocked=True,
                existing_import_id=confirmed_source.id,
                existing_status="CONFIRMED",
                message=(
                    f"Source bill '{norm_source_bill_number}' was already confirmed "
                    f"(Import ID: {confirmed_source.id}). Re-import blocked to prevent duplicate sales/stock deduction."
                ),
                match_type="SOURCE_BILL_DUPLICATE",
                matched_by=["source_bill_number"],
                action="BLOCK",
            )
        else:
            return DuplicateCheckResult(
                classification=DuplicateClassification.ALREADY_CONFIRMED,
                is_blocked=True,
                existing_import_id=confirmed_file.id,
                existing_status="CONFIRMED",
                message=(
                    f"Exact file hash matches confirmed import ID {confirmed_file.id} "
                    f"(Bill: {confirmed_file.source_bill_number or 'None'}). "
                    f"Re-import blocked to prevent duplicate sales/stock deduction."
                ),
                match_type="EXACT_FILE_DUPLICATE",
                matched_by=["file_hash"],
                action="BLOCK",
            )

    # --- Rule 2: Pending or Validated Previews (Preview Conflict Block) ---
    pending_source = next((imp for imp in source_bill_matches if imp.status in ("PENDING", "VALIDATED")), None)
    pending_file = next((imp for imp in file_matches if imp.status in ("PENDING", "VALIDATED")), None)

    if pending_source is not None or pending_file is not None:
        if pending_source is not None and pending_file is not None:
            if pending_source.id == pending_file.id:
                return DuplicateCheckResult(
                    classification=DuplicateClassification.PENDING_IMPORT_EXISTS,
                    is_blocked=True,
                    existing_import_id=pending_source.id,
                    existing_status=pending_source.status,
                    message=(
                        f"Active preview already exists for bill '{norm_source_bill_number}' with identical file "
                        f"(Import ID: {pending_source.id}, status: {pending_source.status}). Re-import blocked."
                    ),
                    match_type="EXACT_FILE_DUPLICATE",
                    matched_by=["source_bill_number", "file_hash"],
                    action="RESUME_PREVIEW",
                )
            else:
                return DuplicateCheckResult(
                    classification=DuplicateClassification.PENDING_IMPORT_EXISTS,
                    is_blocked=True,
                    existing_import_id=pending_source.id,
                    existing_status=pending_source.status,
                    message=(
                        f"Active preview conflict: Source bill '{norm_source_bill_number}' has active preview "
                        f"(Import ID: {pending_source.id}, status: {pending_source.status}), and file hash matches "
                        f"another active preview (Import ID: {pending_file.id}, status: {pending_file.status}). Re-import blocked."
                    ),
                    match_type="SOURCE_BILL_DUPLICATE",
                    matched_by=["source_bill_number", "file_hash"],
                    action="RESUME_PREVIEW",
                )
        elif pending_source is not None:
            return DuplicateCheckResult(
                classification=DuplicateClassification.PENDING_IMPORT_EXISTS,
                is_blocked=True,
                existing_import_id=pending_source.id,
                existing_status=pending_source.status,
                message=(
                    f"Active preview already exists for source bill '{norm_source_bill_number}' "
                    f"(Import ID: {pending_source.id}, status: {pending_source.status}). Re-import blocked."
                ),
                match_type="SOURCE_BILL_DUPLICATE",
                matched_by=["source_bill_number"],
                action="RESUME_PREVIEW",
            )
        else:
            return DuplicateCheckResult(
                classification=DuplicateClassification.PENDING_IMPORT_EXISTS,
                is_blocked=True,
                existing_import_id=pending_file.id,
                existing_status=pending_file.status,
                message=(
                    f"Exact file hash matches existing active preview "
                    f"(Import ID: {pending_file.id}, status: {pending_file.status}). Re-import blocked."
                ),
                match_type="EXACT_FILE_DUPLICATE",
                matched_by=["file_hash"],
                action="RESUME_PREVIEW",
            )

    # --- Rule 3: Rejected and Failed Imports (Distinct Non-blocking Reports) ---
    prior_source = source_bill_matches[0] if source_bill_matches else None
    prior_file = file_matches[0] if file_matches else None

    if prior_source is not None or prior_file is not None:
        target_record = prior_source if prior_source is not None else prior_file
        matched_by = []
        if prior_source is not None:
            matched_by.append("source_bill_number")
        if prior_file is not None:
            matched_by.append("file_hash")

        match_type = "SOURCE_BILL_DUPLICATE" if prior_source is not None else "EXACT_FILE_DUPLICATE"

        if target_record.status == "REJECTED":
            classification = DuplicateClassification.PREVIOUS_IMPORT_REJECTED
            msg = (
                f"Previous import attempt for source bill '{norm_source_bill_number or target_record.source_bill_number or 'None'}' "
                f"was rejected (Import ID: {target_record.id}). Retry is not blocked by duplicate logic."
            )
        elif target_record.status == "FAILED":
            classification = DuplicateClassification.PREVIOUS_IMPORT_FAILED
            msg = (
                f"Previous import attempt for source bill '{norm_source_bill_number or target_record.source_bill_number or 'None'}' "
                f"failed (Import ID: {target_record.id}). Retry is not blocked by duplicate logic."
            )
        else:
            classification = (
                DuplicateClassification.SOURCE_BILL_DUPLICATE
                if prior_source is not None
                else DuplicateClassification.EXACT_FILE_DUPLICATE
            )
            msg = f"Existing import matched with status '{target_record.status}' (Import ID: {target_record.id})."

        return DuplicateCheckResult(
            classification=classification,
            is_blocked=False,
            existing_import_id=target_record.id,
            existing_status=target_record.status,
            message=msg,
            match_type=match_type,
            matched_by=matched_by,
            action="ALLOW_RETRY",
        )

    # --- Rule 4: Canonical Content Matches (Supporting Advisory Only) ---
    if canonical_matches:
        canonical_rec = canonical_matches[0]
        return DuplicateCheckResult(
            classification=DuplicateClassification.CANONICAL_CONTENT_MATCH,
            is_blocked=False,
            existing_import_id=canonical_rec.id,
            existing_status=canonical_rec.status,
            message=(
                f"Canonical content fingerprint matches existing import ID {canonical_rec.id} "
                f"(status: {canonical_rec.status}, bill: '{canonical_rec.source_bill_number or 'None'}'). "
                f"Because bill numbers differ or are absent, this may be a legitimate repeat sale. "
                f"Advisory match only; import is not blocked."
            ),
            match_type="CANONICAL_CONTENT_MATCH",
            matched_by=["canonical_fingerprint"],
            action="PROCEED_WITH_ADVISORY",
        )

    # --- Rule 5: No Match Found ---
    return DuplicateCheckResult(
        classification=DuplicateClassification.NEW_IMPORT,
        is_blocked=False,
        existing_import_id=None,
        existing_status=None,
        message="No existing import matching file hash, source bill, or canonical fingerprint. Safe to proceed as a new import.",
        match_type=None,
        matched_by=[],
        action="PROCEED",
    )


# ==============================================================================
# PHASE 4 & 5: Preview Registration, Idempotency, and Confirmation Services
# ==============================================================================

class PreviewItemSchema(BaseModel):
    row_number: Optional[int] = None
    sku: str
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    quantity: float
    unit_price: float
    line_total: float
    current_stock: Optional[float] = None


class PreviewDataPayload(BaseModel):
    bill_number: Optional[str] = None
    customer_name: Optional[str] = None
    payment_method: Optional[str] = None
    calculated_total: float
    declared_total: Optional[float] = None
    items: List[PreviewItemSchema] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    can_confirm: bool = False


class BillImportPreviewResponse(BaseModel):
    import_id: Optional[int] = None
    status: str
    source_filename: str
    source_system: str
    store_id: str
    file_hash: str
    canonical_fingerprint: Optional[str] = None
    source_bill_number: Optional[str] = None
    total_items: int = 0
    total_amount: float = 0.0
    duplicate_status: DuplicateCheckResult
    can_confirm: bool = False
    preview_data: PreviewDataPayload
    created_at: Optional[str] = None


class BillImportConfirmationResult(BaseModel):
    import_id: int
    sale_id: int
    bill_number: str
    status: str = "CONFIRMED"
    confirmed_at: str
    total_amount: float
    total_items: int
    deductions: List[Dict[str, Any]] = Field(default_factory=list)
    already_confirmed: bool = False
    message: str


def _build_preview_data_from_import(
    imp: BillImport,
    fallback_dup: Optional[DuplicateCheckResult] = None,
) -> BillImportPreviewResponse:
    """Helper to reconstruct BillImportPreviewResponse from a persisted BillImport record."""
    parsed_json: Dict[str, Any] = {}
    if imp.raw_data_json:
        try:
            parsed_json = json.loads(imp.raw_data_json)
        except Exception:
            parsed_json = {}

    items = [
        PreviewItemSchema(
            row_number=it.get("row_number"),
            sku=it.get("sku", ""),
            product_id=it.get("product_id"),
            product_name=it.get("product_name"),
            quantity=float(it.get("quantity", 0)),
            unit_price=float(it.get("unit_price", 0)),
            line_total=float(it.get("line_total", 0)),
            current_stock=float(it["current_stock"]) if it.get("current_stock") is not None else None,
        )
        for it in parsed_json.get("items", [])
    ]

    # Stock sufficiency check: can_confirm requires valid catalogue match AND sufficient current stock
    has_insufficient_stock = any(
        it.current_stock is not None and it.quantity > it.current_stock
        for it in items
    ) or any(
        w.get("code") == "INSUFFICIENT_STOCK"
        for w in parsed_json.get("warnings", [])
    )

    can_confirm = (
        imp.status in ("PENDING", "VALIDATED")
        and len(parsed_json.get("errors", [])) == 0
        and len(items) > 0
        and not has_insufficient_stock
    )

    preview_payload = PreviewDataPayload(
        bill_number=imp.source_bill_number or parsed_json.get("bill_number"),
        customer_name=parsed_json.get("customer_name") or "Walk-in Customer",
        payment_method=parsed_json.get("payment_method") or "UPI",
        calculated_total=float(imp.total_amount),
        declared_total=parsed_json.get("declared_total"),
        items=items,
        errors=parsed_json.get("errors", []),
        warnings=parsed_json.get("warnings", []),
        can_confirm=can_confirm,
    )

    dup_status = fallback_dup or DuplicateCheckResult(
        classification=DuplicateClassification.PENDING_IMPORT_EXISTS if imp.status in ("PENDING", "VALIDATED") else DuplicateClassification.ALREADY_CONFIRMED,
        is_blocked=imp.status == "CONFIRMED",
        existing_import_id=imp.id,
        existing_status=imp.status,
        message=f"Import #{imp.id} exists in status '{imp.status}'.",
        action="RESUME_PREVIEW" if imp.status in ("PENDING", "VALIDATED") else "BLOCK",
    )

    return BillImportPreviewResponse(
        import_id=imp.id,
        status=imp.status,
        source_filename=imp.source_filename,
        source_system=imp.source_system,
        store_id=imp.store_id,
        file_hash=imp.file_hash,
        canonical_fingerprint=imp.canonical_fingerprint,
        source_bill_number=imp.source_bill_number,
        total_items=imp.total_items,
        total_amount=float(imp.total_amount),
        duplicate_status=dup_status,
        can_confirm=can_confirm,
        preview_data=preview_payload,
        created_at=imp.created_at.isoformat() if imp.created_at else None,
    )


def register_bill_import_preview(
    db: Session,
    file_content: Union[bytes, str],
    filename: str,
    store_id: str = "DEFAULT",
    source_system: str = "CSV_UPLOAD",
) -> BillImportPreviewResponse:
    """
    Registers an uploaded bill as an auditable preview.

    Guarantees:
    - Never creates Sales or SaleItems.
    - Never modifies Product stock or creates StockMovements.
    - If a matching file or source bill already has an active preview (PENDING/VALIDATED),
      returns the existing preview to prevent competing active drafts.
    - If a matching bill is ALREADY_CONFIRMED, blocks creation and returns the conflict.
    - Persists reconstructible, validated data in `raw_data_json`.
    """
    raw_bytes = file_content if isinstance(file_content, bytes) else file_content.encode("utf-8")
    norm_store_id = store_id.strip() if store_id and store_id.strip() else "DEFAULT"
    norm_source_system = source_system.strip() if source_system and source_system.strip() else "CSV_UPLOAD"
    sanitized_filename = os.path.basename(filename)[:255] if filename else "unnamed.csv"

    # 1. Exact file hash from raw bytes
    file_hash = compute_file_hash(raw_bytes)

    # 2. Parse CSV syntactically
    parse_result = parse_csv_content(raw_bytes)

    # 3. Match SKUs and evaluate stock availability against catalogue
    validated_result = validate_skus_and_stock(parse_result, db)

    # 4. Compute canonical fingerprint from normalized content
    canonical_fp = compute_canonical_fingerprint(validated_result, norm_store_id, norm_source_system)

    # 5. Check duplicate status against database
    dup_status = check_bill_duplicate_status(
        db=db,
        file_hash=file_hash,
        canonical_fingerprint=canonical_fp,
        source_bill_number=validated_result.bill_number,
        store_id=norm_store_id,
        source_system=norm_source_system,
    )

    # 6. If blocked, do not insert a new record
    if dup_status.is_blocked:
        if dup_status.existing_import_id is not None:
            existing_rec = db.query(BillImport).filter(BillImport.id == dup_status.existing_import_id).first()
            if existing_rec:
                return _build_preview_data_from_import(existing_rec, fallback_dup=dup_status)

        # Return blocked preview with current parse details if existing record could not be loaded
        items = [
            PreviewItemSchema(
                row_number=it.row_number,
                sku=it.sku,
                product_id=it.product_id,
                product_name=it.product_name,
                quantity=float(it.quantity),
                unit_price=float(it.unit_price),
                line_total=float(it.line_total),
                current_stock=float(it.current_stock) if it.current_stock is not None else None,
            )
            for it in validated_result.items
        ]
        return BillImportPreviewResponse(
            import_id=None,
            status="BLOCKED",
            source_filename=sanitized_filename,
            source_system=norm_source_system,
            store_id=norm_store_id,
            file_hash=file_hash,
            canonical_fingerprint=canonical_fp,
            source_bill_number=validated_result.bill_number,
            total_items=len(validated_result.items),
            total_amount=float(quantize_currency(validated_result.calculated_total)),
            duplicate_status=dup_status,
            can_confirm=False,
            preview_data=PreviewDataPayload(
                bill_number=validated_result.bill_number,
                customer_name=validated_result.customer_name,
                payment_method=validated_result.payment_method,
                calculated_total=float(quantize_currency(validated_result.calculated_total)),
                declared_total=float(quantize_currency(validated_result.declared_total)) if validated_result.declared_total is not None else None,
                items=items,
                errors=[err.model_dump() for err in validated_result.errors],
                warnings=[w.model_dump() for w in validated_result.warnings],
                can_confirm=False,
            ),
            created_at=datetime.utcnow().isoformat(),
        )

    # 7. Prepare persistent representation
    status = "VALIDATED" if validated_result.is_valid else "REJECTED"
    first_error = validated_result.errors[0].message if validated_result.errors else None

    items_payload = [
        {
            "row_number": it.row_number,
            "sku": it.sku,
            "product_id": it.product_id,
            "product_name": it.product_name,
            "quantity": float(it.quantity),
            "unit_price": float(it.unit_price),
            "line_total": float(it.line_total),
            "current_stock": float(it.current_stock) if it.current_stock is not None else None,
        }
        for it in validated_result.items
    ]

    has_insufficient_stock = any(
        w.code == "INSUFFICIENT_STOCK" for w in validated_result.warnings
    )
    can_confirm_preview = validated_result.is_valid and not has_insufficient_stock

    persisted_payload = {
        "bill_number": validated_result.bill_number,
        "customer_name": validated_result.customer_name,
        "payment_method": validated_result.payment_method,
        "calculated_total": float(quantize_currency(validated_result.calculated_total)),
        "declared_total": float(quantize_currency(validated_result.declared_total)) if validated_result.declared_total is not None else None,
        "items": items_payload,
        "errors": [err.model_dump() for err in validated_result.errors],
        "warnings": [w.model_dump() for w in validated_result.warnings],
        "can_confirm": can_confirm_preview,
    }

    import_record = BillImport(
        source_filename=sanitized_filename,
        source_system=norm_source_system,
        store_id=norm_store_id,
        file_hash=file_hash,
        canonical_fingerprint=canonical_fp,
        source_bill_number=validated_result.bill_number,
        status=status,
        total_items=len(validated_result.items),
        total_amount=float(quantize_currency(validated_result.calculated_total)),
        raw_data_json=json.dumps(persisted_payload),
        error_message=first_error,
        created_at=datetime.utcnow(),
    )

    try:
        db.add(import_record)
        db.commit()
        db.refresh(import_record)
    except IntegrityError:
        db.rollback()
        # Handle race condition where a concurrent request registered the active bill
        conflict_dup = check_bill_duplicate_status(
            db=db,
            file_hash=file_hash,
            canonical_fingerprint=canonical_fp,
            source_bill_number=validated_result.bill_number,
            store_id=norm_store_id,
            source_system=norm_source_system,
        )
        if conflict_dup.existing_import_id:
            existing = db.query(BillImport).filter(BillImport.id == conflict_dup.existing_import_id).first()
            if existing:
                return _build_preview_data_from_import(existing, fallback_dup=conflict_dup)
        raise

    return _build_preview_data_from_import(import_record, fallback_dup=dup_status)


def get_bill_import_preview(
    db: Session,
    import_id: int,
    store_id: str = "DEFAULT",
) -> BillImportPreviewResponse:
    """Retrieves an existing preview by import ID with store scoping verification."""
    imp = db.query(BillImport).filter(BillImport.id == import_id).first()
    if not imp:
        raise ValueError(f"Import #{import_id} not found.")

    norm_store = store_id.strip() if store_id else "DEFAULT"
    if imp.store_id != norm_store:
        raise PermissionError(f"Access denied: Import #{import_id} does not belong to store '{norm_store}'.")

    return _build_preview_data_from_import(imp)


def confirm_bill_import(
    db: Session,
    import_id: int,
    store_id: str = "DEFAULT",
) -> BillImportConfirmationResult:
    """
    Confirms an eligible bill import and synchronizes inventory within a single atomic transaction.

    Atomic Invariants:
    1. Import must exist, be in PENDING or VALIDATED status, and match store_id.
    2. If already CONFIRMED, returns existing confirmation payload idempotently without duplicating sale or stock deduction.
    3. Re-validates current product stock against catalogue. Fails if stock is insufficient.
    4. Creates `Sale` record.
    5. Creates `SaleItem` records.
    6. Deducts product stock.
    7. Creates `StockMovement` records (with negative quantity_change).
    8. Updates `BillImport` to CONFIRMED with sale_id and timestamp.
    9. Rolls back entire transaction if any step fails.
    """
    norm_store = store_id.strip() if store_id else "DEFAULT"

    # Query import
    imp = db.query(BillImport).filter(BillImport.id == import_id).first()
    if not imp:
        raise ValueError(f"Import #{import_id} not found.")

    if imp.store_id != norm_store:
        raise PermissionError(f"Access denied: Import #{import_id} does not belong to store '{norm_store}'.")

    # Idempotent re-entry check: already confirmed
    if imp.status == "CONFIRMED":
        sale = db.query(Sale).filter(Sale.id == imp.sale_id).first() if imp.sale_id else None
        return BillImportConfirmationResult(
            import_id=imp.id,
            sale_id=imp.sale_id or 0,
            bill_number=sale.bill_number if sale else (imp.source_bill_number or f"BV-IMP-{imp.id}"),
            status="CONFIRMED",
            confirmed_at=imp.confirmed_at.isoformat() if imp.confirmed_at else datetime.utcnow().isoformat(),
            total_amount=float(imp.total_amount),
            total_items=imp.total_items,
            deductions=[],
            already_confirmed=True,
            message="Import was already confirmed. No duplicate sale or stock deduction performed.",
        )

    if imp.status not in ("PENDING", "VALIDATED"):
        raise ValueError(f"Import #{import_id} is in status '{imp.status}' and cannot be confirmed.")

    if not imp.raw_data_json:
        raise ValueError(f"Import #{import_id} contains no persisted preview data.")

    try:
        preview_data = json.loads(imp.raw_data_json)
    except Exception as exc:
        raise ValueError(f"Persisted preview data for import #{import_id} is malformed: {str(exc)}")

    items = preview_data.get("items", [])
    if not items:
        raise ValueError(f"Import #{import_id} contains no items.")

    # Acquire write protection on SQLite before the critical read-and-write sequence
    bind = db.get_bind()
    if bind and bind.dialect.name == "sqlite":
        try:
            db.execute(text("BEGIN IMMEDIATE"))
        except Exception as exc:
            err_str = str(exc).lower()
            if "locked" in err_str or "busy" in err_str:
                raise RuntimeError("Database is currently locked by another concurrent confirmation. Please retry.")

    # Re-verify catalogue products and available stock
    skus = [it["sku"] for it in items]
    db_products = {p.sku: p for p in db.query(Product).filter(Product.sku.in_(skus)).all()}

    # Check for missing SKUs
    missing_skus = set(skus) - set(db_products.keys())
    if missing_skus:
        raise ValueError(f"Cannot confirm: SKUs {sorted(list(missing_skus))} no longer exist in catalogue.")

    # Check stock requirements (aggregating required quantities by SKU)
    sku_required_qty: Dict[str, float] = {}
    for it in items:
        sku = it["sku"]
        sku_required_qty[sku] = sku_required_qty.get(sku, 0.0) + float(it["quantity"])

    for sku, req_qty in sku_required_qty.items():
        prod = db_products[sku]
        if prod.stock_qty < req_qty:
            raise ValueError(
                f"Cannot confirm: Insufficient stock for '{prod.name}' ({prod.sku}). "
                f"Requested: {req_qty} {prod.unit}, Available: {prod.stock_qty} {prod.unit}."
            )

    # Determine unique bill_number for Sale
    sale_bill_number = imp.source_bill_number or f"BV-IMP-{imp.id}"

    # Verify no conflicting sale exists with that bill number
    existing_sale = db.query(Sale).filter(Sale.bill_number == sale_bill_number).first()
    if existing_sale:
        raise ValueError(f"A Sale with bill number '{sale_bill_number}' already exists in the system.")

    try:
        pm = (preview_data.get("payment_method") or "UPI").upper()
        target_store = imp.store_id or "DEFAULT"
        acc = get_or_create_default_account(
            db,
            store_id=target_store,
            account_type="CASH" if pm == "CASH" else "BANK"
        )
        # 1. Create Sale
        sale = Sale(
            bill_number=sale_bill_number,
            customer_name=preview_data.get("customer_name") or "Walk-in Customer",
            payment_method=preview_data.get("payment_method") or "UPI",
            business_account_id=acc.id,
            status="Completed",
            total=float(imp.total_amount),
            store_id=target_store,
            created_at=datetime.utcnow(),
        )
        db.add(sale)
        db.flush()  # Populates sale.id

        if pm not in ("CREDIT", "KHATA", "UDHAAR"):
            record_account_transaction(
                db=db,
                account_id=acc.id,
                transaction_type="INCOME",
                amount=float(imp.total_amount),
                reference_type="BILL_IMPORT",
                reference_id=str(imp.id),
                description=f"Imported bill {sale_bill_number}",
                store_id=target_store,
            )

        # 2. Create SaleItems
        for it in items:
            prod = db_products[it["sku"]]
            sale_item = SaleItem(
                sale_id=sale.id,
                product_id=prod.id,
                product_name=prod.name,
                quantity=float(it["quantity"]),
                unit_price=float(it["unit_price"]),
                unit_cost=float(prod.cost_price),
                line_total=float(it["line_total"]),
            )
            db.add(sale_item)

        # 3. Deduct stock and record auditable StockMovement
        deductions = []
        for it in items:
            prod = db_products[it["sku"]]
            qty = float(it["quantity"])
            new_stock = round(prod.stock_qty - qty, 4)
            prod.stock_qty = new_stock

            movement = StockMovement(
                product_id=prod.id,
                quantity_change=-qty,  # Negative sign convention
                balance_after=new_stock,
                reason="BILL_IMPORT",
                bill_import_id=imp.id,
                sale_id=sale.id,
                notes=f"Import confirmed from {imp.source_filename}",
                created_at=datetime.utcnow(),
            )
            db.add(movement)
            deductions.append({
                "sku": prod.sku,
                "product_name": prod.name,
                "quantity_deducted": qty,
                "balance_after": new_stock,
            })

        # 4. Finalize BillImport status
        now = datetime.utcnow()
        imp.status = "CONFIRMED"
        imp.sale_id = sale.id
        imp.confirmed_at = now

        db.commit()
        db.refresh(imp)

        return BillImportConfirmationResult(
            import_id=imp.id,
            sale_id=sale.id,
            bill_number=sale.bill_number,
            status="CONFIRMED",
            confirmed_at=now.isoformat(),
            total_amount=float(imp.total_amount),
            total_items=imp.total_items,
            deductions=deductions,
            already_confirmed=False,
            message="Bill import confirmed and inventory synchronized successfully.",
        )

    except Exception:
        db.rollback()
        raise


def reject_bill_import_preview(
    db: Session,
    import_id: int,
    reason: Optional[str] = None,
    store_id: str = "DEFAULT",
) -> BillImportPreviewResponse:
    """Rejects or cancels an active preview (PENDING/VALIDATED)."""
    norm_store = store_id.strip() if store_id else "DEFAULT"
    imp = db.query(BillImport).filter(BillImport.id == import_id).first()
    if not imp:
        raise ValueError(f"Import #{import_id} not found.")

    if imp.store_id != norm_store:
        raise PermissionError(f"Access denied: Import #{import_id} does not belong to store '{norm_store}'.")

    if imp.status == "CONFIRMED":
        raise ValueError(f"Cannot reject: Import #{import_id} is already confirmed.")

    imp.status = "REJECTED"
    imp.error_message = reason or "Preview rejected by retailer"
    db.commit()
    db.refresh(imp)

    return _build_preview_data_from_import(imp)


def list_bill_imports(
    db: Session,
    store_id: str = "DEFAULT",
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Returns a paginated list of bill import attempts scoped to store_id."""
    norm_store = store_id.strip() if store_id else "DEFAULT"
    limit = max(1, min(limit, 100))
    skip = max(0, skip)

    query = db.query(BillImport).filter(BillImport.store_id == norm_store)
    if status and status.strip():
        query = query.filter(BillImport.status == status.strip().upper())

    records = query.order_by(BillImport.created_at.desc()).offset(skip).limit(limit).all()

    return [
        {
            "id": imp.id,
            "source_filename": imp.source_filename,
            "source_system": imp.source_system,
            "store_id": imp.store_id,
            "source_bill_number": imp.source_bill_number,
            "status": imp.status,
            "total_items": imp.total_items,
            "total_amount": float(imp.total_amount),
            "sale_id": imp.sale_id,
            "created_at": imp.created_at.isoformat() if imp.created_at else None,
            "confirmed_at": imp.confirmed_at.isoformat() if imp.confirmed_at else None,
            "error_message": imp.error_message,
        }
        for imp in records
    ]


