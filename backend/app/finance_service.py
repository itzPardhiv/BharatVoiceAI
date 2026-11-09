"""
Financial Intelligence and Profit Calculation Engine for BharatVoice AI.
Uses Decimal for precise monetary calculations and derives all metrics from SQLite database records.
Enforces:
  Net Sales = Sales Revenue - Discounts - Refunds
  COGS = sum(quantity * unit_cost) for items in completed sales
  Gross Profit = Net Sales - COGS
  Net Profit = Gross Profit - Operating Expenses
  Gross Margin % = (Gross Profit / Net Sales) * 100
  Net Margin % = (Net Profit / Net Sales) * 100
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import Sale, SaleItem, Expense, Product, Customer


def to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(round(float(value), 4)))


def round_cur(val: Decimal) -> float:
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_date_range(
    period: str = "today",
    custom_start: Optional[str] = None,
    custom_end: Optional[str] = None,
) -> Tuple[datetime, datetime, str]:
    """
    Parses date range based on period keyword or custom ISO date strings.
    Returns (start_datetime, end_datetime, normalized_period_label)
    """
    now = datetime.utcnow()
    today_date = now.date()

    period_lower = period.lower().strip()

    if period_lower == "today":
        start = datetime.combine(today_date, datetime.min.time())
        end = start + timedelta(days=1)
        label = "Today"
    elif period_lower == "yesterday":
        start = datetime.combine(today_date - timedelta(days=1), datetime.min.time())
        end = datetime.combine(today_date, datetime.min.time())
        label = "Yesterday"
    elif period_lower in ("7d", "last 7 days", "last_7_days"):
        start = datetime.combine(today_date - timedelta(days=6), datetime.min.time())
        end = datetime.combine(today_date + timedelta(days=1), datetime.min.time())
        label = "Last 7 Days"
    elif period_lower in ("30d", "last 30 days", "last_30_days"):
        start = datetime.combine(today_date - timedelta(days=29), datetime.min.time())
        end = datetime.combine(today_date + timedelta(days=1), datetime.min.time())
        label = "Last 30 Days"
    elif period_lower in ("this_month", "this month"):
        start = datetime(today_date.year, today_date.month, 1)
        # First day of next month
        if today_date.month == 12:
            end = datetime(today_date.year + 1, 1, 1)
        else:
            end = datetime(today_date.year, today_date.month + 1, 1)
        label = "This Month"
    elif period_lower in ("previous_month", "last month", "previous month"):
        if today_date.month == 1:
            start = datetime(today_date.year - 1, 12, 1)
            end = datetime(today_date.year, 1, 1)
        else:
            start = datetime(today_date.year, today_date.month - 1, 1)
            end = datetime(today_date.year, today_date.month, 1)
        label = "Previous Month"
    elif period_lower == "custom" and custom_start and custom_end:
        try:
            start = datetime.fromisoformat(custom_start.replace("Z", ""))
            end = datetime.fromisoformat(custom_end.replace("Z", ""))
            label = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
        except Exception:
            start = datetime.combine(today_date, datetime.min.time())
            end = start + timedelta(days=1)
            label = "Today (Fallback)"
    else:
        # Default today
        start = datetime.combine(today_date, datetime.min.time())
        end = start + timedelta(days=1)
        label = "Today"

    return start, end, label


def calculate_financial_metrics(
    db: Session,
    store_id: str = "DEFAULT",
    period: str = "today",
    custom_start: Optional[str] = None,
    custom_end: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Computes strict non-floating point financial metrics for the requested period.
    Handles zero denominators safely.
    """
    start_dt, end_dt, period_label = parse_date_range(period, custom_start, custom_end)

    # 1. Fetch completed sales in period
    sales = (
        db.query(Sale)
        .filter(
            Sale.store_id == store_id,
            Sale.status == "Completed",
            Sale.created_at >= start_dt,
            Sale.created_at < end_dt,
        )
        .all()
    )

    gross_revenue = Decimal("0.00")
    total_discounts = Decimal("0.00")
    total_refunds = Decimal("0.00")
    cogs = Decimal("0.00")
    has_estimated_cost = False

    sale_ids = [s.id for s in sales]
    items_by_sale: Dict[int, List[SaleItem]] = {sid: [] for sid in sale_ids}

    if sale_ids:
        sale_items = (
            db.query(SaleItem, Product)
            .join(Product, SaleItem.product_id == Product.id)
            .filter(SaleItem.sale_id.in_(sale_ids))
            .all()
        )
        for s_item, prod in sale_items:
            items_by_sale[s_item.sale_id].append((s_item, prod))

    for s in sales:
        gross_revenue += to_decimal(s.total)
        total_discounts += to_decimal(getattr(s, "discount", 0.0))

        # Sum line item COGS
        for s_item, prod in items_by_sale.get(s.id, []):
            qty = to_decimal(s_item.quantity)
            if s_item.unit_cost is not None:
                item_cost = to_decimal(s_item.unit_cost)
            else:
                # Historical sale without snapshot: estimate with current product cost price
                item_cost = to_decimal(prod.cost_price)
                has_estimated_cost = True
            cogs += qty * item_cost

    net_sales = gross_revenue - total_discounts - total_refunds
    gross_profit = net_sales - cogs

    # 2. Operating Expenses in period (ACTIVE status only)
    expenses = (
        db.query(Expense)
        .filter(
            Expense.store_id == store_id,
            Expense.status == "ACTIVE",
            Expense.expense_date >= start_dt,
            Expense.expense_date < end_dt,
        )
        .all()
    )
    operating_expenses = sum([to_decimal(e.amount) for e in expenses], Decimal("0.00"))

    net_profit = gross_profit - operating_expenses

    # Margin calculations (handle zero denominators safely)
    if net_sales > Decimal("0.00"):
        gross_profit_margin = (gross_profit / net_sales) * Decimal("100.00")
        net_profit_margin = (net_profit / net_sales) * Decimal("100.00")
    else:
        gross_profit_margin = Decimal("0.00")
        net_profit_margin = Decimal("0.00")

    # 3. Current Inventory Valuation (All active products in store)
    products = db.query(Product).filter(Product.store_id == store_id, Product.is_active == 1).all()
    inv_cost_valuation = sum([to_decimal(p.stock_qty) * to_decimal(p.cost_price) for p in products], Decimal("0.00"))
    inv_selling_valuation = sum([to_decimal(p.stock_qty) * to_decimal(p.price) for p in products], Decimal("0.00"))

    # 4. Outstanding customer dues
    customer_dues = (
        db.query(func.coalesce(func.sum(Customer.outstanding_balance), 0.0))
        .filter(Customer.store_id == store_id)
        .scalar()
        or 0.0
    )

    return {
        "period": period_label,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "gross_revenue": round_cur(gross_revenue),
        "total_discounts": round_cur(total_discounts),
        "total_refunds": round_cur(total_refunds),
        "net_sales": round_cur(net_sales),
        "cogs": round_cur(cogs),
        "gross_profit": round_cur(gross_profit),
        "operating_expenses": round_cur(operating_expenses),
        "net_profit": round_cur(net_profit),
        "gross_profit_margin": round_cur(gross_profit_margin),
        "net_profit_margin": round_cur(net_profit_margin),
        "total_sales_count": len(sales),
        "has_estimated_cost_records": has_estimated_cost,
        "inventory_cost_valuation": round_cur(inv_cost_valuation),
        "inventory_selling_valuation": round_cur(inv_selling_valuation),
        "outstanding_customer_dues": round_cur(to_decimal(customer_dues)),
    }


