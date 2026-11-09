"""
Business Accounts, Cashbook, and Double-Entry Ledger Service for BharatVoice AI.
Handles atomic transfers, running balance audits, and daily cash closing.
"""
from decimal import Decimal
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException, status
from .models import BusinessAccount, AccountTransaction, Sale, Expense
from .finance_service import to_decimal, round_cur


def get_or_create_default_account(
    db: Session,
    store_id: str = "DEFAULT",
    account_type: str = "CASH",
) -> BusinessAccount:
    """
    Ensures a default account exists for the given type and store.
    """
    acc = (
        db.query(BusinessAccount)
        .filter(
            BusinessAccount.store_id == store_id,
            BusinessAccount.account_type == account_type.upper(),
            BusinessAccount.is_active == 1,
        )
        .first()
    )
    if not acc:
        type_names = {
            "CASH": "Cash Counter Drawer",
            "BANK": "Current Bank Account",
            "UPI": "UPI Collection Account",
            "WALLET": "Digital Wallet",
            "OTHER": "General Business Account",
        }
        name = type_names.get(account_type.upper(), f"{account_type} Account")
        acc = BusinessAccount(
            store_id=store_id,
            name=name,
            account_type=account_type.upper(),
            opening_balance=0.0,
            current_balance=0.0,
            is_active=1,
            created_at=datetime.utcnow(),
        )
        db.add(acc)
        db.flush()
    return acc


def record_account_transaction(
    db: Session,
    account_id: int,
    transaction_type: str,
    amount: float,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    description: Optional[str] = None,
    counterpart_account_id: Optional[int] = None,
    store_id: str = "DEFAULT",
) -> AccountTransaction:
    """
    Appends an immutable ledger transaction and updates the account's running balance atomically.
    """
    acc = db.query(BusinessAccount).filter(BusinessAccount.id == account_id).with_for_update().first()
    if not acc:
        raise HTTPException(status_code=404, detail=f"Business account #{account_id} not found.")

    amt_dec = to_decimal(amount)
    cur_bal = to_decimal(acc.current_balance)

    if transaction_type in ("INCOME", "TRANSFER_IN", "REFUND", "EXPENSE_REVERSAL", "ADJUSTMENT_ADD"):
        new_bal = cur_bal + amt_dec
    elif transaction_type in ("EXPENSE", "TRANSFER_OUT", "SALE_REFUND", "ADJUSTMENT_SUB"):
        new_bal = cur_bal - amt_dec
    else:
        new_bal = cur_bal + amt_dec

    acc.current_balance = round_cur(new_bal)

    tx = AccountTransaction(
        store_id=store_id,
        account_id=acc.id,
        transaction_type=transaction_type,
        amount=round_cur(amt_dec),
        running_balance=round_cur(new_bal),
        reference_type=reference_type,
        reference_id=str(reference_id) if reference_id is not None else None,
        description=description,
        counterpart_account_id=counterpart_account_id,
        is_reversed=0,
        created_at=datetime.utcnow(),
    )
    db.add(tx)
    db.flush()
    return tx


def execute_account_transfer(
    db: Session,
    from_account_id: int,
    to_account_id: int,
    amount: float,
    notes: Optional[str] = None,
    store_id: str = "DEFAULT",
) -> Dict[str, Any]:
    """
    Atomically transfers funds between two store accounts with balance verification.
    """
    if from_account_id == to_account_id:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account.")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Transfer amount must be strictly greater than zero.")

    from_acc = (
        db.query(BusinessAccount)
        .filter(BusinessAccount.id == from_account_id, BusinessAccount.store_id == store_id)
        .with_for_update()
        .first()
    )
    if not from_acc:
        raise HTTPException(status_code=404, detail=f"Source account #{from_account_id} not found in store.")

    to_acc = (
        db.query(BusinessAccount)
        .filter(BusinessAccount.id == to_account_id, BusinessAccount.store_id == store_id)
        .with_for_update()
        .first()
    )
    if not to_acc:
        raise HTTPException(status_code=404, detail=f"Destination account #{to_account_id} not found in store.")

    amt_dec = to_decimal(amount)
    from_bal = to_decimal(from_acc.current_balance)

    if from_bal < amt_dec:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient funds in '{from_acc.name}'. Available: ₹{round_cur(from_bal)}, requested: ₹{round_cur(amt_dec)}.",
        )

    # 1. Deduct from source
    new_from_bal = from_bal - amt_dec
    from_acc.current_balance = round_cur(new_from_bal)

    tx_out = AccountTransaction(
        store_id=store_id,
        account_id=from_acc.id,
        transaction_type="TRANSFER_OUT",
        amount=round_cur(amt_dec),
        running_balance=round_cur(new_from_bal),
        reference_type="TRANSFER",
        reference_id=str(to_acc.id),
        description=f"Transfer to {to_acc.name}. {notes or ''}".strip(),
        counterpart_account_id=to_acc.id,
        created_at=datetime.utcnow(),
    )
    db.add(tx_out)

    # 2. Add to destination
    to_bal = to_decimal(to_acc.current_balance)
    new_to_bal = to_bal + amt_dec
    to_acc.current_balance = round_cur(new_to_bal)

    tx_in = AccountTransaction(
        store_id=store_id,
        account_id=to_acc.id,
        transaction_type="TRANSFER_IN",
        amount=round_cur(amt_dec),
        running_balance=round_cur(new_to_bal),
        reference_type="TRANSFER",
        reference_id=str(from_acc.id),
        description=f"Transfer from {from_acc.name}. {notes or ''}".strip(),
        counterpart_account_id=from_acc.id,
        created_at=datetime.utcnow(),
    )
    db.add(tx_in)
    db.commit()

    return {
        "success": True,
        "transferred_amount": round_cur(amt_dec),
        "from_account": {"id": from_acc.id, "name": from_acc.name, "new_balance": round_cur(new_from_bal)},
        "to_account": {"id": to_acc.id, "name": to_acc.name, "new_balance": round_cur(new_to_bal)},
    }


