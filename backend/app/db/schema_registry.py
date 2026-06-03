"""
SchemaRegistry — loads kra_schema_llm_context.json and provides:

  • get_relevant_schema_string(query) — focused schema subset with descriptions
  • get_relevant_tables(query)        — list of table names for this query
  • validate_sql_tables(sql)          — tables in generated SQL not in the schema
  • get_valid_tables/columns          — membership checks for SQL validation
  • get_recommended_filters(tables)   — auto-apply soft filters

The JSON is richer than a plain INFORMATION_SCHEMA dump: it includes
table descriptions, column descriptions, business domain groupings, LLM
usage hints, recommended default filters, and relationship candidates.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Business domain → keyword triggers ───────────────────────────────────────
# When a query keyword matches, all tables for that domain are candidates.
_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "employee_master":       ["employee", "user", "staff", "person", "people",
                              "designation", "reporting", "stream", "joined",
                              "email", "e_mail", "role", "shift"],
    "goals_kra":             ["goal", "kra", "objective", "target", "performance",
                              "rating", "remark", "status", "assigned", "progress",
                              "completion", "overdue", "weightage", "productive"],
    "feedback":              ["feedback", "review", "comment", "likes"],
    "skills_certifications": ["skill", "certificate", "certification", "proficiency",
                              "course", "platform", "experience"],
    "badges_recognition":    ["badge", "award"],
    "recommendations":       ["recommendation", "recommend", "position"],
    "rnr":                   ["rnr", "nomination", "reward", "recognition", "cycle",
                              "nominee", "nominator"],
    "notifications":         ["notification"],
    "metadata_master":       ["category", "tag", "master", "classification"],
}

# ── Tables that should accompany a selected table (common joins) ──────────────
_TABLE_COMPANIONS: Dict[str, List[str]] = {
    "user_goal_mapping":     ["master_goals", "user_table", "status"],
    "master_goals":          ["user_goal_mapping", "master_categories"],
    "remarks_table":         ["user_goal_mapping", "user_table"],
    "remarks_threads":       ["remarks_table", "user_table"],
    "approval_history":      ["user_table", "badge_master", "user_badges"],
    "user_badges":           ["badge_master", "user_table"],
    "certification_approvals": ["certificates", "user_table"],
    "certification_completion": ["certificates"],
    "skills":                ["user_table"],
    "rnr_nominations":       ["rnr_categories", "rnr_cycles", "user_table"],
    "rnr_approval_actions":  ["rnr_nominations", "user_table"],
    "user_feedback":         ["user_table"],
    "recommendations":       ["user_table"],
    "goal_history":          ["user_goal_mapping", "master_goals", "user_table"],
    "tag_category":          ["tags", "designation", "master_categories"],
}

# Tables that are purely system/meta and excluded from auto-selection
_EXCLUDED_FROM_AUTO: Set[str] = {
    "migration_log", "stg_kra_excel",
    "user_goal_mapping_backup_pre_migration",
    "ai_messages", "ai_report_versions", "ai_reports",
    "ai_sessions", "ai_shares", "ai_users",
    "kra_blob", "conversation_history",
}

# SQL keywords — must not be treated as table names when extracting FROM/JOIN
_SQL_KEYWORDS: frozenset = frozenset({
    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
    "CROSS", "FULL", "ON", "AND", "OR", "NOT", "IN", "IS", "NULL",
    "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION",
    "ALL", "DISTINCT", "AS", "SET", "WITH", "CASE", "WHEN", "THEN",
    "ELSE", "END", "EXISTS", "BETWEEN", "LIKE", "ASC", "DESC", "INTO",
    "VALUES", "UPDATE", "DELETE", "INSERT", "CREATE", "DROP", "ALTER",
    "TABLE", "INDEX", "VIEW", "PROCEDURE", "FUNCTION", "USING",
    "NATURAL", "STRAIGHT_JOIN", "IF", "IFNULL", "COALESCE",
    "CONCAT", "TRIM", "YEAR", "MONTH", "DATE", "NOW", "COUNT",
    "SUM", "AVG", "MAX", "MIN", "OVER", "PARTITION", "RANK",
    "DATE_FORMAT", "STR_TO_DATE", "DATEDIFF", "TIMESTAMPDIFF",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_cte_names(sql: str) -> Set[str]:
    ctes: Set[str] = set()
    for m in re.finditer(
        r'\bWITH\b\s+`?([a-zA-Z_]\w*)`?\s+AS\s*\(', sql, re.IGNORECASE
    ):
        ctes.add(m.group(1).lower())
    for m in re.finditer(
        r',\s*`?([a-zA-Z_]\w*)`?\s+AS\s*\(', sql, re.IGNORECASE
    ):
        ctes.add(m.group(1).lower())
    return ctes


def _extract_sql_tables(sql: str) -> List[str]:
    sql_clean = re.sub(r"--[^\n]*", " ", sql)
    sql_clean = re.sub(r"/\*.*?\*/", " ", sql_clean, flags=re.DOTALL)
    cte_names = _extract_cte_names(sql_clean)

    pattern = re.compile(
        r"(?:FROM|JOIN)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?"
        r"(?:\s+(?:AS\s+)?`?[a-zA-Z_][a-zA-Z0-9_]*`?)?",
        re.IGNORECASE,
    )
    seen: Set[str] = set()
    tables: List[str] = []
    for m in pattern.finditer(sql_clean):
        name = m.group(1).strip("`\"")
        if name.upper() in _SQL_KEYWORDS:
            continue
        if name.lower() in cte_names:
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            tables.append(name)
    return tables


# ── Data structures ───────────────────────────────────────────────────────────

class ColumnInfo:
    __slots__ = ("name", "data_type", "description")

    def __init__(self, name: str, data_type: str, description: str = "") -> None:
        self.name = name
        self.data_type = data_type
        self.description = description


class TableInfo:
    __slots__ = ("name", "description", "llm_usage_hint", "columns",
                 "recommended_filters", "column_set")

    def __init__(
        self,
        name: str,
        description: str,
        llm_usage_hint: str,
        columns: List[ColumnInfo],
        recommended_filters: List[str],
    ) -> None:
        self.name = name
        self.description = description
        self.llm_usage_hint = llm_usage_hint
        self.columns = columns
        self.recommended_filters = recommended_filters
        self.column_set: Set[str] = {c.name.lower() for c in columns}


# ── Registry ──────────────────────────────────────────────────────────────────

class SchemaRegistry:
    """
    In-memory KRA schema registry backed by kra_schema_llm_context.json.
    Provides rich, description-aware schema context for LLM SQL generation.
    """

    def __init__(self) -> None:
        self._tables: Dict[str, TableInfo] = {}
        self._domains: Dict[str, List[str]] = {}      # domain → [table_names]
        self._global_rules: Dict = {}
        self._loaded: bool = False

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_from_llm_context_json(self, json_path: str) -> bool:
        path = Path(json_path)
        if not path.exists():
            logger.warning("[schema_registry] JSON not found: %s", json_path)
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            # Global rules
            self._global_rules = data.get("global_rules", {})

            # Business domains
            self._domains = data.get("business_domains", {})

            # Tables
            tables: Dict[str, TableInfo] = {}
            for t in data.get("tables", []):
                cols = [
                    ColumnInfo(
                        name=c["name"],
                        data_type=c.get("data_type", ""),
                        description=c.get("description", ""),
                    )
                    for c in t.get("columns", [])
                ]
                tables[t["table_name"]] = TableInfo(
                    name=t["table_name"],
                    description=t.get("description", ""),
                    llm_usage_hint=t.get("llm_usage_hint", ""),
                    columns=cols,
                    recommended_filters=t.get("recommended_filters", []),
                )
            self._tables = tables
            self._loaded = True
            logger.info(
                "[schema_registry] loaded %d tables from %s", len(tables), json_path
            )
            return True
        except Exception as exc:
            logger.error("[schema_registry] load error: %s", exc)
            return False

    def load_from_schema_manager(self, schema_dict: Dict) -> None:
        """Fallback: build a bare registry from the live DB schema dict."""
        tables: Dict[str, TableInfo] = {}
        for table_name, info in schema_dict.items():
            cols = [
                ColumnInfo(name=c["name"], data_type=c["type"])
                for c in info.get("columns", [])
            ]
            tables[table_name] = TableInfo(
                name=table_name,
                description="",
                llm_usage_hint="",
                columns=cols,
                recommended_filters=[],
            )
        self._tables = tables
        self._loaded = True
        logger.info(
            "[schema_registry] synced %d tables from live DB (fallback)", len(tables)
        )

    def is_loaded(self) -> bool:
        return self._loaded

    # ── Relevant-table selection ──────────────────────────────────────────────

    def get_relevant_tables(self, query: str, max_tables: int = 12) -> List[str]:
        if not self._loaded:
            return []
        query_lower = query.lower()
        selected: Set[str] = set()

        # 1. Domain keyword matching
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            if any(re.search(r'\b' + re.escape(kw), query_lower) for kw in keywords):
                for tbl in self._domains.get(domain, []):
                    if tbl in self._tables and tbl not in _EXCLUDED_FROM_AUTO:
                        selected.add(tbl)

        # 2. Direct table name mention in query
        for tbl in self._tables:
            if tbl in _EXCLUDED_FROM_AUTO:
                continue
            if tbl in query_lower or tbl.replace("_", " ") in query_lower:
                selected.add(tbl)

        # 3. Column keyword scoring for tables not yet selected
        scored: List[tuple] = []
        for tbl, info in self._tables.items():
            if tbl in selected or tbl in _EXCLUDED_FROM_AUTO:
                continue
            score = 0
            for col in info.columns:
                for part in col.name.split("_"):
                    if len(part) >= 4 and part.lower() in query_lower:
                        score += 1
            if score > 0:
                scored.append((score, tbl))
        scored.sort(reverse=True)
        budget = max(0, max_tables - len(selected))
        for _, tbl in scored[:budget]:
            selected.add(tbl)

        # 4. Add companion tables (important auto-joins)
        companions: Set[str] = set()
        for tbl in list(selected):
            for companion in _TABLE_COMPANIONS.get(tbl, []):
                if companion in self._tables and companion not in selected:
                    companions.add(companion)
        selected |= companions

        # 5. Always include user_table when employee data is in scope
        if selected & {"user_goal_mapping", "remarks_table", "skills",
                       "certificates", "user_badges", "user_feedback",
                       "rnr_nominations", "goal_history", "recommendations"}:
            selected.add("user_table")

        return sorted(selected)[:max_tables]

    # ── Schema string for LLM prompt ─────────────────────────────────────────

    def get_relevant_schema_string(self, query: str, max_tables: int = 12) -> str:
        """
        Return a rich, description-aware schema string containing only the
        tables most relevant to the user query.  Each table entry includes:
        - Description / purpose
        - LLM usage hint
        - Recommended default filters
        - All columns with data type and description
        """
        if not self._loaded:
            return ""
        tables = self.get_relevant_tables(query, max_tables)
        if not tables:
            return self._format_tables(list(self._tables.keys())[:max_tables])
        return self._format_tables(tables)

    def get_full_schema_string(self) -> str:
        return self._format_tables(sorted(self._tables.keys()))

    def _format_tables(self, table_names: List[str]) -> str:
        lines: List[str] = []
        for name in table_names:
            info = self._tables.get(name)
            if not info:
                continue
            lines.append(f"Table: {name}")
            if info.description:
                lines.append(f"  Purpose: {info.description}")
            if info.recommended_filters:
                lines.append(f"  Auto-filters: {', '.join(info.recommended_filters)}")
            lines.append("  Columns:")
            for col in info.columns:
                desc = f" — {col.description}" if col.description else ""
                lines.append(f"    {col.name:<30} ({col.data_type}){desc}")
            lines.append("")
        return "\n".join(lines)

    # ── Validation ────────────────────────────────────────────────────────────

    def get_valid_tables(self) -> Set[str]:
        return set(self._tables.keys())

    def get_schema_table_list(self) -> List[str]:
        return sorted(self._tables.keys())

    def is_valid_table(self, table: str) -> bool:
        if not self._loaded:
            return True
        lower_map = {t.lower() for t in self._tables}
        return table.lower() in lower_map

    def get_valid_columns(self, table: str) -> Set[str]:
        info = self._tables.get(table) or self._tables.get(table.lower())
        return info.column_set if info else set()

    def is_valid_column(self, table: str, column: str) -> bool:
        return column.lower() in self.get_valid_columns(table)

    def validate_sql_tables(self, sql: str) -> List[str]:
        """
        Extract table names from SQL, return those absent from the schema.
        Fails-open when registry not loaded (cold-start safety).
        """
        if not self._loaded:
            return []
        return [t for t in _extract_sql_tables(sql) if not self.is_valid_table(t)]

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def get_recommended_filters(self, table_names: List[str]) -> List[str]:
        filters: List[str] = []
        for name in table_names:
            info = self._tables.get(name)
            if info:
                filters.extend(info.recommended_filters)
        return filters

    def get_global_rules(self) -> Dict:
        return self._global_rules

    def describe_table(self, table: str) -> Optional[str]:
        info = self._tables.get(table)
        if not info:
            return None
        return self._format_tables([table])


schema_registry = SchemaRegistry()
