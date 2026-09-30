"""Item 06 split: household list tool handlers. Pure move out of main.py."""
from app.lists import add_item, clear_list, get_all_list_names, get_list, remove_items


def _handle_list_call(sender: str, tool: str, inp: dict) -> str:
    list_name = inp.get("list_name", "")

    if tool == "add_to_list":
        text = inp.get("text", "").strip()
        if not text:
            return "What should I add to the list?"
        add_item(list_name, text, sender)
        return f"Added to {list_name}: {text} ✅"

    if tool == "remove_from_list":
        text = inp.get("text", "").strip()
        removed = remove_items(list_name, text)
        if removed:
            return f"Removed from {list_name}: {', '.join(removed)} ✅"
        return f"No items matching '{text}' found in {list_name}."

    if tool == "clear_list":
        count = clear_list(list_name)
        return f"{list_name.capitalize()} list cleared ({count} item(s)) ✅"

    if tool == "show_list":
        items = get_list(list_name)
        if not items:
            return f"The {list_name} list is empty."
        lines = [f"• {item.text}" for item in items]
        return f"{list_name.capitalize()} list:\n" + "\n".join(lines)

    if tool == "show_all_lists":
        names = get_all_list_names()
        if not names:
            return "You don't have any lists yet."
        lines = [f"• {name} ({count})" for name, count in names]
        return "Your lists:\n" + "\n".join(lines)

    return ""
