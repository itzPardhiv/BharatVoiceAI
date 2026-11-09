# BharatVoice AI — Smart Bill Import & Sync: Operations, Observability & Recovery Guide

## 1. Overview & Architecture

The Smart Bill Import & Sync module provides a reliable, secure, audited CSV sales bill ingestion pipeline for Indian retailers. It guarantees the central business invariant:
> **An imported bill must never create duplicate sales or cause inventory to be deducted twice.**

### Key System Guarantees:
1. **Separation of Preview & Confirmation**:
   - `POST /api/bill-imports/upload` registers a preview in `VALIDATED` (or `REJECTED`) status.
   - Zero sales or stock deductions are ever executed during the upload or preview phase.
2. **Deterministic Duplicate Prevention**:
   - Exact file hash (`file_hash` SHA-256 over raw uploaded bytes) prevents re-uploading identical files.
   - Source bill identity `(store_id, source_system, source_bill_number)` prevents duplicate real-world transactions.
   - Active and confirmed imports are protected via the SQLite partial unique index:
     `uq_bill_imports_active_source_bill WHERE status IN ('PENDING', 'VALIDATED', 'CONFIRMED') AND source_bill_number IS NOT NULL`
3. **Atomic Financial & Inventory Invariants**:
   - In `POST /api/bill-imports/{id}/confirm`, Sale creation, SaleItem snapshots, Product stock deduction, StockMovement auditing, and status update to `CONFIRMED` execute in **one atomic transaction**.
   - If any step fails (e.g. insufficient stock, missing product, database error), the entire transaction rolls back cleanly with zero orphaned records.
4. **Idempotency**:
   - Repeating a confirmation request on an already confirmed bill returns the existing confirmation payload with `already_confirmed: true` without deducting stock again or creating a duplicate sale.

---

## 2. Bill Import Lifecycle

```
[ Upload CSV ]
      │
      ▼
   PENDING  ──(SKUs & Stock Validated)──►  VALIDATED  ──(Confirmed)──►  CONFIRMED (Terminal)
      │                                       │                             │
      ▼ (Errors / Price conflict)             ▼ (Retailer rejects)          ▼
   REJECTED                                REJECTED                  Creates Sale &
 (Can be retried)                        (Can be retried)             Deducts Stock
```

- **`PENDING`**: CSV received and parsed syntactically.
- **`VALIDATED`**: Catalogue matching confirmed, prices reconciled, stock sufficient. Ready for confirmation.
- **`CONFIRMED`**: Retailer explicitly authorized. Sale created, stock deducted, `StockMovement` recorded.
- **`REJECTED`**: Validation failed (e.g. unknown SKU) or cancelled by retailer. Retailer may correct the bill and retry.
- **`FAILED`**: Unhandled runtime error during processing. Audit preserved, retries permitted.

---

## 3. Database Migration & Point-in-Time Backup

### Automated SQLite Migration Mechanism
Located at `backend/app/migrations.py`. Run automatically on application startup or manually via:
```python
from app.migrations import run_migrations
run_migrations("bharatvoice.db")
```

### Safe Backup Protocol
Before any schema transformation, an online SQLite backup is created using the SQLite Online Backup API:
```python
from app.migrations import backup_database
backup_path = backup_database("bharatvoice.db")
```
This produces a byte-accurate snapshot (e.g., `bharatvoice.db.backup_20261028_103137`) that accounts for active write transactions.

---

## 4. Recovery Procedures

### Scenario A: Client Timeout After Confirmation Commit
- **Symptom**: Client network drops right after backend commits the confirmation transaction. The retailer does not receive the HTTP 200 response and clicks "Confirm" again.
- **System Behavior**: The backend re-checks `status == 'CONFIRMED'`, detects existing `sale_id`, and immediately returns HTTP 200 with `already_confirmed: true` and the existing confirmation details.
- **Resolution**: Automatic. No intervention required; zero double deductions.

### Scenario B: Draft Preview Stuck or Abandoned
- **Symptom**: A retailer uploads a bill, inspects the preview, but leaves without confirming or rejecting.
- **System Behavior**: The bill remains in `VALIDATED` status. A re-upload of the same bill will return the active preview to resume it.
- **Resolution**: Retailer can either click "Confirm Bill" to complete it, or click "Reject / Cancel Draft" (`POST /api/bill-imports/{id}/reject`) to mark it as `REJECTED`, which frees the bill number for an updated upload.

### Scenario C: Stock Depleted Between Preview and Confirmation
- **Symptom**: Another transaction sells out an item while a preview is open.
- **System Behavior**: At confirmation time, `confirm_bill_import` re-reads current stock from the database. It detects `stock_qty < requested_qty`, rejects the confirmation with HTTP 400 (`"Insufficient stock for ..."`), and rolls back completely.
- **Resolution**: Retailer replenishes stock via catalogue adjustment or removes the item from the bill.

---

## 5. Accounting & Reconciliation Invariants

1. `Sale.total` must equal `sum(SaleItem.line_total)` under Decimal `paise` quantization (`0.01`).
2. For each confirmed sale line of quantity `Q`, `Product.stock_qty` decreases by `Q`.
3. Every inventory reduction generates an auditable `StockMovement` with:
   - `quantity_change = -Q` (negative)
   - `balance_after = committed stock balance`
   - `reason = "BILL_IMPORT"`
   - `bill_import_id = import_record.id`
   - `sale_id = sale.id`
4. Exactly one `BillImport` links to each `Sale` via `BillImport.sale_id` (`UNIQUE`).

