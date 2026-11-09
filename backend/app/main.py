from datetime import datetime, timedelta, date
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from .database import Base, engine, get_db
from .models import (
    Product,
    Sale,
    SaleItem,
    BillImport,
    StockMovement,
    Expense,
    BusinessAccount,
    AccountTransaction,
    Supplier,
    PurchaseOrder,
    PurchaseItem,
    Customer,
    CustomerPayment,
    StoreProfile,
)
from .schemas import (
    ProductOut,
    ProductCreate,
    ProductUpdate,
    StockAdjustRequest,
    StockMovementOut,
    SaleCreate,
    SaleOut,
    SaleDetailOut,
    SaleItemOut,
    FinanceMetricsOut,
    FinanceAnalyticsOut,
    InventoryValuationOut,
    ExpenseCreate,
    ExpenseUpdate,
    ExpenseReverse,
    ExpenseOut,
    BusinessAccountCreate,
    BusinessAccountUpdate,
    BusinessAccountOut,
    AccountTransactionOut,
    AccountTransferRequest,
    CashClosingSummaryOut,
    SupplierCreate,
    SupplierUpdate,
    SupplierOut,
    PurchaseOrderCreate,
    PurchaseOrderOut,
    SupplierPaymentCreate,
    SupplierPaymentOut,
    SaleRefundCreate,
    CustomerCreate,
    CustomerUpdate,
    CustomerOut,
    CustomerPaymentCreate,
    CustomerPaymentOut,
    StoreProfileUpdate,
    StoreProfileOut,
)
from .seed import seed_demo_data
from .bill_import_service import (
    register_bill_import_preview,
    get_bill_import_preview,
    confirm_bill_import,
    reject_bill_import_preview,
    list_bill_imports,
    BillImportPreviewResponse,
    BillImportConfirmationResult,
)
from .auth import get_authenticated_store_context, resolve_effective_store
from .gnani_service import transcribe_speech, synthesize_speech, is_gnani_configured
from .voice_service import process_voice_intent
from .finance_service import (
    calculate_financial_metrics,
    calculate_financial_analytics,
    calculate_inventory_valuation,
)
from .accounts_service import (
    record_account_transaction,
    execute_account_transfer,
    compute_cash_closing_summary,
    get_or_create_default_account,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="BharatVoice AI API",
    description="Accessible retail operations, financial intelligence, and voice platform for Indian retailers.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    import os
    from .migrations import run_migrations
    db_file = os.environ.get("DATABASE_PATH", "./bharatvoice.db")
    if os.path.exists(db_file):
        run_migrations(db_file)

    from .database import SessionLocal
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


@app.get("/")
def root():
    return {"name": "BharatVoice AI API", "status": "running", "version": "0.2.0", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "bharatvoice-api"}


@app.get("/api/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    today = date.today()
    start_today = datetime.combine(today, datetime.min.time())
    start_tomorrow = start_today + timedelta(days=1)
    start_week = start_today - timedelta(days=6)

    today_sales = db.query(func.coalesce(func.sum(Sale.total), 0)).filter(
        Sale.created_at >= start_today, Sale.created_at < start_tomorrow,
        Sale.status == "Completed"
    ).scalar() or 0
    today_bills = db.query(Sale).filter(
        Sale.created_at >= start_today, Sale.created_at < start_tomorrow,
        Sale.status == "Completed"
    ).count()
    total_products = db.query(Product).count()
    low_stock = db.query(Product).filter(Product.stock_qty <= Product.reorder_level).count()
    inventory_value = db.query(func.coalesce(func.sum(Product.stock_qty * Product.cost_price), 0)).scalar() or 0
    week_rows = db.query(
        func.date(Sale.created_at).label("day"),
        func.coalesce(func.sum(Sale.total), 0).label("total")
    ).filter(Sale.created_at >= start_week, Sale.status == "Completed").group_by(
        func.date(Sale.created_at)
    ).all()
    week_map = {str(row.day): float(row.total) for row in week_rows}
    sales_trend = []
    for offset in range(7):
        day = start_week + timedelta(days=offset)
        sales_trend.append({
            "date": day.strftime("%a"),
            "full_date": day.strftime("%Y-%m-%d"),
            "sales": round(week_map.get(day.strftime("%Y-%m-%d"), 0), 2)
        })

    return {
        "today_sales": round(float(today_sales), 2),
        "today_bills": today_bills,
        "total_products": total_products,
        "low_stock": low_stock,
        "inventory_value": round(float(inventory_value), 2),
        "sales_trend": sales_trend,
    }


# ==============================================================================
# PRODUCTS & INVENTORY
# ==============================================================================

@app.get("/api/products", response_model=List[ProductOut])
def list_products(search: str = "", low_stock_only: bool = False, active_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(Product)
    if active_only:
        query = query.filter(Product.is_active == 1)
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.filter((Product.name.ilike(term)) | (Product.sku.ilike(term)) | (Product.category.ilike(term)))
    if low_stock_only:
        query = query.filter(Product.stock_qty <= Product.reorder_level)
    return query.order_by(Product.name.asc()).all()


@app.get("/api/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")
    return product


@app.post("/api/products", response_model=ProductOut, status_code=201)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    existing = db.query(Product).filter(Product.sku == payload.sku).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Product with SKU '{payload.sku}' already exists.")
    product = Product(
        sku=payload.sku,
        name=payload.name,
        category=payload.category,
        unit=payload.unit,
        price=payload.price,
        cost_price=payload.cost_price,
        stock_qty=payload.stock_qty,
        reorder_level=payload.reorder_level,
        is_active=1 if payload.is_active else 0,
        store_id="DEFAULT",
        created_at=datetime.utcnow(),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@app.put("/api/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")
    update_data = payload.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if field == "is_active":
            setattr(product, field, 1 if val else 0)
        else:
            setattr(product, field, val)
    db.commit()
    db.refresh(product)
    return product


@app.delete("/api/products/{product_id}", response_model=ProductOut)
def deactivate_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")
    product.is_active = 0
    db.commit()
    db.refresh(product)
    return product


@app.post("/api/products/{product_id}/stock-adjust", response_model=ProductOut)
def adjust_product_stock(product_id: int, payload: StockAdjustRequest, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product #{product_id} not found.")
    if payload.quantity_change == 0:
        raise HTTPException(status_code=400, detail="Stock adjustment quantity cannot be zero.")

    new_stock = round(product.stock_qty + payload.quantity_change, 4)
    if new_stock < 0:
        raise HTTPException(
            status_code=400,
            detail=f"Adjustment rejected: would cause negative stock ({new_stock} {product.unit}). Available: {product.stock_qty}."
        )

    product.stock_qty = new_stock
    movement = StockMovement(
        product_id=product.id,
        quantity_change=payload.quantity_change,
        balance_after=new_stock,
        reason=payload.reason or "MANUAL_ADJUSTMENT",
        notes=payload.notes,
        created_at=datetime.utcnow(),
    )
    db.add(movement)
    db.commit()
    db.refresh(product)
    return product


@app.get("/api/inventory/movements", response_model=List[StockMovementOut])
def list_stock_movements(
    product_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    q = db.query(StockMovement)
    if product_id:
        q = q.filter(StockMovement.product_id == product_id)
    movements = q.order_by(StockMovement.created_at.desc()).limit(limit).all()
    return [{
        "id": m.id,
        "product_id": m.product_id,
        "quantity_change": m.quantity_change,
        "balance_after": m.balance_after,
        "reason": m.reason,
        "bill_import_id": m.bill_import_id,
        "sale_id": m.sale_id,
        "notes": m.notes,
        "created_at": m.created_at.isoformat(),
    } for m in movements]


# ==============================================================================
# SALES & POS BILLING
# ==============================================================================

@app.get("/api/sales/recent", response_model=List[SaleOut])
def recent_sales(limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    sales = db.query(Sale).order_by(Sale.created_at.desc()).limit(limit).all()
    return [{
        "id": sale.id,
        "bill_number": sale.bill_number,
        "customer_name": sale.customer_name,
        "payment_method": sale.payment_method,
        "status": sale.status,
        "total": sale.total,
        "discount": getattr(sale, "discount", 0.0),
        "created_at": sale.created_at.isoformat(),
    } for sale in sales]


@app.post("/api/sales", response_model=SaleDetailOut, status_code=201)
def create_sale(
    payload: SaleCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    if not payload.items:
        raise HTTPException(status_code=400, detail="Sale must contain at least one line item.")

    bill_no = payload.bill_number
    if not bill_no:
        timestamp_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        bill_no = f"BV-SALE-{timestamp_str}"

    if db.query(Sale).filter(Sale.bill_number == bill_no, Sale.store_id == effective_store_id).first():
        raise HTTPException(status_code=409, detail=f"A sale with bill number '{bill_no}' already exists.")

    skus = [it.sku for it in payload.items]
    prods = {p.sku: p for p in db.query(Product).filter(Product.sku.in_(skus), Product.is_active == 1).all()}
    missing = set(skus) - set(prods.keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"Active products not found for SKUs: {sorted(list(missing))}")

    total_sale_amount = 0.0
    sale_items_to_create = []

    for it in payload.items:
        prod = prods[it.sku]
        if prod.stock_qty < it.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for {prod.name} ({prod.sku}). Required: {it.quantity}, Available: {prod.stock_qty}."
            )
        price = it.unit_price if it.unit_price is not None else prod.price
        line_tot = round(it.quantity * price, 2)
        total_sale_amount += line_tot
        sale_items_to_create.append({
            "product": prod,
            "quantity": it.quantity,
            "unit_price": price,
            "unit_cost": float(prod.cost_price),  # Snapshot cost price at sale time
            "line_total": line_tot,
        })

    discount_amt = round(payload.discount, 2)
    final_total = max(0.0, round(total_sale_amount - discount_amt, 2))

    is_credit = (payload.payment_method or "").upper() in ("CREDIT", "KHATA", "UDHAAR")
    target_account_id = payload.business_account_id
    if not is_credit and not target_account_id:
        acc = get_or_create_default_account(
            db,
            store_id=effective_store_id,
            account_type="CASH" if (payload.payment_method or "").upper() == "CASH" else "BANK"
        )
        target_account_id = acc.id

    sale = Sale(
        bill_number=bill_no,
        customer_name=payload.customer_name or "Walk-in Customer",
        customer_id=payload.customer_id,
        payment_method=payload.payment_method or "UPI",
        business_account_id=target_account_id if not is_credit else None,
        status="Completed",
        total=final_total,
        discount=discount_amt,
        store_id=effective_store_id,
        created_at=datetime.utcnow(),
    )
    db.add(sale)
    db.flush()

    response_items = []
    for it in sale_items_to_create:
        prod = it["product"]
        qty = it["quantity"]
        new_stock = round(prod.stock_qty - qty, 4)
        prod.stock_qty = new_stock

        s_item = SaleItem(
            sale_id=sale.id,
            product_id=prod.id,
            product_name=prod.name,
            quantity=qty,
            unit_price=it["unit_price"],
            unit_cost=it["unit_cost"],
            line_total=it["line_total"],
        )
        db.add(s_item)
        db.flush()

        movement = StockMovement(
            product_id=prod.id,
            quantity_change=-qty,
            balance_after=new_stock,
            reason="DIRECT_SALE",
            sale_id=sale.id,
            notes=f"Sold via bill {bill_no}",
            created_at=datetime.utcnow(),
        )
        db.add(movement)

        response_items.append({
            "id": s_item.id,
            "product_id": prod.id,
            "product_name": prod.name,
            "quantity": qty,
            "unit_price": it["unit_price"],
            "unit_cost": it["unit_cost"],
            "line_total": it["line_total"],
        })

    # Account ledger update
    if not is_credit and target_account_id:
        record_account_transaction(
            db=db,
            account_id=target_account_id,
            transaction_type="INCOME",
            amount=final_total,
            reference_type="SALE",
            reference_id=str(sale.id),
            description=f"Sales receipt {bill_no}",
            store_id=effective_store_id,
        )

    # Customer update
    if payload.customer_id:
        cust = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.store_id == effective_store_id).first()
        if cust:
            cust.total_spent = round(cust.total_spent + final_total, 2)
            if is_credit:
                cust.outstanding_balance = round(cust.outstanding_balance + final_total, 2)

    db.commit()
    db.refresh(sale)

    return {
        "id": sale.id,
        "bill_number": sale.bill_number,
        "customer_name": sale.customer_name,
        "customer_id": sale.customer_id,
        "payment_method": sale.payment_method,
        "business_account_id": sale.business_account_id,
        "status": sale.status,
        "total": sale.total,
        "discount": sale.discount,
        "created_at": sale.created_at.isoformat(),
        "items": response_items,
    }


@app.get("/api/sales/{sale_id}", response_model=SaleDetailOut)
def get_sale_detail(sale_id: int, db: Session = Depends(get_db)):
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail=f"Sale #{sale_id} not found.")
    items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    return {
        "id": sale.id,
        "bill_number": sale.bill_number,
        "customer_name": sale.customer_name,
        "customer_id": getattr(sale, "customer_id", None),
        "payment_method": sale.payment_method,
        "business_account_id": getattr(sale, "business_account_id", None),
        "status": sale.status,
        "total": sale.total,
        "discount": getattr(sale, "discount", 0.0),
        "created_at": sale.created_at.isoformat(),
        "items": [{
            "id": it.id,
            "product_id": it.product_id,
            "product_name": it.product_name,
            "quantity": it.quantity,
            "unit_price": it.unit_price,
            "unit_cost": it.unit_cost,
            "line_total": it.line_total,
        } for it in items],
    }


@app.post("/api/sales/{sale_id}/refund", response_model=SaleDetailOut)
def refund_sale(
    sale_id: int,
    payload: Optional[SaleRefundCreate] = None,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    sale = db.query(Sale).filter(Sale.id == sale_id, Sale.store_id == effective_store_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail=f"Sale #{sale_id} not found.")

    if sale.status == "Refunded":
        raise HTTPException(status_code=400, detail="Sale has already been refunded.")
    if sale.status != "Completed":
        raise HTTPException(status_code=400, detail=f"Cannot refund sale in status '{sale.status}'.")

    sale.status = "Refunded"
    refund_reason = payload.reason if payload and payload.reason else "Customer refund"

    # Restore inventory
    items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    for item in items:
        prod = db.query(Product).filter(Product.id == item.product_id).first()
        if prod:
            new_stock = round(prod.stock_qty + item.quantity, 4)
            prod.stock_qty = new_stock
            movement = StockMovement(
                product_id=prod.id,
                quantity_change=item.quantity,
                balance_after=new_stock,
                reason="SALE_REFUND",
                sale_id=sale.id,
                notes=f"Refund: {refund_reason}",
                created_at=datetime.utcnow(),
            )
            db.add(movement)

    # Adjust account balance if was paid via business account
    if sale.business_account_id:
        record_account_transaction(
            db=db,
            account_id=sale.business_account_id,
            transaction_type="EXPENSE",
            amount=sale.total,
            reference_type="SALE_REFUND",
            reference_id=str(sale.id),
            description=f"Refund for sale {sale.bill_number}: {refund_reason}",
            store_id=effective_store_id,
        )

    # Adjust customer if credit sale
    if sale.customer_id:
        cust = db.query(Customer).filter(Customer.id == sale.customer_id, Customer.store_id == effective_store_id).first()
        if cust:
            cust.total_spent = max(0.0, round(cust.total_spent - sale.total, 2))
            if (sale.payment_method or "").upper() in ("CREDIT", "KHATA", "UDHAAR"):
                cust.outstanding_balance = max(0.0, round(cust.outstanding_balance - sale.total, 2))

    db.commit()
    db.refresh(sale)
    return get_sale_detail(sale_id=sale.id, db=db)


# ==============================================================================
# FINANCIAL INTELLIGENCE & PROFIT (PHASE 1)
# ==============================================================================

@app.get("/api/finance/metrics", response_model=FinanceMetricsOut)
def get_finance_metrics(
    period: str = Query("today"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    """Returns strict non-floating point financial metrics (Revenue, COGS, Gross/Net Profit, Margins)."""
    effective_store_id = resolve_effective_store(auth_store, store_id)
    return calculate_financial_metrics(
        db=db,
        store_id=effective_store_id,
        period=period,
        custom_start=start_date,
        custom_end=end_date,
    )


@app.get("/api/finance/analytics", response_model=FinanceAnalyticsOut)
def get_finance_analytics(
    period: str = Query("7d"),
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    """Returns daily/weekly trend points, product margin rankings, and expense breakdowns."""
    effective_store_id = resolve_effective_store(auth_store, store_id)
    return calculate_financial_analytics(db=db, store_id=effective_store_id, period=period)


@app.get("/api/finance/valuation", response_model=InventoryValuationOut)
def get_inventory_valuation(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    """Returns current stock valuation at cost price vs selling price."""
    effective_store_id = resolve_effective_store(auth_store, store_id)
    return calculate_inventory_valuation(db=db, store_id=effective_store_id)


# ==============================================================================
# EXPENSE MANAGEMENT (PHASE 2)
# ==============================================================================

@app.get("/api/expenses", response_model=List[ExpenseOut])
def list_expenses(
    category: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    q = db.query(Expense).filter(Expense.store_id == effective_store_id)
    if category and category.strip():
        q = q.filter(Expense.category == category.strip())
    expenses = q.order_by(Expense.expense_date.desc()).all()

    accounts = {a.id: a.name for a in db.query(BusinessAccount).filter(BusinessAccount.store_id == effective_store_id).all()}

    return [{
        "id": e.id,
        "store_id": e.store_id,
        "category": e.category,
        "amount": e.amount,
        "expense_date": e.expense_date.isoformat(),
        "payment_method": e.payment_method,
        "description": e.description,
        "vendor_name": e.vendor_name,
        "reference_number": e.reference_number,
        "receipt_url": e.receipt_url,
        "business_account_id": e.business_account_id,
        "business_account_name": accounts.get(e.business_account_id),
        "status": e.status,
        "reversed_reason": e.reversed_reason,
        "reversed_at": e.reversed_at.isoformat() if e.reversed_at else None,
        "created_at": e.created_at.isoformat(),
    } for e in expenses]


@app.post("/api/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(
    payload: ExpenseCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)

    exp_date = datetime.utcnow()
    if payload.expense_date:
        try:
            exp_date = datetime.fromisoformat(payload.expense_date.replace("Z", ""))
        except Exception:
            exp_date = datetime.utcnow()

    exp = Expense(
        store_id=effective_store_id,
        category=payload.category,
        amount=round(payload.amount, 2),
        expense_date=exp_date,
        payment_method=payload.payment_method,
        description=payload.description,
        vendor_name=payload.vendor_name,
        reference_number=payload.reference_number,
        receipt_url=payload.receipt_url,
        business_account_id=payload.business_account_id,
        status="ACTIVE",
        created_at=datetime.utcnow(),
    )
    db.add(exp)
    db.flush()

    # Link to business account if specified
    acc_name = None
    if payload.business_account_id:
        acc = db.query(BusinessAccount).filter(BusinessAccount.id == payload.business_account_id).first()
        if acc:
            acc_name = acc.name
            record_account_transaction(
                db=db,
                account_id=acc.id,
                transaction_type="EXPENSE",
                amount=payload.amount,
                reference_type="EXPENSE",
                reference_id=str(exp.id),
                description=f"{payload.category}: {payload.description}",
                store_id=effective_store_id,
            )

    db.commit()
    db.refresh(exp)

    return {
        "id": exp.id,
        "store_id": exp.store_id,
        "category": exp.category,
        "amount": exp.amount,
        "expense_date": exp.expense_date.isoformat(),
        "payment_method": exp.payment_method,
        "description": exp.description,
        "vendor_name": exp.vendor_name,
        "reference_number": exp.reference_number,
        "receipt_url": exp.receipt_url,
        "business_account_id": exp.business_account_id,
        "business_account_name": acc_name,
        "status": exp.status,
        "reversed_reason": exp.reversed_reason,
        "reversed_at": None,
        "created_at": exp.created_at.isoformat(),
    }


@app.put("/api/expenses/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    exp = db.query(Expense).filter(Expense.id == expense_id, Expense.store_id == effective_store_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Expense #{expense_id} not found.")
    if exp.status == "REVERSED":
        raise HTTPException(status_code=400, detail="Cannot edit a reversed expense.")

    update_dict = payload.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        if field == "expense_date" and val:
            try:
                setattr(exp, field, datetime.fromisoformat(val.replace("Z", "")))
            except Exception:
                pass
        else:
            setattr(exp, field, val)

    db.commit()
    db.refresh(exp)

    acc = db.query(BusinessAccount).filter(BusinessAccount.id == exp.business_account_id).first() if exp.business_account_id else None

    return {
        "id": exp.id,
        "store_id": exp.store_id,
        "category": exp.category,
        "amount": exp.amount,
        "expense_date": exp.expense_date.isoformat(),
        "payment_method": exp.payment_method,
        "description": exp.description,
        "vendor_name": exp.vendor_name,
        "reference_number": exp.reference_number,
        "receipt_url": exp.receipt_url,
        "business_account_id": exp.business_account_id,
        "business_account_name": acc.name if acc else None,
        "status": exp.status,
        "reversed_reason": exp.reversed_reason,
        "reversed_at": exp.reversed_at.isoformat() if exp.reversed_at else None,
        "created_at": exp.created_at.isoformat(),
    }


@app.post("/api/expenses/{expense_id}/reverse", response_model=ExpenseOut)
def reverse_expense(
    expense_id: int,
    payload: ExpenseReverse,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    exp = db.query(Expense).filter(Expense.id == expense_id, Expense.store_id == effective_store_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Expense #{expense_id} not found.")
    if exp.status == "REVERSED":
        raise HTTPException(status_code=400, detail="Expense has already been reversed.")

    now = datetime.utcnow()
    exp.status = "REVERSED"
    exp.reversed_reason = payload.reason
    exp.reversed_at = now

    # Reversal in ledger if attached to an account
    if exp.business_account_id:
        record_account_transaction(
            db=db,
            account_id=exp.business_account_id,
            transaction_type="REFUND",
            amount=exp.amount,
            reference_type="EXPENSE_REVERSAL",
            reference_id=str(exp.id),
            description=f"Reversal of {exp.category} expense: {payload.reason}",
            store_id=effective_store_id,
        )

    db.commit()
    db.refresh(exp)

    acc = db.query(BusinessAccount).filter(BusinessAccount.id == exp.business_account_id).first() if exp.business_account_id else None

    return {
        "id": exp.id,
        "store_id": exp.store_id,
        "category": exp.category,
        "amount": exp.amount,
        "expense_date": exp.expense_date.isoformat(),
        "payment_method": exp.payment_method,
        "description": exp.description,
        "vendor_name": exp.vendor_name,
        "reference_number": exp.reference_number,
        "receipt_url": exp.receipt_url,
        "business_account_id": exp.business_account_id,
        "business_account_name": acc.name if acc else None,
        "status": exp.status,
        "reversed_reason": exp.reversed_reason,
        "reversed_at": exp.reversed_at.isoformat() if exp.reversed_at else None,
        "created_at": exp.created_at.isoformat(),
    }


# ==============================================================================
# BUSINESS ACCOUNTS & CASHBOOK (PHASE 3)
# ==============================================================================

@app.get("/api/accounts", response_model=List[BusinessAccountOut])
def list_accounts(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    accounts = (
        db.query(BusinessAccount)
        .filter(BusinessAccount.store_id == effective_store_id)
        .order_by(BusinessAccount.id.asc())
        .all()
    )
    if not accounts:
        # Auto-seed standard accounts if none exist
        get_or_create_default_account(db, effective_store_id, "CASH")
        get_or_create_default_account(db, effective_store_id, "BANK")
        get_or_create_default_account(db, effective_store_id, "UPI")
        db.commit()
        accounts = db.query(BusinessAccount).filter(BusinessAccount.store_id == effective_store_id).all()

    return [{
        "id": a.id,
        "store_id": a.store_id,
        "name": a.name,
        "account_type": a.account_type,
        "opening_balance": a.opening_balance,
        "current_balance": a.current_balance,
        "is_active": bool(a.is_active),
        "created_at": a.created_at.isoformat(),
    } for a in accounts]


@app.post("/api/accounts", response_model=BusinessAccountOut, status_code=201)
def create_account(
    payload: BusinessAccountCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    acc = BusinessAccount(
        store_id=effective_store_id,
        name=payload.name,
        account_type=payload.account_type.upper(),
        opening_balance=round(payload.opening_balance, 2),
        current_balance=round(payload.opening_balance, 2),
        is_active=1,
        created_at=datetime.utcnow(),
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return {
        "id": acc.id,
        "store_id": acc.store_id,
        "name": acc.name,
        "account_type": acc.account_type,
        "opening_balance": acc.opening_balance,
        "current_balance": acc.current_balance,
        "is_active": bool(acc.is_active),
        "created_at": acc.created_at.isoformat(),
    }


@app.get("/api/accounts/{account_id}/transactions", response_model=List[AccountTransactionOut])
def get_account_transactions(
    account_id: int,
    limit: int = Query(50, ge=1, le=200),
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    acc = db.query(BusinessAccount).filter(BusinessAccount.id == account_id, BusinessAccount.store_id == effective_store_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail=f"Business account #{account_id} not found.")

    txs = (
        db.query(AccountTransaction)
        .filter(AccountTransaction.account_id == account_id)
        .order_by(AccountTransaction.created_at.desc())
        .limit(limit)
        .all()
    )

    all_accs = {a.id: a.name for a in db.query(BusinessAccount).filter(BusinessAccount.store_id == effective_store_id).all()}

    return [{
        "id": t.id,
        "store_id": t.store_id,
        "account_id": t.account_id,
        "account_name": acc.name,
        "transaction_type": t.transaction_type,
        "amount": t.amount,
        "running_balance": t.running_balance,
        "reference_type": t.reference_type,
        "reference_id": t.reference_id,
        "description": t.description,
        "counterpart_account_id": t.counterpart_account_id,
        "counterpart_account_name": all_accs.get(t.counterpart_account_id),
        "is_reversed": bool(t.is_reversed),
        "created_at": t.created_at.isoformat(),
    } for t in txs]


@app.post("/api/accounts/transfer")
def transfer_between_accounts(
    payload: AccountTransferRequest,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    return execute_account_transfer(
        db=db,
        from_account_id=payload.from_account_id,
        to_account_id=payload.to_account_id,
        amount=payload.amount,
        notes=payload.notes,
        store_id=effective_store_id,
    )


@app.get("/api/accounts/cash-closing", response_model=CashClosingSummaryOut)
def get_cash_closing(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    return compute_cash_closing_summary(db=db, store_id=effective_store_id)


# ==============================================================================
# SUPPLIERS & PURCHASES (PHASE 4)
# ==============================================================================

@app.get("/api/suppliers", response_model=List[SupplierOut])
def list_suppliers(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    suppliers = db.query(Supplier).filter(Supplier.store_id == effective_store_id).order_by(Supplier.name.asc()).all()
    return [{
        "id": s.id,
        "store_id": s.store_id,
        "name": s.name,
        "contact_person": s.contact_person,
        "phone": s.phone,
        "email": s.email,
        "address": s.address,
        "gstin": s.gstin,
        "outstanding_balance": s.outstanding_balance,
        "is_active": bool(s.is_active),
        "created_at": s.created_at.isoformat(),
    } for s in suppliers]


@app.post("/api/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(
    payload: SupplierCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    s = Supplier(
        store_id=effective_store_id,
        name=payload.name,
        contact_person=payload.contact_person,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        gstin=payload.gstin,
        outstanding_balance=0.0,
        is_active=1,
        created_at=datetime.utcnow(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return {
        "id": s.id,
        "store_id": s.store_id,
        "name": s.name,
        "contact_person": s.contact_person,
        "phone": s.phone,
        "email": s.email,
        "address": s.address,
        "gstin": s.gstin,
        "outstanding_balance": s.outstanding_balance,
        "is_active": bool(s.is_active),
        "created_at": s.created_at.isoformat(),
    }


@app.put("/api/suppliers/{supplier_id}", response_model=SupplierOut)
def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    s = db.query(Supplier).filter(Supplier.id == supplier_id, Supplier.store_id == effective_store_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Supplier #{supplier_id} not found.")

    for k, v in payload.model_dump(exclude_unset=True).items():
        if k == "is_active":
            setattr(s, k, 1 if v else 0)
        else:
            setattr(s, k, v)

    db.commit()
    db.refresh(s)
    return {
        "id": s.id,
        "store_id": s.store_id,
        "name": s.name,
        "contact_person": s.contact_person,
        "phone": s.phone,
        "email": s.email,
        "address": s.address,
        "gstin": s.gstin,
        "outstanding_balance": s.outstanding_balance,
        "is_active": bool(s.is_active),
        "created_at": s.created_at.isoformat(),
    }


@app.post("/api/suppliers/{supplier_id}/payment", response_model=SupplierPaymentOut, status_code=201)
def record_supplier_payment(
    supplier_id: int,
    payload: SupplierPaymentCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id, Supplier.store_id == effective_store_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail=f"Supplier #{supplier_id} not found.")

    amt = round(payload.amount, 2)
    supplier.outstanding_balance = round(supplier.outstanding_balance - amt, 2)

    # If business account specified, record ledger expense
    if payload.business_account_id:
        record_account_transaction(
            db=db,
            account_id=payload.business_account_id,
            transaction_type="EXPENSE",
            amount=amt,
            reference_type="SUPPLIER_PAYMENT",
            reference_id=str(supplier.id),
            description=f"Payment settlement to supplier {supplier.name}. {payload.notes or ''}".strip(),
            store_id=effective_store_id,
        )

    db.commit()
    db.refresh(supplier)

    return {
        "supplier_id": supplier.id,
        "name": supplier.name,
        "amount_paid": amt,
        "outstanding_balance": supplier.outstanding_balance,
        "payment_method": payload.payment_method,
        "business_account_id": payload.business_account_id,
        "notes": payload.notes,
    }


@app.get("/api/purchases", response_model=List[PurchaseOrderOut])
def list_purchases(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.store_id == effective_store_id)
        .order_by(PurchaseOrder.purchase_date.desc())
        .all()
    )
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.store_id == effective_store_id).all()}

    return [{
        "id": p.id,
        "store_id": p.store_id,
        "supplier_id": p.supplier_id,
        "supplier_name": suppliers.get(p.supplier_id),
        "invoice_number": p.invoice_number,
        "purchase_date": p.purchase_date.isoformat(),
        "status": p.status,
        "total_amount": p.total_amount,
        "paid_amount": p.paid_amount,
        "payment_status": p.payment_status,
        "payment_method": p.payment_method,
        "notes": p.notes,
        "created_at": p.created_at.isoformat(),
        "items": [{
            "id": it.id,
            "product_id": it.product_id,
            "product_name": it.product_name,
            "quantity": it.quantity,
            "unit_cost": it.unit_cost,
            "line_total": it.line_total,
        } for it in p.items],
    } for p in pos]


@app.post("/api/purchases", response_model=PurchaseOrderOut, status_code=201)
def create_purchase_order(
    payload: PurchaseOrderCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    supplier = db.query(Supplier).filter(Supplier.id == payload.supplier_id, Supplier.store_id == effective_store_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail=f"Supplier #{payload.supplier_id} not found.")

    # Unique check for invoice
    existing_po = db.query(PurchaseOrder).filter(
        PurchaseOrder.store_id == effective_store_id,
        PurchaseOrder.supplier_id == supplier.id,
        PurchaseOrder.invoice_number == payload.invoice_number,
    ).first()
    if existing_po:
        raise HTTPException(status_code=409, detail=f"Purchase order invoice '{payload.invoice_number}' already exists for supplier {supplier.name}.")

    p_date = datetime.utcnow()
    if payload.purchase_date:
        try:
            p_date = datetime.fromisoformat(payload.purchase_date.replace("Z", ""))
        except Exception:
            p_date = datetime.utcnow()

    # Calculate items total and verify products
    skus = [it.sku for it in payload.items]
    prods = {p.sku: p for p in db.query(Product).filter(Product.sku.in_(skus)).all()}
    missing = set(skus) - set(prods.keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"Products not found for SKUs: {sorted(list(missing))}")

    total_amount = sum([round(it.quantity * it.unit_cost, 2) for it in payload.items])
    paid_amt = min(total_amount, round(payload.paid_amount, 2))

    if paid_amt >= total_amount:
        pay_status = "PAID"
    elif paid_amt > 0:
        pay_status = "PARTIAL"
    else:
        pay_status = "UNPAID"

    po = PurchaseOrder(
        store_id=effective_store_id,
        supplier_id=supplier.id,
        invoice_number=payload.invoice_number,
        purchase_date=p_date,
        status="CONFIRMED",
        total_amount=total_amount,
        paid_amount=paid_amt,
        payment_status=pay_status,
        payment_method=payload.payment_method,
        business_account_id=payload.business_account_id,
        notes=payload.notes,
        created_at=datetime.utcnow(),
    )
    db.add(po)
    db.flush()

    response_items = []
    for it in payload.items:
        prod = prods[it.sku]
        line_tot = round(it.quantity * it.unit_cost, 2)
        p_item = PurchaseItem(
            purchase_id=po.id,
            product_id=prod.id,
            product_name=prod.name,
            quantity=it.quantity,
            unit_cost=it.unit_cost,
            line_total=line_tot,
        )
        db.add(p_item)
        db.flush()

        # Atomic stock increase and cost update
        new_stock = round(prod.stock_qty + it.quantity, 4)
        prod.stock_qty = new_stock
        if it.unit_cost > 0:
            prod.cost_price = it.unit_cost

        # Stock movement record
        movement = StockMovement(
            product_id=prod.id,
            quantity_change=it.quantity,
            balance_after=new_stock,
            reason="PURCHASE",
            notes=f"Purchased via invoice {payload.invoice_number}",
            created_at=datetime.utcnow(),
        )
        db.add(movement)

        response_items.append({
            "id": p_item.id,
            "product_id": prod.id,
            "product_name": prod.name,
            "quantity": it.quantity,
            "unit_cost": it.unit_cost,
            "line_total": line_tot,
        })

    # Update supplier balance
    unpaid_portion = round(total_amount - paid_amt, 2)
    supplier.outstanding_balance = round(supplier.outstanding_balance + unpaid_portion, 2)

    # If payment was made from business account
    if paid_amt > 0 and payload.business_account_id:
        record_account_transaction(
            db=db,
            account_id=payload.business_account_id,
            transaction_type="EXPENSE",
            amount=paid_amt,
            reference_type="PURCHASE",
            reference_id=str(po.id),
            description=f"Purchase invoice {po.invoice_number} from {supplier.name}",
            store_id=effective_store_id,
        )

    db.commit()
    db.refresh(po)

    return {
        "id": po.id,
        "store_id": po.store_id,
        "supplier_id": po.supplier_id,
        "supplier_name": supplier.name,
        "invoice_number": po.invoice_number,
        "purchase_date": po.purchase_date.isoformat(),
        "status": po.status,
        "total_amount": po.total_amount,
        "paid_amount": po.paid_amount,
        "payment_status": po.payment_status,
        "payment_method": po.payment_method,
        "notes": po.notes,
        "created_at": po.created_at.isoformat(),
        "items": response_items,
    }


# ==============================================================================
# CUSTOMER ACCOUNT MANAGEMENT (PHASE 5)
# ==============================================================================

@app.get("/api/customers", response_model=List[CustomerOut])
def list_customers(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    customers = db.query(Customer).filter(Customer.store_id == effective_store_id).order_by(Customer.name.asc()).all()
    return [{
        "id": c.id,
        "store_id": c.store_id,
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "address": c.address,
        "outstanding_balance": c.outstanding_balance,
        "total_spent": c.total_spent,
        "created_at": c.created_at.isoformat(),
    } for c in customers]


@app.post("/api/customers", response_model=CustomerOut, status_code=201)
def create_customer(
    payload: CustomerCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    c = Customer(
        store_id=effective_store_id,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        outstanding_balance=0.0,
        total_spent=0.0,
        created_at=datetime.utcnow(),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {
        "id": c.id,
        "store_id": c.store_id,
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "address": c.address,
        "outstanding_balance": c.outstanding_balance,
        "total_spent": c.total_spent,
        "created_at": c.created_at.isoformat(),
    }


@app.put("/api/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    c = db.query(Customer).filter(Customer.id == customer_id, Customer.store_id == effective_store_id).first()
    if not c:
        raise HTTPException(status_code=404, detail=f"Customer #{customer_id} not found.")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)

    db.commit()
    db.refresh(c)
    return {
        "id": c.id,
        "store_id": c.store_id,
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "address": c.address,
        "outstanding_balance": c.outstanding_balance,
        "total_spent": c.total_spent,
        "created_at": c.created_at.isoformat(),
    }


@app.post("/api/customers/{customer_id}/payment", response_model=CustomerPaymentOut, status_code=201)
def record_customer_payment(
    customer_id: int,
    payload: CustomerPaymentCreate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    c = db.query(Customer).filter(Customer.id == customer_id, Customer.store_id == effective_store_id).first()
    if not c:
        raise HTTPException(status_code=404, detail=f"Customer #{customer_id} not found.")

    amt = round(payload.amount, 2)
    c.outstanding_balance = round(c.outstanding_balance - amt, 2)

    pay = CustomerPayment(
        store_id=effective_store_id,
        customer_id=c.id,
        amount=amt,
        payment_method=payload.payment_method,
        business_account_id=payload.business_account_id,
        notes=payload.notes,
        created_at=datetime.utcnow(),
    )
    db.add(pay)

    if payload.business_account_id:
        record_account_transaction(
            db=db,
            account_id=payload.business_account_id,
            transaction_type="INCOME",
            amount=amt,
            reference_type="CUSTOMER_PAYMENT",
            reference_id=str(pay.id),
            description=f"Payment from customer {c.name}",
            store_id=effective_store_id,
        )

    db.commit()
    db.refresh(pay)

    return {
        "id": pay.id,
        "store_id": pay.store_id,
        "customer_id": pay.customer_id,
        "amount": pay.amount,
        "payment_method": pay.payment_method,
        "notes": pay.notes,
        "created_at": pay.created_at.isoformat(),
    }


# ==============================================================================
# STORE PROFILE & SETTINGS (PHASE 7)
# ==============================================================================

@app.get("/api/settings/profile", response_model=StoreProfileOut)
def get_store_profile(
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    sp = db.query(StoreProfile).filter(StoreProfile.store_id == effective_store_id).first()
    if not sp:
        sp = StoreProfile(
            store_id=effective_store_id,
            store_name="BharatVoice Retail Store",
            owner_name="Store Owner",
            currency_symbol="₹",
            currency_code="INR",
            default_tax_rate=0.0,
            invoice_footer="Thank you for shopping with us!",
            updated_at=datetime.utcnow(),
        )
        db.add(sp)
        db.commit()
        db.refresh(sp)

    return {
        "id": sp.id,
        "store_id": sp.store_id,
        "store_name": sp.store_name,
        "owner_name": sp.owner_name,
        "phone": sp.phone,
        "email": sp.email,
        "address": sp.address,
        "gstin": sp.gstin,
        "currency_symbol": sp.currency_symbol,
        "currency_code": sp.currency_code,
        "default_tax_rate": sp.default_tax_rate,
        "invoice_footer": sp.invoice_footer,
        "accessibility_preferences_json": sp.accessibility_preferences_json,
        "updated_at": sp.updated_at.isoformat(),
    }


@app.put("/api/settings/profile", response_model=StoreProfileOut)
def update_store_profile(
    payload: StoreProfileUpdate,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    sp = db.query(StoreProfile).filter(StoreProfile.store_id == effective_store_id).first()
    if not sp:
        sp = StoreProfile(store_id=effective_store_id, store_name="BharatVoice Retail Store")
        db.add(sp)

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(sp, k, v)
    sp.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(sp)

    return {
        "id": sp.id,
        "store_id": sp.store_id,
        "store_name": sp.store_name,
        "owner_name": sp.owner_name,
        "phone": sp.phone,
        "email": sp.email,
        "address": sp.address,
        "gstin": sp.gstin,
        "currency_symbol": sp.currency_symbol,
        "currency_code": sp.currency_code,
        "default_tax_rate": sp.default_tax_rate,
        "invoice_footer": sp.invoice_footer,
        "accessibility_preferences_json": sp.accessibility_preferences_json,
        "updated_at": sp.updated_at.isoformat(),
    }


# ==============================================================================
# VOICE ASSISTANT ENDPOINTS (PHASE 6)
# ==============================================================================

class VoiceQueryRequest(BaseModel):
    query: str
    language_code: str = "en-IN"
    confirmed: bool = False
    action_payload: Optional[dict] = None


@app.post("/api/voice/process")
async def process_voice_command(
    payload: VoiceQueryRequest,
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    """Processes natural language voice or typed commands."""
    store_id = resolve_effective_store(auth_store, "DEFAULT")
    intent_res = process_voice_intent(
        query=payload.query,
        db=db,
        store_id=store_id,
        confirmed=payload.confirmed,
        action_payload=payload.action_payload,
        language_code=payload.language_code,
    )

    audio_base64 = None
    if is_gnani_configured():
        tts_res = await synthesize_speech(
            text=intent_res["reply"],
            language_code=payload.language_code or "en-IN",
        )
        if tts_res.get("audio_base64"):
            audio_base64 = tts_res.get("audio_base64")

    return {
        "query": payload.query,
        "intent": intent_res["intent"],
        "reply": intent_res["reply"],
        "reply_en": intent_res.get("reply_en", intent_res["reply"]),
        "data": intent_res.get("data"),
        "action_required": intent_res.get("action_required", False),
        "action_type": intent_res.get("action_type"),
        "action_payload": intent_res.get("action_payload"),
        "audio_base64": audio_base64,
        "live_gnani": is_gnani_configured(),
        "language_code": payload.language_code,
    }


@app.post("/api/voice/transcribe")
async def transcribe_voice(
    audio: UploadFile = File(...),
    language_code: str = Form("en-IN"),
):
    """Transcribes uploaded audio exclusively via Gnani Prisma STT."""
    if not is_gnani_configured():
        return {
            "success": False,
            "transcription": "",
            "message": "Gnani Prisma credentials not configured. Set GNANI_API_KEY in environment for speech-to-text.",
            "live_gnani": False,
            "provider": "gnani-prisma",
        }

    audio_bytes = await audio.read()
    res = await transcribe_speech(audio_bytes=audio_bytes, language_code=language_code)
    text = (res.get("transcript") or res.get("text") or "").strip()
    return {
        "success": bool(text),
        "transcript": text,
        "transcription": text,
        "confidence": res.get("confidence", 0.95),
        "provider": "gnani-prisma",
        "language": language_code,
        "live_gnani": True,
    }


class VoiceTTSRequest(BaseModel):
    text: str
    language_code: str = "en-IN"
    voice_gender: str = "female"


@app.post("/api/voice/tts")
async def synthesize_voice_endpoint(payload: VoiceTTSRequest):
    """Synthesizes speech exclusively via Gnani Timbre TTS."""
    if not is_gnani_configured():
        return {
            "success": False,
            "audio_base64": None,
            "message": "Gnani Timbre credentials not configured. Set GNANI_API_KEY in environment for Timbre TTS.",
            "live_gnani": False,
            "provider": "gnani-timbre",
        }

    res = await synthesize_speech(
        text=payload.text,
        language_code=payload.language_code,
        voice_gender=payload.voice_gender,
    )
    return {
        "success": res.get("success", False),
        "audio_base64": res.get("audio_base64"),
        "format": res.get("format", "audio/wav"),
        "provider": "gnani-timbre",
        "live_gnani": True,
    }


@app.get("/api/voice/status")
def voice_status():
    """Checks whether live Gnani conversational AI credentials are active."""
    return {
        "live_gnani": is_gnani_configured(),
        "provider": "Gnani.ai (Prisma STT + Timbre TTS)",
        "supported_languages": [
            {"code": "en-IN", "label": "Indian English"},
            {"code": "hi-IN", "label": "Hindi (हिंदी)"},
            {"code": "te-IN", "label": "Telugu (తెలుగు)"},
            {"code": "ta-IN", "label": "Tamil (தமிழ்)"},
            {"code": "kn-IN", "label": "Kannada (ಕನ್ನಡ)"},
            {"code": "mr-IN", "label": "Marathi (मराठी)"},
        ],
    }


# ==============================================================================
# SMART BILL IMPORT & SYNC
# ==============================================================================

class RejectRequest(BaseModel):
    reason: Optional[str] = None


@app.post("/api/bill-imports/upload", response_model=BillImportPreviewResponse, status_code=status.HTTP_201_CREATED)
async def upload_bill_import(
    file: UploadFile = File(...),
    source_bill_number: Optional[str] = Form(None),
    source_system: str = Form("CSV_UPLOAD"),
    store_id: Optional[str] = Form(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
    response: Response = Response(),
):
    filename = file.filename or "unknown.csv"
    if not (filename.lower().endswith(".csv") or filename.lower().endswith(".txt")):
        raise HTTPException(status_code=400, detail="Only CSV files (.csv) are supported for bill import.")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds maximum permitted size of 5MB.")

    effective_store_id = resolve_effective_store(auth_store, store_id)

    preview_resp = register_bill_import_preview(
        db=db,
        file_content=content,
        filename=filename,
        store_id=effective_store_id,
        source_system=source_system,
    )

    if preview_resp.duplicate_status and (
        preview_resp.duplicate_status.classification in ("ALREADY_CONFIRMED", "CONFIRMED_EXACT_DUPLICATE")
        or preview_resp.duplicate_status.is_blocked
    ):
        response.status_code = status.HTTP_409_CONFLICT
    elif preview_resp.status in ("REJECTED", "BLOCKED"):
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED

    return preview_resp


@app.get("/api/bill-imports/{import_id}", response_model=BillImportPreviewResponse)
def get_import_preview(
    import_id: int,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    try:
        return get_bill_import_preview(db=db, import_id=import_id, store_id=effective_store_id)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))


@app.get("/api/bill-imports")
def list_imports(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    return list_bill_imports(db=db, store_id=effective_store_id, status=status, skip=skip, limit=limit)


@app.post("/api/bill-imports/{import_id}/confirm", response_model=BillImportConfirmationResult)
def confirm_import(
    import_id: int,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    try:
        return confirm_bill_import(db=db, import_id=import_id, store_id=effective_store_id)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except RuntimeError as re:
        msg = str(re)
        if "locked" in msg.lower():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)
    except ValueError as ve:
        msg = str(ve)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


@app.post("/api/bill-imports/{import_id}/reject", response_model=BillImportPreviewResponse)
def reject_import(
    import_id: int,
    payload: Optional[RejectRequest] = None,
    store_id: Optional[str] = Query(None),
    auth_store: Optional[str] = Depends(get_authenticated_store_context),
    db: Session = Depends(get_db),
):
    effective_store_id = resolve_effective_store(auth_store, store_id)
    try:
        reason = payload.reason if payload else None
        return reject_bill_import_preview(db=db, import_id=import_id, reason=reason, store_id=effective_store_id)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        msg = str(ve)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
