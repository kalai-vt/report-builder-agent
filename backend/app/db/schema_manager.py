import logging
from typing import Dict, List

from sqlalchemy import text

from app.db.connection import db_manager

logger = logging.getLogger(__name__)


class SchemaManager:
    def __init__(self):
        self._schema_cache: Dict = {}
        self._schema_loaded: bool = False

    def refresh_schema(self) -> Dict:
        logger.info("Refreshing database schema…")
        schema: Dict = {}

        with db_manager.engine.connect() as conn:
            # ── Single batch query for all columns ────────────────────────────
            cols_result = conn.execute(text(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
                "       COLUMN_KEY, COLUMN_COMMENT "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION"
            ))
            for row in cols_result:
                table = row[0]
                if table not in schema:
                    schema[table] = {"columns": [], "foreign_keys": []}
                schema[table]["columns"].append({
                    "name":     row[1],
                    "type":     row[2],
                    "nullable": row[3] == "YES",
                    "key":      row[4],
                    "comment":  row[5] or "",
                })

            # ── Single batch query for all foreign keys ───────────────────────
            fk_result = conn.execute(text(
                "SELECT TABLE_NAME, COLUMN_NAME, "
                "       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                "FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "  AND REFERENCED_TABLE_NAME IS NOT NULL"
            ))
            for row in fk_result:
                table = row[0]
                if table in schema:
                    schema[table]["foreign_keys"].append({
                        "column":            row[1],
                        "references_table":  row[2],
                        "references_column": row[3],
                    })

        self._schema_cache = schema
        self._schema_loaded = True
        logger.info(f"Schema refreshed: {len(schema)} tables")

        # If the rich JSON registry was not already loaded, sync from live DB
        # so that table/column validation still works even without the JSON file.
        from app.db.schema_registry import schema_registry  # lazy import
        if not schema_registry.is_loaded():
            schema_registry.load_from_schema_manager(schema)

        # Invalidate suggestion cache so next greeting/off-topic call rebuilds
        from app.services.suggestion_builder import suggestion_builder  # lazy import
        suggestion_builder.invalidate()

        return schema

    def get_schema(self) -> Dict:
        if not self._schema_loaded:
            return self.refresh_schema()
        return self._schema_cache

    def get_schema_string(self) -> str:
        schema = self.get_schema()
        lines: List[str] = []
        for table, info in schema.items():
            lines.append(f"Table: {table}")
            fk_map = {fk["column"]: fk for fk in info["foreign_keys"]}
            for col in info["columns"]:
                pk  = " [PK]" if col["key"] == "PRI" else ""
                fk  = f" [FK→{fk_map[col['name']]['references_table']}.{fk_map[col['name']]['references_column']}]" \
                      if col["name"] in fk_map else ""
                nil = "" if col["nullable"] else " NOT NULL"
                cmt = f"  -- {col['comment']}" if col["comment"] else ""
                lines.append(f"  {col['name']} ({col['type']}{nil}{pk}{fk}){cmt}")
            lines.append("")
        return "\n".join(lines)


schema_manager = SchemaManager()
