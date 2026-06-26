"""
SchemaLoader — loads and caches database_schema_context.json.

Provides:
  - load_schema()               → raw schema dict
  - get_all_tables()            → list of table name strings
  - get_table(name)             → table dict or None
  - get_columns(table_name)     → list of column dicts
  - get_description(table_name) → table description string
  - format_schema_context()     → LLM-ready formatted string (cached)
  - invalidate()                → force reload on next access (tests)

Hot reload: re-reads the JSON after SCHEMA_RELOAD_TTL seconds (default 300).
Set SCHEMA_RELOAD_TTL=0 to disable hot reload.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SCHEMA_PATH = (
    Path(__file__).parent.parent / "data" / "database_schema_context.json"
)


class SchemaLoader:
    """Thread-safe JSON schema loader with TTL-based hot reload and in-memory cache."""

    _TTL = int(os.getenv("SCHEMA_RELOAD_TTL", "300"))

    def __init__(self, schema_path: Optional[Path] = None) -> None:
        self._path = Path(schema_path) if schema_path else _DEFAULT_SCHEMA_PATH
        self._data: Optional[Dict[str, Any]] = None
        self._loaded_at: float = 0.0
        self._lock = threading.Lock()
        self._formatted_cache: Optional[str] = None

    # ── Internal load / expiry ───────────────────────────────────────────────

    def _expired(self) -> bool:
        return self._data is None or (
            self._TTL > 0 and (time.time() - self._loaded_at) > self._TTL
        )

    @property
    def _schema(self) -> Dict[str, Any]:
        if self._expired():
            with self._lock:
                if self._expired():
                    with open(self._path, "r", encoding="utf-8") as fh:
                        self._data = json.load(fh)
                    self._formatted_cache = None
                    self._loaded_at = time.time()
                    logger.debug(
                        "SchemaLoader: loaded %s (%d tables)",
                        self._path,
                        len(self._data.get("tables", [])),
                    )
        return self._data  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Force the next access to re-read the JSON file. Useful in tests."""
        with self._lock:
            self._data = None
            self._formatted_cache = None
            self._loaded_at = 0.0

    # ── Public API ───────────────────────────────────────────────────────────

    def load_schema(self) -> Dict[str, Any]:
        """Return the raw schema dict."""
        return self._schema

    def get_all_tables(self) -> List[str]:
        """Return a list of all table names."""
        return [t["table_name"] for t in self._schema.get("tables", [])]

    def get_table(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the table dict for the given name, or None if not found."""
        for t in self._schema.get("tables", []):
            if t["table_name"] == name:
                return t
        return None

    def get_columns(self, table_name: str) -> List[Dict[str, Any]]:
        """Return the columns list for the given table name."""
        t = self.get_table(table_name)
        return t["columns"] if t else []

    def get_description(self, table_name: str) -> str:
        """Return the description string for the given table name."""
        t = self.get_table(table_name)
        return t.get("table_description", "") if t else ""

    def format_schema_context(self) -> str:
        """Return a formatted, LLM-ready block listing all tables and columns.

        Result is cached after the first call and cleared on reload.
        """
        if self._formatted_cache is not None:
            return self._formatted_cache

        tables = self._schema.get("tables", [])
        if not tables:
            self._formatted_cache = ""
            return ""

        buf: List[str] = [
            "AVAILABLE DATABASE TABLES",
            "Use these table and column names exactly as defined below.",
            "Never invent table or column names. Always prefer the tables listed here.",
        ]

        for table in tables:
            buf.append("")
            buf.append(f"Table:\n{table['table_name']}")
            buf.append(f"\nDescription:\n{table.get('table_description', '')}")
            buf.append("\nColumns")
            for col in table.get("columns", []):
                buf.append(
                    f"\n{col['column_name']} ({col.get('data_type', '')})\n"
                    f"{col.get('column_description', '')}"
                )

        self._formatted_cache = "\n".join(buf)
        return self._formatted_cache


schema_loader = SchemaLoader()
