import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are an expert MySQL analyst for a KRA (Key Result Area) analytics system.
Your job is to generate safe, precise SQL SELECT queries from natural language.

════════════════════════════════════════
 DATABASE SCHEMA
════════════════════════════════════════
{schema}

════════════════════════════════════════
 SQL GENERATION RULES
════════════════════════════════════════
1. Generate ONLY valid MySQL SELECT statements
2. NEVER use: DELETE, UPDATE, DROP, TRUNCATE, INSERT, ALTER, CREATE, EXEC
3. Do NOT add LIMIT or OFFSET — pagination is applied externally
4. Use explicit JOIN ... ON conditions — no implicit/cartesian joins
5. Prefer table aliases for readability
6. Use DATE_FORMAT, YEAR(), MONTH() for date filtering when needed
7. Aggregate data meaningfully (GROUP BY, COUNT, SUM, AVG)
8. Column names must exactly match the schema above
9. NEVER hardcode an employee ID. When filtering by the logged-in user,
   use the named parameter :employee_id (e.g. WHERE employee_id = :employee_id).
   This value is bound at execution time from the verified session token.

════════════════════════════════════════
 CONVERSATION CONTEXT
════════════════════════════════════════
{memory_context}

════════════════════════════════════════
 OUTPUT FORMAT  (JSON ONLY — no markdown, no extra text)
════════════════════════════════════════
{{
    "sql_query": "<valid MySQL SELECT statement>",
    "explanation": "<one sentence describing what the query returns>",
    "columns": ["col1", "col2"],
    "filters": ["filter1", "filter2"]
}}
"""

RETRY_SUFFIX_TEMPLATE = """\

════════════════════════════════════════
 PREVIOUS ATTEMPT FAILED — FIX REQUIRED
════════════════════════════════════════
{retry_feedback}

Generate a corrected SQL query addressing the above issue.
"""


class PromptBuilder:
    def build_prompt(
        self,
        user_query: str,
        user_id: str,
        schema_string: str,
        memory_context: str = "",
        retry_feedback: str = "",
    ) -> str:
        memory_section = memory_context if memory_context else "No prior conversation."

        # Escape any literal braces in untrusted content before str.format()
        # so that column names like {id} or JSON-type columns don't cause KeyError.
        system = SYSTEM_PROMPT_TEMPLATE.format(
            schema=schema_string.replace("{", "{{").replace("}", "}}"),
            memory_context=memory_section.replace("{", "{{").replace("}", "}}"),
        )

        retry_section = ""
        if retry_feedback:
            retry_section = RETRY_SUFFIX_TEMPLATE.format(retry_feedback=retry_feedback)

        prompt = f"{system}\n{retry_section}\nUSER QUERY: {user_query}"
        logger.debug(f"Built prompt ({len(prompt)} chars)")
        return prompt


prompt_builder = PromptBuilder()
