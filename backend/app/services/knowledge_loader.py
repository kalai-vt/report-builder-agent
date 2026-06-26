"""
KnowledgeLoader — loads and assembles prompt knowledge from prompt_knowledge_base.yaml.

Provides:
  - get_few_shot_text()                        → assembled few-shot reference block
  - build_system_prompt(few_shot, schema, mem) → full system prompt string
  - get_query_hint(key)                        → per-query override hint text
  - get_template(key)                          → named prompt template string
  - invalidate()                               → force reload on next access (tests)

Hot reload: re-reads the YAML after KNOWLEDGE_RELOAD_TTL seconds have elapsed
since the last load (default 300 s).  Set the env-var to 0 to disable.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.services.schema_loader import schema_loader

logger = logging.getLogger(__name__)

_DEFAULT_KB_PATH = (
    Path(__file__).parent.parent / "data" / "prompt_knowledge_base.yaml"
)


class KnowledgeLoader:
    """Thread-safe YAML knowledge loader with TTL-based hot reload."""

    _TTL = int(os.getenv("KNOWLEDGE_RELOAD_TTL", "300"))

    def __init__(self, kb_path: Optional[Path] = None) -> None:
        self._path = Path(kb_path) if kb_path else _DEFAULT_KB_PATH
        self._data: Optional[Dict[str, Any]] = None
        self._loaded_at: float = 0.0
        self._lock = threading.Lock()
        # Derived caches — cleared whenever the file is reloaded
        self._few_shot_cache: Optional[str] = None
        self._tpl_skeleton_cache: Optional[str] = None

    # ── Internal load / expiry ───────────────────────────────────────────────

    def _expired(self) -> bool:
        return self._data is None or (
            self._TTL > 0 and (time.time() - self._loaded_at) > self._TTL
        )

    @property
    def _kb(self) -> Dict[str, Any]:
        if self._expired():
            with self._lock:
                if self._expired():           # double-checked locking
                    with open(self._path, "r", encoding="utf-8") as fh:
                        self._data = yaml.safe_load(fh)
                    self._few_shot_cache = None
                    self._tpl_skeleton_cache = None
                    self._loaded_at = time.time()
                    logger.debug("KnowledgeLoader: loaded %s", self._path)
        return self._data  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Force the next access to re-read the YAML file. Useful in tests."""
        with self._lock:
            self._data = None
            self._few_shot_cache = None
            self._tpl_skeleton_cache = None
            self._loaded_at = 0.0

    # ── Few-shot text assembly ───────────────────────────────────────────────

    def get_few_shot_text(self) -> str:
        """Return the fully assembled APPROVED REFERENCE SQL EXAMPLES block."""
        if self._few_shot_cache is not None:
            return self._few_shot_cache

        fs: Dict[str, Any] = self._kb["few_shot_section"]
        buf: List[str] = []

        # ── Header + matching rule ──
        buf.append(fs["header"].rstrip())
        buf.append("")

        # ── Pre-example disambiguation (non-compliance + stream) ──
        buf.append(fs["pre_example_disambiguation"].rstrip())

        # ── Examples sorted by numeric id ──
        for ex in sorted(fs["examples"], key=lambda e: int(e["id"])):
            eid = str(ex["id"])
            prefix = f"─── Example {eid} "
            dashes = "─" * max(0, 80 - len(prefix))
            buf.append(prefix + dashes)
            # Indent every line of the header block by 2 spaces
            for line in ex["header_text"].rstrip().splitlines():
                buf.append("  " + line)
            buf.append("  SQL:")
            buf.append(ex["sql"].rstrip())
            buf.append("")

        # ── Post-example disambiguation blocks ──
        buf.append(fs["post_example_disambiguation"].rstrip())
        buf.append("")

        # ── Closing banner ──
        buf.append(fs["footer"].rstrip())
        buf.append("")

        self._few_shot_cache = "\n".join(buf)
        return self._few_shot_cache

    # ── System prompt assembly ───────────────────────────────────────────────

    def build_system_prompt(
        self,
        few_shot: str,
        schema: str,
        memory_context: str,
    ) -> str:
        """Assemble the full system prompt with dynamic sections substituted.

        Uses sentinel strings (<<<FEW_SHOT>>> etc.) internally so that SQL
        content containing curly-braces never causes format-string errors.
        """
        if self._tpl_skeleton_cache is None:
            spt = self._kb["system_prompt_template"]
            self._tpl_skeleton_cache = "\n\n".join(
                [
                    spt["intro"].rstrip(),
                    "<<<DB_SCHEMA>>>",
                    "<<<FEW_SHOT>>>",
                    spt["schema_section_header"].rstrip() + "\n<<<SCHEMA>>>",
                    spt["business_terminology"].rstrip(),
                    spt["sql_generation_rules"].rstrip(),
                    spt["conversation_context_header"].rstrip() + "\n<<<MEMORY>>>",
                    spt["output_format"].rstrip(),
                ]
            )

        return (
            self._tpl_skeleton_cache
            .replace("<<<DB_SCHEMA>>>", schema_loader.format_schema_context())
            .replace("<<<FEW_SHOT>>>", few_shot)
            .replace("<<<SCHEMA>>>", schema)
            .replace("<<<MEMORY>>>", memory_context)
        )

    # ── Query hints ──────────────────────────────────────────────────────────

    def get_query_hint(self, key: str) -> str:
        """Return a per-query last-line override hint by key.

        Returns an empty string when the key is not found so callers can
        safely append the result without checking for None.
        """
        return self._kb.get("query_hints", {}).get(key, "")

    # ── Prompt templates ─────────────────────────────────────────────────────

    def get_template(self, key: str) -> str:
        """Return a named prompt template string (supports str.format() placeholders)."""
        return self._kb.get("prompt_templates", {}).get(key, "")


knowledge_loader = KnowledgeLoader()
