import re
import logging
from enum import Enum
from typing import Tuple

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNSAFE = "unsafe"


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

# Detects integer FK columns (status_id, goal_id, etc.) compared to non-numeric
# string literals — silently evaluates to 0 in MySQL.
# Explicitly EXCLUDES employee_id (VARCHAR) which is handled separately below.
_ID_STRING_COMPARE_RE = re.compile(
    r"\b(?!employee_id\b)\w*(?:_id|_status)\b\s*(?:=|!=|<>|IN\s*\()\s*'[^0-9'][^']*'",
    re.IGNORECASE,
)

# Catches hardcoded employee_id literal values.
# Valid only when the user explicitly provided that value in their query.
# When a name is given instead of an ID, LIKE on firstname/lastname must be used.
_HARDCODED_EMP_ID_RE = re.compile(
    r"\bemployee_id\s*=\s*'([^']+)'",
    re.IGNORECASE,
)

_SELECT_RE      = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_JOIN_RE        = re.compile(r"\bJOIN\b", re.IGNORECASE)
_ON_RE          = re.compile(r"\bON\b", re.IGNORECASE)
_FROM_CLAUSE_RE = re.compile(
    r"\bFROM\b(.+?)(?:\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_PAGINATION_LIMIT_RE = re.compile(
    r"\s+LIMIT\s+\d+\s*(,\s*\d+|OFFSET\s+\d+)\s*$",
    re.IGNORECASE,
)
_BARE_LIMIT_RE = re.compile(r"\s+LIMIT\s+\d+\s*$", re.IGNORECASE)


class SQLValidator:

    def validate(
        self,
        sql: str,
        user_id: str,
        user_query: str = "",
    ) -> Tuple[ValidationStatus, str, str]:
        sql = self._clean(sql)

        for pattern in _UNSAFE_PATTERNS:
            m = pattern.search(sql)
            if m:
                logger.warning("Unsafe SQL [%s] blocked user=%s", m.group(0), user_id)
                return ValidationStatus.UNSAFE, f"Forbidden operation: '{m.group(0)}'", sql

        if not _SELECT_RE.match(sql):
            return ValidationStatus.UNSAFE, "Only SELECT statements are permitted", sql

        sql = self._strip_limit_offset(sql)

        if self._has_cartesian_join(sql):
            return (
                ValidationStatus.INVALID,
                "Cartesian joins detected — all JOINs must have explicit ON conditions",
                sql,
            )

        # ── Integer FK compared to string literal ─────────────────────────────
        m = _ID_STRING_COMPARE_RE.search(sql)
        if m:
            logger.warning("FK-string comparison [%s] user=%s", m.group(0), user_id)
            return (
                ValidationStatus.INVALID,
                (
                    f"Integer FK column compared to a string literal: '{m.group(0)}'. "
                    "MySQL silently converts the string to 0, returning 0 rows. "
                    "JOIN the lookup table and filter on its name column instead "
                    "(e.g. JOIN status s ON ugm.status_id = s.id WHERE s.status_name = 'Completed')."
                ),
                sql,
            )

        # ── Hardcoded employee_id not from user query ─────────────────────────
        # employee_id is VARCHAR so the comparison is SQL-valid, but it means the
        # LLM guessed/hallucinated the ID from a name instead of using LIKE.
        # Allow ONLY when the user explicitly typed that exact ID in their query.
        for m in _HARDCODED_EMP_ID_RE.finditer(sql):
            val = m.group(1)
            if user_query and val.lower() not in user_query.lower():
                logger.warning(
                    "[sql_validator] hallucinated employee_id '%s' not in query user=%s",
                    val, user_id,
                )
                return (
                    ValidationStatus.INVALID,
                    (
                        f"Hardcoded employee_id = '{val}' was never mentioned by the user. "
                        "You must NOT infer or guess an employee's ID from their name. "
                        "For name-based searches follow Rule 13: "
                        "WHERE u.firstname LIKE '%Firstname%' AND u.lastname LIKE '%Lastname%'. "
                        "Only use WHERE employee_id = 'X' when the user explicitly wrote that ID."
                    ),
                    sql,
                )

        # ── Schema-grounded table + column validation ─────────────────────────
        try:
            from app.db.schema_registry import schema_registry
            if schema_registry.is_loaded():
                invalid_tables = schema_registry.validate_sql_tables(sql)
                if invalid_tables:
                    names = ", ".join(f"'{t}'" for t in invalid_tables)
                    valid_list = ", ".join(schema_registry.get_schema_table_list())
                    logger.warning(
                        "[sql_validator] hallucinated table(s) %s user=%s", names, user_id
                    )
                    return (
                        ValidationStatus.INVALID,
                        (
                            f"Unknown table(s): {names}. "
                            "Use ONLY tables defined in the KRA schema. "
                            f"Available tables: {valid_list}"
                        ),
                        sql,
                    )

                invalid_cols = schema_registry.validate_sql_columns(sql)
                if invalid_cols:
                    corrections = []
                    for col_ref, table, suggestion in invalid_cols:
                        msg = f"'{col_ref}' does not exist in '{table}'"
                        if suggestion:
                            msg += f" -> use '{suggestion}' instead"
                        corrections.append(msg)
                    error_msg = (
                        "Column(s) not found in schema JSON: "
                        + "; ".join(corrections)
                        + ". Use ONLY column names listed in the DATABASE SCHEMA above."
                    )
                    logger.warning(
                        "[sql_validator] hallucinated column(s) %s user=%s",
                        corrections, user_id,
                    )
                    return ValidationStatus.INVALID, error_msg, sql
        except Exception as exc:
            logger.warning("[sql_validator] schema registry check skipped: %s", exc)

        logger.debug("SQL validated user=%s: %s", user_id, sql[:100])
        return ValidationStatus.VALID, "SQL is valid", sql

    def _clean(self, sql: str) -> str:
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql)
        sql = sql.replace("‘", "'").replace("’", "'")
        sql = sql.replace("“", '"').replace("”", '"')
        return sql.strip().rstrip(";")

    def _strip_limit_offset(self, sql: str) -> str:
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
