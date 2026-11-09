import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .models import Product, Sale, SaleItem

def seed_demo_data(db: Session):
    # Conditioned on BHARATVOICE_SEED_DEMO=true.
    # New store databases initialize with an empty catalogue unless explicitly requested.
    if os.environ.get("BHARATVOICE_SEED_DEMO", "").strip().lower() not in ("true", "1"):
        return

    if db.query(Product).count() == 0:
        products = [
            Product(sku="BV-1001", name="Tata Salt 1 kg", category="Staples", unit="pack", price=28, cost_price=22, stock_qty=42, reorder_level=10),
            Product(sku="BV-1002", name="Aashirvaad Atta 5 kg", category="Staples", unit="bag", price=285, cost_price=245, stock_qty=8, reorder_level=10),
            Product(sku="BV-1003", name="Amul Gold Milk 500 ml", category="Dairy", unit="packet", price=34, cost_price=30, stock_qty=24, reorder_level=12),
            Product(sku="BV-1004", name="Parle-G Biscuits", category="Snacks", unit="packet", price=10, cost_price=8, stock_qty=6, reorder_level=15),
            Product(sku="BV-1005", name="Fortune Sunflower Oil 1 L", category="Cooking Oil", unit="bottle", price=145, cost_price=128, stock_qty=18, reorder_level=8),
            Product(sku="BV-1006", name="Toor Dal 1 kg", category="Pulses", unit="pack", price=165, cost_price=142, stock_qty=4, reorder_level=8),
            Product(sku="BV-1007", name="Colgate Strong Teeth 150 g", category="Personal Care", unit="tube", price=98, cost_price=82, stock_qty=16, reorder_level=5),
            Product(sku="BV-1008", name="Coca-Cola 750 ml", category="Beverages", unit="bottle", price=40, cost_price=31, stock_qty=31, reorder_level=10),
        ]
        db.add_all(products)
        db.commit()

    if db.query(Sale).count() == 0:
        products_by_sku = {p.sku: p for p in db.query(Product).all()}
        examples = [
            (0, "BV-5001", "Walk-in Customer", "UPI", [("BV-1001", 2), ("BV-1004", 3), ("BV-1003", 2)]),
            (1, "BV-5002", "Walk-in Customer", "Cash", [("BV-1002", 1), ("BV-1005", 1)]),
            (2, "BV-5003", "Ravi Kumar", "UPI", [("BV-1003", 4), ("BV-1008", 2)]),
            (3, "BV-5004", "Walk-in Customer", "Card", [("BV-1006", 1), ("BV-1001", 1)]),
            (4, "BV-5005", "Anitha", "UPI", [("BV-1007", 1), ("BV-1004", 5)]),
            (5, "BV-5006", "Walk-in Customer", "Cash", [("BV-1008", 3), ("BV-1003", 2)]),
            (6, "BV-5007", "Walk-in Customer", "UPI", [("BV-1001", 1), ("BV-1005", 1)]),
        ]
        now = datetime.utcnow()
        for days_ago, bill, customer, method, lines in examples:
            sale = Sale(
                bill_number=bill,
                customer_name=customer,
                payment_method=method,
                status="Completed",
                created_at=now - timedelta(days=days_ago),
                total=sum(products_by_sku[sku].price * qty for sku, qty in lines),
            )
            db.add(sale)
            db.flush()
            for sku, qty in lines:
                product = products_by_sku[sku]
                db.add(SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    product_name=product.name,
                    quantity=qty,
                    unit_price=product.price,
                    line_total=round(product.price * qty, 2),
                ))
        db.commit()
