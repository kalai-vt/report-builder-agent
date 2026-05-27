import re
import logging
from enum import Enum
from typing import Tuple

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNSAFE = "unsafe"


# Compiled once at import — avoids re-compilation on every request
_UNSAFE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bDELETE\b", r"\bUPDATE\b", r"\bDROP\b", r"\bTRUNCATE\b",
        r"\bINSERT\b", r"\bALTER\b", r"\bCREATE\b", r"\bREPLACE\b",
        r"\bEXEC\b", r"\bEXECUTE\b", r"\bGRANT\b", r"\bREVOKE\b",
        r"\bLOAD\s+DATA\b", r"\bOUTFILE\b", r"\bINFILE\b",
        r"\bINTO\s+OUTFILE\b", r"\bINTO\s+DUMPFILE\b", r"\bCALL\b",
        r"--", r";.*SELECT",
    ]
]

# Detects comparisons of *_id / *_status_id columns to non-numeric string literals.
# e.g. status_id = 'pending'  or  status_id = 'completed'
# These silently evaluate to status_id = 0 in MySQL and always return 0 rows.
_ID_STRING_COMPARE_RE = re.compile(
    r"\b\w*(?:_id|_status)\b\s*(?:=|!=|<>|IN\s*\()\s*'[^0-9'][^']*'",
    re.IGNORECASE,
)

_SELECT_RE      = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_JOIN_RE        = re.compile(r"\bJOIN\b", re.IGNORECASE)
_ON_RE          = re.compile(r"\bON\b", re.IGNORECASE)
_FROM_CLAUSE_RE = re.compile(
    r"\bFROM\b(.+?)(?:\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|$)",
    re.IGNORECASE | re.DOTALL,
)

# Matches trailing LIMIT n OFFSET m  or  LIMIT n,m  (pagination-style — both parts present).
# A bare "LIMIT n" without OFFSET is a user-requested row cap and is intentionally preserved.
_PAGINATION_LIMIT_RE = re.compile(
    r"\s+LIMIT\s+\d+\s*(,\s*\d+|OFFSET\s+\d+)\s*$",
    re.IGNORECASE,
)

# Extracts a bare trailing LIMIT n (no OFFSET) — kept for reference in _strip_limit_offset.
_BARE_LIMIT_RE = re.compile(
    r"\s+LIMIT\s+\d+\s*$",
    re.IGNORECASE,
)


class SQLValidator:

    def validate(self, sql: str, user_id: str) -> Tuple[ValidationStatus, str, str]:
        sql = self._clean(sql)

        for pattern in _UNSAFE_PATTERNS:
            m = pattern.search(sql)
            if m:
                logger.warning(f"Unsafe SQL [{m.group(0)}] blocked for user={user_id}")
                return ValidationStatus.UNSAFE, f"Forbidden operation: '{m.group(0)}'", sql

        if not _SELECT_RE.match(sql):
            return ValidationStatus.UNSAFE, "Only SELECT statements are permitted", sql

        # Strip pagination-style LIMIT (LIMIT n OFFSET m / LIMIT n,m) injected by the LLM.
        # A bare LIMIT n without OFFSET is a user-requested row cap and is preserved.
        sql = self._strip_limit_offset(sql)

        if self._has_cartesian_join(sql):
            return (
                ValidationStatus.INVALID,
                "Cartesian joins detected — all JOINs must have explicit ON conditions",
                sql,
            )

        m = _ID_STRING_COMPARE_RE.search(sql)
        if m:
            logger.warning(f"FK-string comparison detected [{m.group(0)}] user={user_id}")
            return (
                ValidationStatus.INVALID,
                (
                    f"Integer FK column compared to a string literal: '{m.group(0)}'. "
                    "MySQL converts the string to 0, so this always returns 0 rows. "
                    "JOIN the lookup table and compare on its name column instead "
                    "(e.g. JOIN status s ON ugm.status_id = s.id WHERE s.status_name = 'Completed')."
                ),
                sql,
            )

        logger.debug(f"SQL validated for user={user_id}: {sql[:100]}…")
        return ValidationStatus.VALID, "SQL is valid", sql

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _clean(self, sql: str) -> str:
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql)
        sql = sql.replace("‘", "'").replace("’", "'")
        sql = sql.replace("“", '"').replace("”", '"')
        return sql.strip().rstrip(";")

    def _strip_limit_offset(self, sql: str) -> str:
        """Remove pagination-style LIMIT (with OFFSET) injected by the LLM.
        A bare LIMIT n (no OFFSET) is a user-requested row cap and is kept intact."""
        return _PAGINATION_LIMIT_RE.sub("", sql).strip()

    def _has_cartesian_join(self, sql: str) -> bool:
        m = _FROM_CLAUSE_RE.search(sql)
        if not m:
            return False
        from_clause = m.group(1)
        depth = comma_count = 0
        for ch in from_clause:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                comma_count += 1
        if comma_count == 0:
            return False
        joins = len(_JOIN_RE.findall(sql))
        ons   = len(_ON_RE.findall(sql))
        return joins > 0 and ons < joins


sql_validator = SQLValidator()
