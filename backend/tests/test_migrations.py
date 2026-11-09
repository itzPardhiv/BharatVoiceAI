import os
import sqlite3
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models import Product, Sale, SaleItem, BillImport, StockMovement
from app.migrations import run_migrations, backup_database


@pytest.fixture
def temp_db(tmp_path):
    """Creates a temporary SQLite database file for testing migration execution."""
    db_file = str(tmp_path / "test_migration.db")

    # Initialize an older schema with the table-level UNIQUE constraint
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(160) NOT NULL,
            category VARCHAR(100) NOT NULL,
            unit VARCHAR(30) NOT NULL,
            price FLOAT NOT NULL,
            cost_price FLOAT NOT NULL,
            stock_qty FLOAT NOT NULL,
            reorder_level FLOAT NOT NULL,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            bill_number VARCHAR(50) NOT NULL UNIQUE,
            customer_name VARCHAR(160),
            payment_method VARCHAR(30) NOT NULL,
            status VARCHAR(30) NOT NULL,
            total FLOAT NOT NULL,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE bill_imports (
            id INTEGER PRIMARY KEY,
            source_filename VARCHAR(255) NOT NULL,
            source_system VARCHAR(50) NOT NULL DEFAULT 'CSV_UPLOAD',
            store_id VARCHAR(50) NOT NULL DEFAULT 'DEFAULT',
            file_hash VARCHAR(64) NOT NULL,
            canonical_fingerprint VARCHAR(64),
            source_bill_number VARCHAR(100),
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            total_items INTEGER NOT NULL DEFAULT 0,
            total_amount FLOAT NOT NULL DEFAULT 0.0,
            sale_id INTEGER UNIQUE,
            raw_data_json TEXT,
            error_message TEXT,
            created_at DATETIME NOT NULL,
            confirmed_at DATETIME,
            CONSTRAINT ck_bill_imports_status CHECK (status IN ('PENDING', 'VALIDATED', 'CONFIRMED', 'REJECTED', 'FAILED')),
            CONSTRAINT uq_bill_imports_source_bill UNIQUE (store_id, source_system, source_bill_number)
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            quantity_change FLOAT NOT NULL,
            balance_after FLOAT NOT NULL,
            reason VARCHAR(50) NOT NULL DEFAULT 'BILL_IMPORT',
            bill_import_id INTEGER,
            sale_id INTEGER,
            notes VARCHAR(255),
            created_at DATETIME NOT NULL
        );
        -- Seed some existing data
        INSERT INTO products VALUES (1, 'SKU-1', 'Product 1', 'General', 'pack', 50, 40, 10, 5, '2026-10-28 10:00:00');
        INSERT INTO sales VALUES (1, 'BILL-100', 'Customer A', 'UPI', 'Completed', 50.0, '2026-10-28 10:00:00');
        """
    )
    conn.commit()
    conn.close()

    return db_file


def test_migration_preserves_existing_data_and_updates_schema(temp_db):
    """Verify that run_migrations migrates the table and preserves all existing rows."""
    res = run_migrations(temp_db)
    assert res is True

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Verify products and sales survived
    cursor.execute("SELECT COUNT(*) FROM products;")
    assert cursor.fetchone()[0] == 1
    cursor.execute("SELECT COUNT(*) FROM sales;")
    assert cursor.fetchone()[0] == 1

    # Verify old constraint is gone from table DDL
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bill_imports';")
    schema = cursor.fetchone()[0]
    assert "uq_bill_imports_source_bill" not in schema

    # Verify partial unique index exists
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_bill_imports_active_source_bill';")
    idx_sql = cursor.fetchone()[0]
    assert "WHERE status IN ('PENDING', 'VALIDATED', 'CONFIRMED')" in idx_sql

    conn.close()


def test_retries_permitted_for_rejected_and_failed_source_bills(temp_db):
    """Verify that multiple rejected/failed records can coexist with the same source bill number."""
    run_migrations(temp_db)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Insert initial rejected attempt
    cursor.execute(
        """
        INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
        VALUES ('bill_v1.csv', 'DEFAULT', 'CSV_UPLOAD', 'BILL-RETRY-01', 'hash1', 'REJECTED', '2026-10-28 10:00:00');
        """
    )
    # Insert second attempt that failed
    cursor.execute(
        """
        INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
        VALUES ('bill_v2.csv', 'DEFAULT', 'CSV_UPLOAD', 'BILL-RETRY-01', 'hash2', 'FAILED', '2026-10-28 10:05:00');
        """
    )
    # Insert third attempt that is active (PENDING)
    cursor.execute(
        """
        INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
        VALUES ('bill_v3.csv', 'DEFAULT', 'CSV_UPLOAD', 'BILL-RETRY-01', 'hash3', 'PENDING', '2026-10-28 10:10:00');
        """
    )
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM bill_imports WHERE source_bill_number = 'BILL-RETRY-01';")
    assert cursor.fetchone()[0] == 3

    conn.close()


def test_active_and_confirmed_source_bills_strictly_enforce_uniqueness(temp_db):
    """Verify that two active or confirmed imports with the same source bill number raise IntegrityError."""
    run_migrations(temp_db)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Active pending import
    cursor.execute(
        """
        INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
        VALUES ('bill1.csv', 'DEFAULT', 'CSV_UPLOAD', 'BILL-CONFLICT-01', 'hash1', 'PENDING', '2026-10-28 10:00:00');
        """
    )
    conn.commit()

    # Second active import with same source bill number must fail under partial index
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
            VALUES ('bill2.csv', 'DEFAULT', 'CSV_UPLOAD', 'BILL-CONFLICT-01', 'hash2', 'VALIDATED', '2026-10-28 10:01:00');
            """
        )

    # Confirmed import with same source bill number must also fail
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
            VALUES ('bill3.csv', 'DEFAULT', 'CSV_UPLOAD', 'BILL-CONFLICT-01', 'hash3', 'CONFIRMED', '2026-10-28 10:02:00');
            """
        )

    conn.close()


def test_nullable_bill_numbers_allowed_multiple_active(temp_db):
    """Verify multiple active imports with source_bill_number = NULL are allowed."""
    run_migrations(temp_db)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
        VALUES ('walkin1.csv', 'DEFAULT', 'CSV_UPLOAD', NULL, 'hash1', 'PENDING', '2026-10-28 10:00:00');
        """
    )
    cursor.execute(
        """
        INSERT INTO bill_imports (source_filename, store_id, source_system, source_bill_number, file_hash, status, created_at)
        VALUES ('walkin2.csv', 'DEFAULT', 'CSV_UPLOAD', NULL, 'hash2', 'PENDING', '2026-10-28 10:01:00');
        """
    )
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM bill_imports WHERE source_bill_number IS NULL;")
    assert cursor.fetchone()[0] == 2
    conn.close()