def compute_cash_closing_summary(
    db: Session,
    store_id: str = "DEFAULT",
    for_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Summarizes daily cash movements to reconcile physical cash counter drawers.
    """
    target_date = for_date or date.today()
    start_dt = datetime.combine(target_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    cash_acc = (
        db.query(BusinessAccount)
        .filter(BusinessAccount.store_id == store_id, BusinessAccount.account_type == "CASH")
        .first()
    )
    opening_cash = to_decimal(cash_acc.opening_balance if cash_acc else 0.0)
    current_cash = to_decimal(cash_acc.current_balance if cash_acc else 0.0)

    # Cash sales today
    cash_sales = (
        db.query(func.coalesce(func.sum(Sale.total), 0.0))
        .filter(
            Sale.store_id == store_id,
            Sale.status == "Completed",
            Sale.payment_method.ilike("%CASH%"),
            Sale.created_at >= start_dt,
            Sale.created_at < end_dt,
        )
        .scalar()
        or 0.0
    )
    cash_sales_dec = to_decimal(cash_sales)

    # Total sales all modes
    total_sales_all = (
        db.query(func.coalesce(func.sum(Sale.total), 0.0))
        .filter(
            Sale.store_id == store_id,
            Sale.status == "Completed",
            Sale.created_at >= start_dt,
            Sale.created_at < end_dt,
        )
        .scalar()
        or 0.0
    )

    # Cash expenses today
    cash_expenses = (
        db.query(func.coalesce(func.sum(Expense.amount), 0.0))
        .filter(
            Expense.store_id == store_id,
            Expense.status == "ACTIVE",
            Expense.payment_method.ilike("%CASH%"),
            Expense.expense_date >= start_dt,
            Expense.expense_date < end_dt,
        )
        .scalar()
        or 0.0
    )
    cash_expenses_dec = to_decimal(cash_expenses)

    # Transfers today
    cash_transfers_in = Decimal("0.00")
    cash_transfers_out = Decimal("0.00")
    if cash_acc:
        txs = (
            db.query(AccountTransaction)
            .filter(
                AccountTransaction.account_id == cash_acc.id,
                AccountTransaction.created_at >= start_dt,
                AccountTransaction.created_at < end_dt,
            )
            .all()
        )
        for t in txs:
            if t.transaction_type == "TRANSFER_IN":
                cash_transfers_in += to_decimal(t.amount)
            elif t.transaction_type == "TRANSFER_OUT":
                cash_transfers_out += to_decimal(t.amount)

    expected_closing = opening_cash + cash_sales_dec - cash_expenses_dec + cash_transfers_in - cash_transfers_out

    return {
        "date": target_date.isoformat(),
        "opening_cash": round_cur(opening_cash),
        "cash_sales": round_cur(cash_sales_dec),
        "cash_expenses": round_cur(cash_expenses_dec),
        "cash_transfers_in": round_cur(cash_transfers_in),
        "cash_transfers_out": round_cur(cash_transfers_out),
        "expected_closing_cash": round_cur(expected_closing),
        "actual_current_balance": round_cur(current_cash),
        "total_sales_all_modes": round_cur(to_decimal(total_sales_all)),
    }
