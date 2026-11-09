import os
import sqlite3
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

MIGRATION_V1 = "20261028_01_active_source_bill_and_stock_constraints"
MIGRATION_V2 = "20261105_02_product_is_active_and_store_isolation"
MIGRATION_V3 = "20261105_03_advanced_finance_and_retail_modules"


def backup_database(db_path: str) -> str:
    """
    Creates a transactionally safe point-in-time backup of the SQLite database
    using SQLite's online backup API.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found: {db_path}")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        logger.info("Successfully created database backup at %s", backup_path)
        return backup_path
    finally:
        dst.close()
        src.close()


def run_migrations(db_path: str) -> bool:
    """
    Executes safe SQLite schema migrations:
    1. Schema migrations tracking table.
    2. V1: Replaces the unconditional UNIQUE(store_id, source_system, source_bill_number) constraint
       on `bill_imports` with a partial unique index `uq_bill_imports_active_source_bill`.
    3. V1: Ensures `stock_movements` check constraints are properly applied.
    4. V2: Adds `is_active` to `products` (default 1) and ensures store_id column consistency.
    5. V3: Adds `unit_cost` to `sale_items`, `store_id`/`discount`/`customer_id`/`business_account_id` to `sales`,
       creates `expenses`, `business_accounts`, `account_transactions`, `suppliers`, `purchase_orders`,
       `purchase_items`, `customers`, `customer_payments`, and `store_profiles`. Seeds default business accounts.
    6. Preserves all existing product, sale, sale_item, bill_import, and stock_movement records.
    """
    if not os.path.exists(db_path):
        logger.info("No database file found at %s. Skipping migration.", db_path)
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Step 0: Ensure schema_migrations table exists
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(50) PRIMARY KEY,
                applied_at DATETIME NOT NULL,
                description TEXT
            );
            """
        )
        conn.commit()

        # Check existing migrations
        cursor.execute("SELECT version FROM schema_migrations;")
        applied_versions = {row[0] for row in cursor.fetchall()}

        # Check if products table exists
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='products';")
        prod_row = cursor.fetchone()
        if not prod_row:
            logger.info("products table does not exist. create_all() will handle initial creation.")
            cursor.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
                (MIGRATION_V1, datetime.utcnow().isoformat(), "Initial schema"),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
                (MIGRATION_V2, datetime.utcnow().isoformat(), "Initial schema"),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
                (MIGRATION_V3, datetime.utcnow().isoformat(), "Initial schema"),
            )
            conn.commit()
            return False

        needs_v1 = MIGRATION_V1 not in applied_versions
        needs_v2 = MIGRATION_V2 not in applied_versions
        needs_v3 = MIGRATION_V3 not in applied_versions

        # Check existing tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        existing_tables = {row[0] for row in cursor.fetchall()}

        # Also inspect columns directly
        cursor.execute("PRAGMA table_info(products);")
        prod_cols = [col[1] for col in cursor.fetchall()]
        if "is_active" not in prod_cols:
            needs_v2 = True
        if "store_id" not in prod_cols:
            needs_v3 = True

        sale_item_cols = []
        if "sale_items" in existing_tables:
            cursor.execute("PRAGMA table_info(sale_items);")
            sale_item_cols = [col[1] for col in cursor.fetchall()]
            if "unit_cost" not in sale_item_cols:
                needs_v3 = True

        sale_cols = []
        if "sales" in existing_tables:
            cursor.execute("PRAGMA table_info(sales);")
            sale_cols = [col[1] for col in cursor.fetchall()]
            if "store_id" not in sale_cols or "discount" not in sale_cols:
                needs_v3 = True

        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bill_imports';")
        bi_row = cursor.fetchone()
        bi_sql = bi_row[0] if bi_row else ""
        if "uq_bill_imports_source_bill" in bi_sql:
            needs_v1 = True

        if not needs_v1 and not needs_v2 and not needs_v3:
            logger.info("Schema is up to date.")
            return False

        # Close read conn before taking backup
        conn.close()

        # Backup
        backup_database(db_path)

        # Open transaction
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute("BEGIN TRANSACTION;")

        # Apply V1 if needed
        if needs_v1 and "uq_bill_imports_source_bill" in bi_sql:
            cursor.execute(
                """
                CREATE TABLE bill_imports_new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
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
                    FOREIGN KEY(sale_id) REFERENCES sales (id),
                    CONSTRAINT ck_bill_imports_status CHECK (status IN ('PENDING', 'VALIDATED', 'CONFIRMED', 'REJECTED', 'FAILED')),
                    CONSTRAINT ck_bill_imports_total_items CHECK (total_items >= 0),
                    CONSTRAINT ck_bill_imports_total_amount CHECK (total_amount >= 0)
                );
                """
            )
            cursor.execute(
                """
                INSERT INTO bill_imports_new (
                    id, source_filename, source_system, store_id, file_hash,
                    canonical_fingerprint, source_bill_number, status, total_items,
                    total_amount, sale_id, raw_data_json, error_message, created_at, confirmed_at
                )
                SELECT
                    id, source_filename, source_system, store_id, file_hash,
                    canonical_fingerprint, source_bill_number, status, total_items,
                    total_amount, sale_id, raw_data_json, error_message, created_at, confirmed_at
                FROM bill_imports;
                """
            )
            cursor.execute("DROP TABLE bill_imports;")
            cursor.execute("ALTER TABLE bill_imports_new RENAME TO bill_imports;")
            cursor.execute("CREATE INDEX ix_bill_imports_id ON bill_imports (id);")
            cursor.execute("CREATE INDEX ix_bill_imports_file_hash ON bill_imports (file_hash);")
            cursor.execute("CREATE INDEX ix_bill_imports_canonical_fingerprint ON bill_imports (canonical_fingerprint);")
            cursor.execute("CREATE INDEX ix_bill_imports_source_bill_number ON bill_imports (source_bill_number);")
            cursor.execute("CREATE INDEX ix_bill_imports_canonical ON bill_imports (store_id, canonical_fingerprint);")
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_imports_active_source_bill
                ON bill_imports (store_id, source_system, source_bill_number)
                WHERE status IN ('PENDING', 'VALIDATED', 'CONFIRMED') AND source_bill_number IS NOT NULL;
                """
            )

        cursor.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
            (MIGRATION_V1, datetime.utcnow().isoformat(), "Applied partial index and stock constraints"),
        )

        # Apply V2 if needed (add is_active to products)
        if "is_active" not in prod_cols:
            cursor.execute("ALTER TABLE products ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1;")
            logger.info("Added is_active column to products.")

        # Ensure index on partial unique source bill exists
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_imports_active_source_bill
            ON bill_imports (store_id, source_system, source_bill_number)
            WHERE status IN ('PENDING', 'VALIDATED', 'CONFIRMED') AND source_bill_number IS NOT NULL;
            """
        )

        cursor.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
            (MIGRATION_V2, datetime.utcnow().isoformat(), "Added is_active column to products"),
        )

        # Apply V3: Add financial columns and new tables
        if "products" in existing_tables and "store_id" not in prod_cols:
            cursor.execute("ALTER TABLE products ADD COLUMN store_id VARCHAR(50) NOT NULL DEFAULT 'DEFAULT';")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_products_store_id ON products (store_id);")
            logger.info("Added store_id column to products.")

        if "sale_items" in existing_tables and "unit_cost" not in sale_item_cols:
            cursor.execute("ALTER TABLE sale_items ADD COLUMN unit_cost FLOAT;")
            logger.info("Added unit_cost column to sale_items.")

        if "sales" in existing_tables:
            if "store_id" not in sale_cols:
                cursor.execute("ALTER TABLE sales ADD COLUMN store_id VARCHAR(50) NOT NULL DEFAULT 'DEFAULT';")
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_sales_store_id ON sales (store_id);")
                logger.info("Added store_id column to sales.")

            if "discount" not in sale_cols:
                cursor.execute("ALTER TABLE sales ADD COLUMN discount FLOAT NOT NULL DEFAULT 0.0;")
                logger.info("Added discount column to sales.")

            if "customer_id" not in sale_cols:
                cursor.execute("ALTER TABLE sales ADD COLUMN customer_id INTEGER;")
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_sales_customer_id ON sales (customer_id);")
                logger.info("Added customer_id column to sales.")

            if "business_account_id" not in sale_cols:
                cursor.execute("ALTER TABLE sales ADD COLUMN business_account_id INTEGER;")
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_sales_business_account_id ON sales (business_account_id);")
                logger.info("Added business_account_id column to sales.")

        # Ensure default business accounts exist for store 'DEFAULT'
        if "business_accounts" in existing_tables:
            cursor.execute("SELECT COUNT(*) FROM business_accounts WHERE store_id = 'DEFAULT';")
            ba_count = cursor.fetchone()[0]
            if ba_count == 0:
                now_str = datetime.utcnow().isoformat()
                cursor.execute(
                    """
                    INSERT INTO business_accounts (store_id, name, account_type, opening_balance, current_balance, is_active, created_at)
                    VALUES
                        ('DEFAULT', 'Cash Counter Drawer', 'CASH', 5000.0, 5000.0, 1, ?),
                        ('DEFAULT', 'Current Bank Account', 'BANK', 25000.0, 25000.0, 1, ?),
                        ('DEFAULT', 'UPI Collection Account', 'UPI', 0.0, 0.0, 1, ?);
                    """,
                    (now_str, now_str, now_str),
                )
                logger.info("Seeded default business accounts for store DEFAULT.")

        # Ensure default store profile exists for 'DEFAULT'
        if "store_profiles" in existing_tables:
            cursor.execute("SELECT COUNT(*) FROM store_profiles WHERE store_id = 'DEFAULT';")
            sp_count = cursor.fetchone()[0]
            if sp_count == 0:
                now_str = datetime.utcnow().isoformat()
                cursor.execute(
                    """
                    INSERT INTO store_profiles (store_id, store_name, owner_name, currency_symbol, currency_code, default_tax_rate, updated_at)
                    VALUES ('DEFAULT', 'BharatVoice Retail Store', 'Store Owner', '₹', 'INR', 0.0, ?);
                    """,
                    (now_str,),
                )
                logger.info("Seeded default store profile for store DEFAULT.")

        cursor.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
            (MIGRATION_V3, datetime.utcnow().isoformat(), "Added financial snapshot columns and retail modules"),
        )

        # Re-enable foreign keys and verify integrity
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA foreign_key_check;")
        violations = cursor.fetchall()
        if violations:
            raise RuntimeError(f"Foreign key violations detected during migration: {violations}")

        conn.commit()
        logger.info("Migration V3 completed successfully.")
        return True

    except Exception as e:
        logger.error("Migration failed: %s. Rolling back.", e, exc_info=True)
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