def test_migration_rerun_is_idempotent_and_fast(temp_db):
    """Verify that running migrations multiple times is idempotent, uses schema_migrations, and does not re-migrate."""
    res1 = run_migrations(temp_db)
    assert res1 is True

    # Check that schema_migrations has recorded the version
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM schema_migrations;")
    versions = [r[0] for r in cursor.fetchall()]
    assert "20261028_01_active_source_bill_and_stock_constraints" in versions
    conn.close()

    # Second run must return False and perform no table rebuild
    res2 = run_migrations(temp_db)
    assert res2 is False


def test_migration_rollback_on_failure(temp_db, monkeypatch):
    """Verify that if an error occurs during migration, the transaction rolls back cleanly leaving original data intact."""
    # Insert an existing product and sale
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM products;")
    assert cursor.fetchone()[0] == 1
    conn.close()

    # Monkeypatch cursor.execute to raise error when inserting into bill_imports_new
    original_connect = sqlite3.connect

    class FaultyCursor(sqlite3.Cursor):
        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO bill_imports_new" in sql:
                raise RuntimeError("Simulated migration failure during table swap!")
            return super().execute(sql, *args, **kwargs)

    class FaultyConnection(sqlite3.Connection):
        def cursor(self):
            return FaultyCursor(self)

    # Patch sqlite3.connect for the migration run
    def faulty_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.row_factory = sqlite3.Row
        return FaultyConnection(*args, **kwargs)

    # Verify that run_migrations raises the exception and rolls back
    with monkeypatch.context() as m:
        m.setattr(sqlite3, "connect", faulty_connect)
        with pytest.raises(RuntimeError, match="Simulated migration failure"):
            run_migrations(temp_db)

    # Verify database is intact
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM products;")
    assert cursor.fetchone()[0] == 1
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bill_imports';")
    assert cursor.fetchone() is not None
    conn.close()


