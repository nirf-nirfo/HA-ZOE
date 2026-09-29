"""Root conftest: sets required env vars BEFORE pydantic-settings loads.

The Settings model has half a dozen required fields (Anthropic + WhatsApp
tokens, HA URL, etc). Populate them with harmless placeholders so `from
app.settings import settings` at import time in every store module doesn't
blow up during test collection.
"""
import os
import sys
from pathlib import Path

# Make `import app.*` work whether pytest is invoked from repo root or addon/.
_ADDON_DIR = Path(__file__).resolve().parent
if str(_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(_ADDON_DIR))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("HA_BASE_URL", "http://test-ha.local")
os.environ.setdefault("HA_LONG_LIVED_TOKEN", "test-ha-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test-phone-id")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-wa-token")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
os.environ.setdefault("WHATSAPP_APP_SECRET", "test-secret")
os.environ.setdefault("ALLOWED_SENDER_NUMBERS", "9720000000000")
