"""Item 06 split: personal-task tool handlers. Pure move out of main.py."""
from app import personal_tasks


def _handle_personal_task_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "add_personal_task":
        text = (inp.get("text") or "").strip()
        if not text:
            return "What personal task should I add?"
        t = personal_tasks.add(sender, text)
        return f"Added to your personal tasks ✅ [{t.id}] {text}"

    if tool == "list_personal_tasks":
        tasks = personal_tasks.list_for(sender)
        if not tasks:
            return "אין לך משימות אישיות פתוחות."
        lines = [f"• [{t.id}] {t.text}" for t in tasks]
        return "המשימות האישיות שלך:\n" + "\n".join(lines)

    if tool == "complete_personal_task":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which personal task should I mark done?"
        matches = personal_tasks.find_matching(sender, query)
        if not matches:
            return f"I couldn't find a personal task matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{t.id}] {t.text}" for t in matches]
            return f"Several personal tasks match '{query}' — which one? Reply with its id:\n" + "\n".join(lines)
        t = matches[0]
        personal_tasks.complete(sender, t.id)
        return f"סימנתי שסיימת ✅ — {t.text}"

    if tool == "clear_personal_tasks":
        count = personal_tasks.clear(sender)
        return f"נמחקו {count} משימות אישיות ✅" if count else "אין משימות אישיות למחיקה."

    return ""
