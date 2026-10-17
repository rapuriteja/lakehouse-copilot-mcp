"""The secret scanner must catch real-looking keys and ignore code references."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "scan_secrets", Path(__file__).parents[1] / "scripts" / "scan_secrets.py"
)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

# Built at runtime so this test file itself never contains a key-like string.
FAKE_OPENROUTER = "sk" + "-or-" + "v1-" + "0a1b2c3d" * 8
FAKE_OPENAI = "sk" + "-proj-" + "A1b2C3d4" * 4


def test_catches_keys_and_masks_them() -> None:
    found = scan.find(f"OPENROUTER_API_KEY={FAKE_OPENROUTER}")
    names = {name for name, _ in found}
    assert {"OpenRouter key", "Key-like assignment"} <= names
    assert all(FAKE_OPENROUTER not in masked for _, masked in found)
    assert scan.find(f'api_key = "{FAKE_OPENAI}"')


def test_ignores_references_and_placeholders() -> None:
    assert scan.find('"azure-search-api-key" = azurerm_search_service.this.primary_key') == []
    assert scan.find("OPENROUTER_API_KEY=") == []
    assert scan.find('api_key=settings.secret("openrouter_api_key")') == []