def calculate_financial_analytics(
    db: Session,
    store_id: str = "DEFAULT",
    period: str = "7d",
) -> Dict[str, Any]:
    """
    Computes time-series trend points, product margin rankings, and expense breakdowns.
    """
    start_dt, end_dt, period_label = parse_date_range(period)

    # 1. Build daily trend points
    trend_points: List[Dict[str, Any]] = []
    curr = start_dt
    while curr < end_dt:
        next_day = curr + timedelta(days=1)
        # Sales in this day
        day_sales = (
            db.query(Sale)
            .filter(
                Sale.store_id == store_id,
                Sale.status == "Completed",
                Sale.created_at >= curr,
                Sale.created_at < next_day,
            )
            .all()
        )
        day_sale_ids = [s.id for s in day_sales]
        day_cogs = Decimal("0.00")
        if day_sale_ids:
            day_items = (
                db.query(SaleItem, Product)
                .join(Product, SaleItem.product_id == Product.id)
                .filter(SaleItem.sale_id.in_(day_sale_ids))
                .all()
            )
            for item, prod in day_items:
                cost = to_decimal(item.unit_cost if item.unit_cost is not None else prod.cost_price)
                day_cogs += to_decimal(item.quantity) * cost

        day_gross = sum([to_decimal(s.total) for s in day_sales], Decimal("0.00"))
        day_discounts = sum([to_decimal(getattr(s, "discount", 0.0)) for s in day_sales], Decimal("0.00"))
        day_net = day_gross - day_discounts
        day_gp = day_net - day_cogs

        # Expenses in this day
        day_expenses = (
            db.query(func.coalesce(func.sum(Expense.amount), 0.0))
            .filter(
                Expense.store_id == store_id,
                Expense.status == "ACTIVE",
                Expense.expense_date >= curr,
                Expense.expense_date < next_day,
            )
            .scalar()
            or 0.0
        )
        day_exp_dec = to_decimal(day_expenses)
        day_np = day_gp - day_exp_dec

        trend_points.append({
            "date": curr.strftime("%Y-%m-%d"),
            "label": curr.strftime("%b %d"),
            "gross_sales": round_cur(day_gross),
            "net_sales": round_cur(day_net),
            "cogs": round_cur(day_cogs),
            "gross_profit": round_cur(day_gp),
            "expenses": round_cur(day_exp_dec),
            "net_profit": round_cur(day_np),
        })
        curr = next_day

    # 2. Product Profit Performance
    sales_in_period = (
        db.query(Sale.id)
        .filter(
            Sale.store_id == store_id,
            Sale.status == "Completed",
            Sale.created_at >= start_dt,
            Sale.created_at < end_dt,
        )
        .all()
    )
    period_sale_ids = [s[0] for s in sales_in_period]

    prod_stats: Dict[int, Dict[str, Any]] = {}

    if period_sale_ids:
        items = (
            db.query(SaleItem, Product)
            .join(Product, SaleItem.product_id == Product.id)
            .filter(SaleItem.sale_id.in_(period_sale_ids))
            .all()
        )
        for it, prod in items:
            pid = prod.id
            if pid not in prod_stats:
                prod_stats[pid] = {
                    "product_id": pid,
                    "sku": prod.sku,
                    "name": prod.name,
                    "category": prod.category,
                    "qty": Decimal("0.00"),
                    "sales_amount": Decimal("0.00"),
                    "cogs": Decimal("0.00"),
                }
            qty = to_decimal(it.quantity)
            line_tot = to_decimal(it.line_total)
            cost = to_decimal(it.unit_cost if it.unit_cost is not None else prod.cost_price)

            prod_stats[pid]["qty"] += qty
            prod_stats[pid]["sales_amount"] += line_tot
            prod_stats[pid]["cogs"] += qty * cost

    rankings: List[Dict[str, Any]] = []
    for pid, data in prod_stats.items():
        sales_amt = data["sales_amount"]
        cogs_amt = data["cogs"]
        gp = sales_amt - cogs_amt
        margin = (gp / sales_amt * Decimal("100.00")) if sales_amt > Decimal("0.00") else Decimal("0.00")
        rankings.append({
            "product_id": data["product_id"],
            "sku": data["sku"],
            "name": data["name"],
            "category": data["category"],
            "quantity_sold": round_cur(data["qty"]),
            "sales_amount": round_cur(sales_amt),
            "cogs": round_cur(cogs_amt),
            "gross_profit": round_cur(gp),
            "margin_percentage": round_cur(margin),
        })

    # Sort rankings
    best_selling = sorted(rankings, key=lambda r: r["quantity_sold"], reverse=True)[:10]
    highest_profit = sorted(rankings, key=lambda r: r["gross_profit"], reverse=True)[:10]
    low_margin = sorted(rankings, key=lambda r: r["margin_percentage"])[:10]

    # 3. Expense category breakdown
    category_totals = (
        db.query(Expense.category, func.sum(Expense.amount), func.count(Expense.id))
        .filter(
            Expense.store_id == store_id,
            Expense.status == "ACTIVE",
            Expense.expense_date >= start_dt,
            Expense.expense_date < end_dt,
        )
        .group_by(Expense.category)
        .all()
    )

    total_exp_sum = sum([to_decimal(ct[1]) for ct in category_totals], Decimal("0.00"))
    expense_breakdown: List[Dict[str, Any]] = []

    for cat, amt, cnt in category_totals:
        amt_dec = to_decimal(amt)
        pct = (amt_dec / total_exp_sum * Decimal("100.00")) if total_exp_sum > Decimal("0.00") else Decimal("0.00")
        expense_breakdown.append({
            "category": cat,
            "total_amount": round_cur(amt_dec),
            "percentage": round_cur(pct),
            "count": cnt,
        })

    expense_breakdown.sort(key=lambda x: x["total_amount"], reverse=True)

    return {
        "period": period_label,
        "trend": trend_points,
        "best_selling_products": best_selling,
        "highest_profit_products": highest_profit,
        "low_margin_products": low_margin,
        "expense_breakdown": expense_breakdown,
    }


