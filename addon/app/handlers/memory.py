"""Item 06 split: memory tool handlers. Pure move out of main.py."""
from app.memory import forget, remember


def _handle_memory_call(tool: str, inp: dict) -> str:
    text = inp.get("text", "").strip()
    if tool == "remember":
        if not text:
            return "Nothing to remember."
        fact = remember(text)
        if fact is None:
            return f"Already in memory: {text}"
        return f"Remembered: {text}"

    if tool == "forget":
        removed = forget(text)
        if removed:
            return "Forgot: " + "; ".join(removed)
        return f"No remembered fact matching '{text}'."

    return ""
