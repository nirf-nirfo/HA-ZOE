"""Item 06 split: expense + recurring-expense tool handlers."""
from app import expenses, recurring_expenses, senders
from app.handlers._common import _fmt_ils, _valid_date


def _handle_expense_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "add_expense":
        try:
            amount = float(inp["amount"])
        except (KeyError, TypeError, ValueError):
            return "I need a numeric amount."
        if amount <= 0:
            return "Amount must be positive."
        category = inp.get("category")
        if category not in expenses.CATEGORIES:
            return f"Category must be one of: {', '.join(expenses.CATEGORIES)}."
        payment_method = inp.get("payment_method") or "לא צוין"
        if payment_method not in expenses.PAYMENT_METHODS:
            return f"Payment method must be one of: {', '.join(expenses.PAYMENT_METHODS)}."
        description = (inp.get("description") or "").strip()
        date = _valid_date(inp.get("date"))
        # Honor an explicit source from the model; otherwise default to "manual".
        # (Receipt-vs-manual isn't reliably inferable from tool inputs alone.)
        source = inp.get("source") or "manual"
        e = expenses.add(sender, amount, category, payment_method, description, source=source, date=date)
        payment_note = f" ({payment_method})" if payment_method != "לא צוין" else ""
        desc_note = f" — {description}" if description else ""
        return f"נרשם ✅ {_fmt_ils(amount)} · {category}{payment_note}{desc_note} [id: {e.id}]"

    if tool == "delete_last_expense":
        removed = expenses.delete_last_manual(sender)
        if not removed:
            return "אין הוצאה למחיקה 🤷"
        return (
            f"🗑️ נמחק: {_fmt_ils(removed.amount)} — {removed.category}"
            + (f" ({removed.description})" if removed.description else "")
        )

    if tool == "fix_last_expense":
        try:
            new_amount = float(inp["new_amount"])
        except (KeyError, TypeError, ValueError):
            return "I need a numeric new_amount."
        if new_amount <= 0:
            return "Amount must be positive."
        updated = expenses.update_last_amount(sender, new_amount)
        if not updated:
            return "אין הוצאה לתיקון 🤷"
        return f"✏️ תוקן: {_fmt_ils(new_amount)} — {updated.category} ({updated.description or '—'})"

    if tool == "list_recent_expenses":
        try:
            limit = int(inp.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))
        sender_filter = sender if inp.get("sender_only") else None
        recent = expenses.list_recent(limit, sender_filter)
        if not recent:
            return "אין הוצאות רשומות."
        lines = []
        for e in recent:
            who = "" if sender_filter else f" · {senders.resolve(e.sender)}"
            pay = f" · {e.payment_method}" if e.payment_method != "לא צוין" else ""
            desc = f" — {e.description}" if e.description else ""
            lines.append(f"• [{e.id}] {e.date} · {_fmt_ils(e.amount)} · {e.category}{pay}{who}{desc}")
        header = "ההוצאות שלך" if sender_filter else "הוצאות אחרונות (משפחתי)"
        return f"{header}:\n" + "\n".join(lines)

    if tool == "expense_summary":
        period = inp.get("period")
        start = _valid_date(inp.get("start"))
        end = _valid_date(inp.get("end"))
        category = inp.get("category")
        if category and category not in expenses.CATEGORIES:
            return f"Category must be one of: {', '.join(expenses.CATEGORIES)}."
        sender_filter = sender if inp.get("sender_only") else None
        s = expenses.summary(
            period=period, start=start, end=end, category=category, sender=sender_filter,
        )
        if s["count"] == 0:
            return f"אין הוצאות בין {s['start']} ל-{s['end']}."
        lines = [
            f"סיכום {s['start']} → {s['end']}:",
            f"סה״כ: {_fmt_ils(s['total'])} ({s['count']} הוצאות)",
            "",
            "לפי מדווח:",
        ]
        for phone, amt in s["by_sender"].items():
            lines.append(f"  • {senders.resolve(phone)}: {_fmt_ils(amt)}")
        lines.append("")
        lines.append("לפי קטגוריה:")
        for cat, amt in s["by_category"].items():
            pct = int(round(amt / s["total"] * 100)) if s["total"] else 0
            lines.append(f"  • {cat}: {_fmt_ils(amt)} ({pct}%)")
        lines.append("")
        lines.append("לפי אמצעי תשלום:")
        for method, amt in s["by_payment_method"].items():
            lines.append(f"  • {method}: {_fmt_ils(amt)}")
        return "\n".join(lines)

    if tool == "add_recurring_expense":
        name = (inp.get("name") or "").strip()
        if not name:
            return "I need a name for the recurring bill."
        try:
            amount = float(inp["amount"])
        except (KeyError, TypeError, ValueError):
            return "I need a numeric amount."
        try:
            day = int(inp["day_of_month"])
        except (KeyError, TypeError, ValueError):
            return "I need a day_of_month (1-31)."
        if not 1 <= day <= 31:
            return "day_of_month must be between 1 and 31."
        month_pattern = (inp.get("month_pattern") or "monthly").strip() or "monthly"
        category = inp.get("category") or "חשבונות"
        if category not in expenses.CATEGORIES:
            return f"Category must be one of: {', '.join(expenses.CATEGORIES)}."
        payment_method = inp.get("payment_method") or "לא צוין"
        if payment_method not in expenses.PAYMENT_METHODS:
            return f"Payment method must be one of: {', '.join(expenses.PAYMENT_METHODS)}."
        r = recurring_expenses.add(name, amount, day, month_pattern, category, payment_method)
        pattern_note = "" if r.month_pattern == "monthly" else f" (חודשים: {r.month_pattern})"
        return f"🔄 נוספה הוצאה קבועה [{r.id}]: {r.name} — {_fmt_ils(r.amount)} כל {r.day_of_month} לחודש{pattern_note}"

    if tool == "list_recurring_expenses":
        items = recurring_expenses.list_all()
        if not items:
            return "אין הוצאות קבועות רשומות."
        lines = ["🔄 הוצאות קבועות:"]
        for r in items:
            pay = f" · {r.payment_method}" if r.payment_method != "לא צוין" else ""
            pattern_note = "" if r.month_pattern == "monthly" else f" [{r.month_pattern}]"
            lines.append(f"• [{r.id}] {r.name} — {_fmt_ils(r.amount)} (יום {r.day_of_month}){pattern_note}{pay}")
        return "\n".join(lines)

    if tool == "remove_recurring_expense":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which recurring bill should I remove?"
        matches = recurring_expenses.find_matching(query)
        if not matches:
            return f"לא נמצאה הוצאה קבועה תואמת '{query}'."
        if len(matches) > 1:
            lines = [f"• [{r.id}] {r.name} — {_fmt_ils(r.amount)}" for r in matches]
            return f"כמה הוצאות תואמות '{query}' — איזו? השב עם id:\n" + "\n".join(lines)
        r = matches[0]
        recurring_expenses.remove(r.id)
        return f"🗑️ הוסרה הוצאה קבועה: {r.name} — {_fmt_ils(r.amount)}"

    return ""