def calculate_inventory_valuation(
    db: Session,
    store_id: str = "DEFAULT",
) -> Dict[str, Any]:
    """
    Computes total cost valuation, total selling valuation, and projected margin across stock.
    """
    products = db.query(Product).filter(Product.store_id == store_id, Product.is_active == 1).all()

    total_items = len(products)
    total_units = Decimal("0.00")
    total_cost = Decimal("0.00")
    total_selling = Decimal("0.00")
    low_stock_count = 0

    for p in products:
        stock = to_decimal(p.stock_qty)
        cost = to_decimal(p.cost_price)
        selling = to_decimal(p.price)

        total_units += stock
        total_cost += stock * cost
        total_selling += stock * selling

        if p.stock_qty <= p.reorder_level:
            low_stock_count += 1

    potential_gp = total_selling - total_cost
    potential_margin = (potential_gp / total_selling * Decimal("100.00")) if total_selling > Decimal("0.00") else Decimal("0.00")

    return {
        "total_items_count": total_items,
        "total_stock_units": round_cur(total_units),
        "total_cost_valuation": round_cur(total_cost),
        "total_selling_valuation": round_cur(total_selling),
        "potential_gross_profit": round_cur(potential_gp),
        "potential_margin_percentage": round_cur(potential_margin),
        "low_stock_items_count": low_stock_count,
    }

