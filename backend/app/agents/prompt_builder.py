import logging
import re

logger = logging.getLogger(__name__)

# Detects a non-compliance query WITHOUT any department grouping keyword
_NON_COMPLIANCE_RE = re.compile(r'\bnon[\s\-]?compli', re.IGNORECASE)
_GROUPING_RE = re.compile(
    r'\b(department[\s\-]?wise|by[\s\-]?department|categorized|grouped|summary|count[\s\-]?per)\b',
    re.IGNORECASE,
)

# Detects "how many employees/users/people have/with/who ..." — must return a scalar COUNT
_HOW_MANY_EMPLOYEE_RE = re.compile(
    r'\bhow\s+many\s+(employees?|users?|people|members?|staff)\b',
    re.IGNORECASE,
)

# Detects "number of goals not filled / missing goals / goals not completed / unfilled goals" per employee
_GOALS_NOT_FILLED_RE = re.compile(
    r'\b(no\.?\s*of\s+goals?\s+(not\s+(filled|completed|submitted)|missing|unfilled)'
    r'|goals?\s+(not\s+(filled|completed|submitted)|missing|unfilled)'
    r'|missing\s+goals?'
    r'|unfilled\s+goals?'
    r'|goals?\s+not\s+filled)\b',
    re.IGNORECASE,
)

# Detects explicit "> 0" / "only missing" filter on top of goals-not-filled report
_GOALS_NOT_FILLED_GT0_RE = re.compile(
    r'\b(missing\s*[>＞]\s*0'
    r'|goals?\s+not\s+filled\s*[>＞]\s*0'
    r'|only\s+(employees?\s+with\s+)?(missing|unfilled|not\s+filled)\s+goals?'
    r'|employees?\s+with\s+(missing|unfilled)\s+goals?'
    r'|who\s+have\s+not\s+filled\s+(any\s+)?goals?)\b',
    re.IGNORECASE,
)

# Detects remark-compliance / completion-rate queries that need the filled/required % formula
# Note: no trailing \b because some alternatives end in non-word chars like %
_COMPLIANCE_METRICS_RE = re.compile(
    r'(?:'
    r'compliance\s*[%﹪％]'                                      # compliance % / compliance%
    r'|compliance\s+(?:percent|rate|pct|score)'                   # compliance rate / percent
    r'|remark\s+(?:completion|compliance)\s*(?:rate|[%﹪％])?'    # remark completion rate / remark compliance
    r'|filled\s+vs\.?\s*(?:required|total|missing)'               # filled vs required
    r'|required\s+(?:goals?\s+)?(?:vs\.?|and)\s+(?:filled|remarks?)'
    r'|compliance\s+gap'
    r'|\d+\s*[%﹪％]\s+compliance'                                # below 50% compliance
    r'|streams?\s+(?:below|above|ranked\s+by|with\s+lowest|with\s+highest)\s+.{0,30}compliance'
    r'|executive\s+(?:compliance|kra)\s+(?:summary|report)'       # executive compliance summary
    r'|executive\s+summary\s+by\s+stream'
    r'|remark\s+completion\s+(?:rate|report)'
    r'|missing\s+remarks?\s+per\s+(?:stream|designation|team)'
    r'|compliance\s+by\s+(?:stream|designation|team|department)'
    r'|cross[\s\-]stream\s+compliance'
    r'|overall\s+(?:compliance|kra\s+health)'
    r'|kra\s+health\s+summary'
    r')',
    re.IGNORECASE,
)

# Detects trend / monthly time-series queries
_TREND_MONTHLY_RE = re.compile(
    r'\b(month[\s\-]?by[\s\-]?month'
    r'|over\s+(the\s+)?(last|past)\s+\d+\s*months?'
    r'|monthly\s+(trend|report|breakdown|completion|compliance|data)'
    r'|trend\s+(over|for|since|from)'
    r'|completion\s+rate\s+(by|per|each)\s+month'
    r'|filled\s+vs\.?\s*missing\s+(over|each|per)\s+month'
    r'|quarter[\s\-]?over[\s\-]?quarter'
    r'|q\d\s+(to|vs\.?)\s+q\d'
    r'|since\s+(jan|january)\s+\d{4}'
    r'|improving|declining'
    r'|month\s+with\s+(highest|lowest|best|worst)\s+.{0,30}(completion|compliance|remark))\b',
    re.IGNORECASE,
)

# Detects multi-stream filter: "Dev or Dev-Data", "X and Y streams", "in both X and Y"
_MULTI_STREAM_RE = re.compile(
    r'\b([\w][\w\-]*\s+or\s+[\w][\w\-]*\s+streams?'
    r'|streams?\s+[\w\-]+\s+(?:and|or)\s+[\w\-]+'
    r'|in\s+(?:both|either)\s+.{0,40}streams?'
    r'|in\s+the\s+[\w\-]+\s+(?:or|and)\s+[\w\-]+\s+streams?'
    r'|streams?\s+(?:include|like|such\s+as)\s+[\w\-]+\s+(?:and|or)\s+[\w\-]+)\b',
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Static few-shot reference section — always injected into the system prompt.
# LLM must use the closest matching example as a SQL template; generate from
# scratch only when no example matches the user query.
# ─────────────────────────────────────────────────────────────────────────────

_STATIC_FEW_SHOT = """\
════════════════════════════════════════
 APPROVED REFERENCE SQL EXAMPLES
════════════════════════════════════════
MATCHING RULE:
  ● If the user query CLOSELY matches an example → use that SQL as your EXACT
    template.  Change ONLY the variable parts: date, month, year, employee name,
    employee ID.  Keep ALL JOINs, column aliases, WHERE guards, GROUP BY, and
    ORDER BY exactly as shown.
  ● If the user query is UNRELATED to any example → generate SQL from scratch
    using the DATABASE SCHEMA and SQL GENERATION RULES below.

NON-COMPLIANCE DISAMBIGUATION (CRITICAL — read before generating any non-compliance SQL):
  "non-compliance report for [month]"                    → Example 1 (employee list, NO GROUP BY)
  "non-compliance report for [month], department-wise"   → Example 2 (department aggregate, GROUP BY)
  "non-compliance report for [month], by department"     → Example 2
  "non-compliance report for [month], categorized"       → Example 2
  DEFAULT (no grouping word): ALWAYS use Example 1 — individual employee rows, never aggregate.

STREAM DISAMBIGUATION (CRITICAL — read before generating any stream/department SQL):
  Trigger: any prompt containing "stream", "department", "stream-wise", "department-wise",
           "employees in a stream", "employees by stream", "stream report".

  MANDATORY RULE — user_table.stream stores a NUMERIC tag_id, NOT a readable name.
  ● NEVER select u.stream  — it returns a raw numeric ID to the user.
  ● ALWAYS join: LEFT JOIN tags t ON t.tag_id = u.stream
  ● ALWAYS display: t.tag AS stream
  ● ALWAYS filter:  t.tag LIKE '%<stream_name>%'
  ● NEVER filter:   u.stream = '<stream_name>'

  "employees in the [X] stream"   → Example 13 (employee list, t.tag AS stream, NO u.stream)
  "employees in the [X] department" → Example 13
  "employee count by stream"       → Example 15 (aggregate, GROUP BY t.tag)
  DEFAULT for any stream query: use Example 13 as the base template.

─── Example 1 ─────────────────────────────────────────────────────────────────
  Query  : "Provide the non-compliance report for April 2026"
  Intent : Employee-level list of goals NOT completed in a specific month
  ⚠ USE THIS when the query has NO grouping words ("department-wise", "by department",
    "categorized", "summary", "count"). Default non-compliance = employee list.
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, u.stream AS department, mg.goal_desc AS goal_name,
       s.status_name AS goal_status, ugm.target_date
FROM user_goal_mapping ugm
JOIN user_table u    ON ugm.employee_id = u.employee_id
JOIN designation d   ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN status s   ON ugm.status_id = s.id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND s.status_name != 'Completed'
  AND YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) = 4
ORDER BY u.stream, u.firstname

─── Example 2 ─────────────────────────────────────────────────────────────────
  Query  : "Provide the non-compliance report for April 2026, categorized department-wise"
  Intent : Aggregate count of non-compliant employees and incomplete goals per department
  ⚠ USE THIS only when query explicitly contains "department-wise", "by department",
    "categorized", "grouped by department", "summary", or "count per department".
  SQL:
SELECT u.stream AS department,
       COUNT(DISTINCT ugm.employee_id) AS non_compliant_employees,
       COUNT(ugm.id) AS incomplete_goals
FROM user_goal_mapping ugm
JOIN user_table u    ON ugm.employee_id = u.employee_id
JOIN designation d   ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN status s   ON ugm.status_id = s.id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND s.status_name != 'Completed'
  AND YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) = 4
GROUP BY u.stream
ORDER BY non_compliant_employees DESC

─── Example 3 ─────────────────────────────────────────────────────────────────
  Query  : "Share the feedback report for 2026, including inputs from the employee, reporting manager, and HR"
  Intent : KRA goals with manager feedback, employee self-assessment, and reporting manager name
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
       s.status_name AS goal_status, ugm.target_date, ugm.assigned_date,
       rt.remarks AS manager_feedback,
       CONCAT(TRIM(m.firstname),' ',TRIM(m.lastname)) AS feedback_given_by,
       r.remark_text AS employee_feedback,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
FROM user_goal_mapping ugm
JOIN user_table u      ON ugm.employee_id = u.employee_id
JOIN designation d     ON u.designation_id = d.designation_id
JOIN master_goals mg   ON ugm.goal_id = mg.goal_id
LEFT JOIN status s     ON ugm.status_id = s.id
LEFT JOIN remarks_table rt  ON ugm.goal_id = rt.goal_id
LEFT JOIN user_table m      ON rt.given_by = m.employee_id
LEFT JOIN remarks_threads r ON r.goal_id = ugm.goal_id
LEFT JOIN user_table mgr    ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND YEAR(ugm.assigned_date) = 2026
ORDER BY u.firstname

─── Example 4 ─────────────────────────────────────────────────────────────────
  Query  : "Provide the KRA report for the past 1 year for Baskar"
  Intent : Full KRA + feedback report for an employee matched by first name, last 1 year
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
       s.status_name AS goal_status, ugm.target_date, ugm.assigned_date,
       rt.remarks AS manager_feedback,
       CONCAT(TRIM(m.firstname),' ',TRIM(m.lastname)) AS feedback_given_by,
       r.remark_text AS employee_feedback,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
FROM user_goal_mapping ugm
JOIN user_table u      ON ugm.employee_id = u.employee_id
JOIN designation d     ON u.designation_id = d.designation_id
JOIN master_goals mg   ON ugm.goal_id = mg.goal_id
LEFT JOIN status s     ON ugm.status_id = s.id
LEFT JOIN remarks_table rt  ON ugm.goal_id = rt.goal_id
LEFT JOIN user_table m      ON rt.given_by = m.employee_id
LEFT JOIN remarks_threads r ON r.goal_id = ugm.goal_id
LEFT JOIN user_table mgr    ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND u.firstname LIKE '%Baskar%'
  AND ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)
ORDER BY ugm.target_date

─── Example 5 ─────────────────────────────────────────────────────────────────
  Query  : "Provide the report for the past 1 year for employee ID VT136"
  Intent : Full KRA + feedback report for a specific employee_id, last 1 year
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
       s.status_name AS goal_status, ugm.target_date, ugm.assigned_date,
       rt.remarks AS manager_feedback,
       CONCAT(TRIM(m.firstname),' ',TRIM(m.lastname)) AS feedback_given_by,
       r.remark_text AS employee_feedback,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
FROM user_goal_mapping ugm
JOIN user_table u      ON ugm.employee_id = u.employee_id
JOIN designation d     ON u.designation_id = d.designation_id
JOIN master_goals mg   ON ugm.goal_id = mg.goal_id
LEFT JOIN status s     ON ugm.status_id = s.id
LEFT JOIN remarks_table rt  ON ugm.goal_id = rt.goal_id
LEFT JOIN user_table m      ON rt.given_by = m.employee_id
LEFT JOIN remarks_threads r ON r.goal_id = ugm.goal_id
LEFT JOIN user_table mgr    ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND u.employee_id = 'VT136'
  AND ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)
ORDER BY ugm.target_date

─── Example 6 ─────────────────────────────────────────────────────────────────
  Query  : "List all employees who directly report to Jerome"
  Intent : Employees whose reporting_manager matches a given manager name
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, u.stream, u.e_mail,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
FROM user_table u
JOIN designation d   ON u.designation_id = d.designation_id
JOIN user_table mgr  ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND d.is_active=1
  AND (mgr.firstname LIKE '%Jerome%' OR mgr.lastname LIKE '%Jerome%')
ORDER BY u.firstname

─── Example 7 ─────────────────────────────────────────────────────────────────
  Query  : "List employees who have not been reviewed in the last 6 months"
  Intent : Active employees with no manager remarks (remarks_table) in past 6 months
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, u.stream, MAX(rt.created_at) AS last_reviewed_at
FROM user_table u
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN remarks_table rt ON u.employee_id = rt.user_id
  AND rt.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
WHERE u.is_active=1 AND u.is_delete=0 AND d.is_active=1
GROUP BY u.employee_id, u.firstname, u.lastname, d.designation_name, u.stream
HAVING last_reviewed_at IS NULL
ORDER BY u.stream, u.firstname

─── Example 8 ─────────────────────────────────────────────────────────────────
  Query  : "Provide the last 1 year report for employee ID VT136, including feedback, compliance, and KRA details"
  Intent : Comprehensive KRA summary with goals, ratings, manager feedback, compliance, reporting manager
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
       s.status_name AS goal_status, ugm.target_date, ugm.assigned_date,
       rt.remarks AS manager_feedback, rt.performance_rating,
       CONCAT(TRIM(m.firstname),' ',TRIM(m.lastname)) AS feedback_given_by,
       r.remark_text AS employee_feedback,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
FROM user_goal_mapping ugm
JOIN user_table u      ON ugm.employee_id = u.employee_id
JOIN designation d     ON u.designation_id = d.designation_id
JOIN master_goals mg   ON ugm.goal_id = mg.goal_id
LEFT JOIN status s     ON ugm.status_id = s.id
LEFT JOIN remarks_table rt  ON ugm.goal_id = rt.goal_id
LEFT JOIN user_table m      ON rt.given_by = m.employee_id
LEFT JOIN remarks_threads r ON r.goal_id = ugm.goal_id
LEFT JOIN user_table mgr    ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND u.employee_id = 'VT136'
  AND ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)
ORDER BY ugm.target_date

─── Example 9 ─────────────────────────────────────────────────────────────────
  Query  : "List all KRA goals assigned to employees"
  Intent : All active KRA assignments — one row per goal, no aggregation
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
       s.status_name AS goal_status, ugm.target_date, ugm.assigned_date
FROM user_goal_mapping ugm
JOIN user_table u    ON ugm.employee_id = u.employee_id
JOIN designation d   ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN status s   ON ugm.status_id = s.id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
ORDER BY u.firstname, ugm.target_date

─── Example 10 ────────────────────────────────────────────────────────────────
  Query  : "Show compliance report — employees who completed their KRA goals this year"
  Intent : Department-wise count of employees and completed goals (status = Completed)
  SQL:
SELECT u.stream AS department,
       COUNT(DISTINCT ugm.employee_id) AS compliant_employees,
       COUNT(ugm.id) AS completed_goals
FROM user_goal_mapping ugm
JOIN user_table u    ON ugm.employee_id = u.employee_id
JOIN designation d   ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN status s   ON ugm.status_id = s.id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND s.status_name = 'Completed'
GROUP BY u.stream
ORDER BY compliant_employees DESC

─── Example 11 ────────────────────────────────────────────────────────────────
  Query  : "Show KRA report grouped by manager — list all goals under each manager"
  Intent : Manager-first view of all KRA goals, self-join for manager display name
  SQL:
SELECT CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager,
       CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, u.stream AS department,
       mg.goal_desc AS goal_name, s.status_name AS goal_status,
       ugm.target_date, ugm.assigned_date
FROM user_goal_mapping ugm
JOIN user_table u    ON ugm.employee_id = u.employee_id
JOIN designation d   ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN status s   ON ugm.status_id = s.id
LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
ORDER BY reporting_manager, u.firstname

─── Example 12 ────────────────────────────────────────────────────────────────
  Query  : "Show KRA goals pending approval — goals submitted but not yet actioned"
  Intent : Goals in a pending state with full approval chain (assigned_by + reporting_manager)
  SQL:
SELECT CONCAT(TRIM(assigner.firstname),' ',TRIM(assigner.lastname)) AS assigned_by,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager,
       CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, u.stream AS department,
       mg.goal_desc AS goal_name, s.status_name AS goal_status,
       ugm.target_date, ugm.assigned_date
FROM user_goal_mapping ugm
JOIN user_table u    ON ugm.employee_id = u.employee_id
JOIN designation d   ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN status s   ON ugm.status_id = s.id
LEFT JOIN user_table assigner ON ugm.assigned_by = assigner.employee_id
LEFT JOIN user_table mgr      ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND s.status_name LIKE '%Pending%'
ORDER BY assigned_by, u.firstname

─── Example 13 ────────────────────────────────────────────────────────────────
  Query  : "Give me a report of all employees in the QA stream."
  Intent : Employees in a named stream — join tags to resolve readable name.
  ⚠ DO NOT select u.stream (numeric ID). DO NOT filter u.stream = 'QA'.
    SELECT must use t.tag AS stream. Filter must use t.tag LIKE '%QA%'.
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       t.tag AS stream
FROM user_table u
LEFT JOIN tags t ON t.tag_id = u.stream
WHERE t.tag LIKE '%QA%'
  AND u.is_active=1 AND u.is_delete=0
ORDER BY u.firstname

─── Example 14 ────────────────────────────────────────────────────────────────
  Query  : "Show all employees in the Dev stream."
  Intent : Same stream-lookup pattern — change only the stream name in the WHERE clause.
  ⚠ DO NOT select u.stream (numeric ID). DO NOT filter u.stream = 'Dev'.
    SELECT must use t.tag AS stream. Filter must use t.tag LIKE '%Dev%'.
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       t.tag AS stream
FROM user_table u
LEFT JOIN tags t ON t.tag_id = u.stream
WHERE t.tag LIKE '%Dev%'
  AND u.is_active=1 AND u.is_delete=0
ORDER BY u.firstname

─── Example 15 ────────────────────────────────────────────────────────────────
  Query  : "Show employee count by stream."
  Intent : Aggregate headcount per stream — group by t.tag (readable name), not by u.stream (tag_id)
  ⚠ STREAM RULE: NEVER group by u.stream directly. Group by t.tag after joining tags.
  SQL:
SELECT t.tag AS stream, COUNT(u.employee_id) AS employee_count
FROM user_table u
LEFT JOIN tags t ON t.tag_id = u.stream
WHERE u.is_active=1 AND u.is_delete=0
GROUP BY t.tag
ORDER BY employee_count DESC

─── Example 16 ────────────────────────────────────────────────────────────────
  Query  : "How many employees have zero filled remarks this month?"
  Intent : SCALAR COUNT — single number, NOT a list of employees.
  ⚠ "how many employees" ALWAYS returns SELECT COUNT(*) AS employee_count — NEVER a list of names.
    Use a subquery to filter candidates first, then COUNT(*) the outer result.
  SQL:
SELECT COUNT(*) AS employee_count
FROM (
    SELECT u.employee_id
    FROM user_table u
    LEFT JOIN remarks_table rt
           ON u.employee_id = rt.user_id
          AND MONTH(rt.enteredDate) = MONTH(CURDATE())
          AND YEAR(rt.enteredDate)  = YEAR(CURDATE())
    WHERE u.is_active = 1 AND u.is_delete = 0
    GROUP BY u.employee_id
    HAVING COUNT(rt.id) = 0
) AS zero_remark_employees

─── Example 17 ────────────────────────────────────────────────────────────────
  Query  : "How many employees have completed all their KRA goals this month?"
  Intent : SCALAR COUNT — single number. Subquery finds qualifying employees; outer COUNT(*) counts them.
  ⚠ NEVER return a list of names when user asks "how many".
  SQL:
SELECT COUNT(*) AS employee_count
FROM (
    SELECT u.employee_id
    FROM user_goal_mapping ugm
    JOIN user_table u  ON ugm.employee_id = u.employee_id
    LEFT JOIN status s ON ugm.status_id = s.id
    WHERE u.is_active = 1 AND u.is_delete = 0
      AND ugm.isDelete = 0 AND ugm.isactive = 1
      AND MONTH(ugm.target_date) = MONTH(CURDATE())
      AND YEAR(ugm.target_date)  = YEAR(CURDATE())
    GROUP BY u.employee_id
    HAVING COUNT(CASE WHEN s.status_name != 'Completed' THEN 1 END) = 0
) AS fully_completed_employees

─── Example 18 ────────────────────────────────────────────────────────────────
  Query  : "Build a report of all employees with number of goals not filled / missing remarks for May 2026."
  Intent : Per-employee count of goals that have NO remarks entry — shows only employees with at least
           one such goal (employees with all goals remarked naturally disappear).
  ⚠ CORRECT PATTERN — "goals not filled" / "missing remarks" / "remarks not submitted":
    1. INNER JOIN user_goal_mapping (not LEFT JOIN) — employees without goals are excluded automatically.
    2. LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
    3. WHERE rt.id IS NULL  — keeps only goals that have NO remark at all.
    4. COUNT(DISTINCT ugm.id) AS goals_not_filled
    5. No HAVING needed — the WHERE rt.id IS NULL already removes employees with 0 missing goals.
  ⚠ NEVER use: CASE WHEN status != 'Completed' — that checks goal status, not whether a remark exists.
  ⚠ NEVER use: LEFT JOIN user_goal_mapping with CASE WHEN — produces wrong 0-count rows.
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, t.tag AS stream,
       COUNT(DISTINCT ugm.id) AS goals_not_filled
FROM user_table u
JOIN user_goal_mapping ugm ON u.employee_id = ugm.employee_id
JOIN designation d         ON u.designation_id = d.designation_id
LEFT JOIN tags t           ON t.tag_id = u.stream
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active = 1 AND u.is_delete = 0
  AND ugm.isDelete = 0 AND ugm.isactive = 1
  AND d.is_active = 1
  AND rt.id IS NULL
  AND MONTH(ugm.target_date) = 5 AND YEAR(ugm.target_date) = 2026
GROUP BY u.employee_id, u.firstname, u.lastname, d.designation_name, t.tag
ORDER BY goals_not_filled DESC, employee

─── Example 19 ────────────────────────────────────────────────────────────────
  Query  : "Build a report of all employees with missing goals / missing remarks (no date filter)."
  Intent : Same as Example 18 but without a specific month/year filter — all-time missing remarks.
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, t.tag AS stream,
       COUNT(DISTINCT ugm.id) AS goals_not_filled
FROM user_table u
JOIN user_goal_mapping ugm ON u.employee_id = ugm.employee_id
JOIN designation d         ON u.designation_id = d.designation_id
LEFT JOIN tags t           ON t.tag_id = u.stream
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active = 1 AND u.is_delete = 0
  AND ugm.isDelete = 0 AND ugm.isactive = 1
  AND d.is_active = 1
  AND rt.id IS NULL
GROUP BY u.employee_id, u.firstname, u.lastname, d.designation_name, t.tag
ORDER BY goals_not_filled DESC, employee

─── Example 20 ────────────────────────────────────────────────────────────────
  Query  : "Build a report comparing remark completion rates across all streams for May 2026."
  Also matches: "compliance rate by stream", "KRA compliance % by stream", "remark completion by stream",
                "streams below 50% compliance", "executive compliance summary by stream".
  Intent : Per-stream remark compliance table — required_goals, filled_remarks, missing_remarks, compliance_pct.
  ⚠ MANDATORY JOIN CHAIN — always start FROM user_goal_mapping, NOT user_table:
    FROM user_goal_mapping ugm
    JOIN user_table u ON ugm.employee_id = u.employee_id
    LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id   ← join on GOAL not employee
  ⚠ REMARK COMPLIANCE formula (NOT goal-status-based, NOT remarks/employees):
    required_goals  = COUNT(DISTINCT ugm.id)
    filled_remarks  = COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END)
    missing_remarks = required_goals - filled_remarks
    compliance_pct  = ROUND(filled / NULLIF(required, 0) * 100, 2)
  ⚠ NEVER start FROM user_table alone — always go through user_goal_mapping.
  ⚠ NEVER compute COUNT(rt.id) / COUNT(u.employee_id) — that is remarks-per-employee, not compliance.
  ⚠ GROUP BY t.tag for stream-wise; change GROUP BY for designation/team views.
  SQL:
SELECT t.tag AS stream,
       COUNT(DISTINCT ugm.id) AS required_goals,
       COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS filled_remarks,
       COUNT(DISTINCT ugm.id) - COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS missing_remarks,
       ROUND(COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) / NULLIF(COUNT(DISTINCT ugm.id), 0) * 100, 2) AS compliance_pct
FROM user_goal_mapping ugm
JOIN user_table u ON ugm.employee_id = u.employee_id
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN tags t ON t.tag_id = u.stream
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND MONTH(ugm.target_date) = 5 AND YEAR(ugm.target_date) = 2026
GROUP BY t.tag
ORDER BY compliance_pct ASC

─── Example 21 ────────────────────────────────────────────────────────────────
  Query  : "Show overall KRA compliance for May 2026 — required, filled, missing, compliance %."
  Intent : Company-wide remark compliance — single aggregate row with all key metrics.
  ⚠ No GROUP BY — produces one summary row for the company (or add GROUP BY for a dimension).
  SQL:
SELECT COUNT(DISTINCT u.employee_id) AS total_employees,
       COUNT(DISTINCT ugm.id) AS required_goals,
       COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS filled_remarks,
       COUNT(DISTINCT ugm.id) - COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS missing_remarks,
       ROUND(COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) / NULLIF(COUNT(DISTINCT ugm.id), 0) * 100, 2) AS compliance_pct
FROM user_goal_mapping ugm
JOIN user_table u ON ugm.employee_id = u.employee_id
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND MONTH(ugm.target_date) = 5 AND YEAR(ugm.target_date) = 2026

─── Example 22 ────────────────────────────────────────────────────────────────
  Query  : "List employees whose goals are all still 'In Progress' this month."
  Intent : Per-employee goal status breakdown — total_goals, inprogress_goals, completed_goals.
           HAVING ensures only employees with zero completed goals appear.
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, t.tag AS stream,
       COUNT(DISTINCT ugm.id) AS total_goals,
       SUM(CASE WHEN s.status_name = 'In Progress' THEN 1 ELSE 0 END) AS inprogress_goals,
       SUM(CASE WHEN s.status_name = 'Completed'   THEN 1 ELSE 0 END) AS completed_goals
FROM user_goal_mapping ugm
JOIN user_table u ON ugm.employee_id = u.employee_id
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN tags t ON t.tag_id = u.stream
LEFT JOIN status s ON ugm.status_id = s.id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND MONTH(ugm.target_date) = MONTH(CURDATE()) AND YEAR(ugm.target_date) = YEAR(CURDATE())
GROUP BY u.employee_id, u.firstname, u.lastname, d.designation_name, t.tag
HAVING SUM(CASE WHEN s.status_name = 'Completed' THEN 1 ELSE 0 END) = 0
   AND COUNT(DISTINCT ugm.id) > 0
ORDER BY employee

─── Example 23 ────────────────────────────────────────────────────────────────
  Query  : "Show Gokul Annadurai's goals with category, points, and remark status (Filled/Missing)."
  Intent : Per-goal drilldown for a named employee — includes category_name, points (weightage),
           and a computed Filled/Missing remark status column.
  ⚠ REMARK STATUS per goal: CASE WHEN rt.id IS NULL THEN 'Missing' ELSE 'Filled' END AS remark_status
  ⚠ Goal category: LEFT JOIN goal_tag_category gtc ON ugm.goal_id = gtc.goal_id AND gtc.is_active = 1
                   LEFT JOIN master_categories mc ON gtc.tag_cat_id = mc.category_id AND mc.is_active = 1
  ⚠ Goal points: mg.weightage AS points  (from master_goals)
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       mc.category_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
       mg.weightage AS points, s.status_name AS goal_status, ugm.target_date,
       CASE WHEN rt.id IS NULL THEN 'Missing' ELSE 'Filled' END AS remark_status,
       rt.remarks AS manager_feedback
FROM user_goal_mapping ugm
JOIN user_table u ON ugm.employee_id = u.employee_id
JOIN designation d ON u.designation_id = d.designation_id
JOIN master_goals mg ON ugm.goal_id = mg.goal_id
LEFT JOIN goal_tag_category gtc ON ugm.goal_id = gtc.goal_id AND gtc.is_active = 1
LEFT JOIN master_categories mc ON gtc.tag_cat_id = mc.category_id AND mc.is_active = 1
LEFT JOIN status s ON ugm.status_id = s.id
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND u.firstname LIKE '%Gokul%' AND u.lastname LIKE '%Annadurai%'
ORDER BY mc.category_name, mg.goal_desc

─── Example 24 ────────────────────────────────────────────────────────────────
  Query  : "Show the trend of filled vs missing remarks over the last 6 months."
  Intent : Month-by-month remark compliance trend — one row per MONTH, counting GOALS not employees.
  ⚠ TREND pattern: GROUP BY DATE_FORMAT(ugm.target_date, '%Y-%m') ORDER BY month ASC.
  ⚠ Count GOALS (ugm.id), NEVER employees for trend queries.
  ⚠ For quarter-over-quarter: GROUP BY YEAR(ugm.target_date), QUARTER(ugm.target_date) AS quarter
  SQL:
SELECT DATE_FORMAT(ugm.target_date, '%Y-%m') AS month,
       COUNT(DISTINCT ugm.id) AS required_goals,
       COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS filled_remarks,
       COUNT(DISTINCT ugm.id) - COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS missing_remarks,
       ROUND(COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) / NULLIF(COUNT(DISTINCT ugm.id), 0) * 100, 2) AS completion_pct
FROM user_goal_mapping ugm
JOIN user_table u ON ugm.employee_id = u.employee_id
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1
  AND ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
GROUP BY DATE_FORMAT(ugm.target_date, '%Y-%m')
ORDER BY month ASC

─── Example 25 ────────────────────────────────────────────────────────────────
  Query  : "Show all remarks authored / written by Rajasingh MA this year."
  Intent : Remarks where a named person is the AUTHOR (given_by), not the goal owner.
  ⚠ remarks_table.given_by = employee_id of the person who WROTE the remark (author).
  ⚠ remarks_table.user_id  = employee_id of the goal owner (person being reviewed).
  ⚠ NEVER filter on rt.user_id when the query asks for remarks "authored by" / "written by" a name.
  ⚠ Join author via rt.given_by: JOIN user_table author ON rt.given_by = author.employee_id
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee_reviewed,
       mg.goal_desc AS goal_name, rt.remarks AS remark_text,
       rt.performance_rating,
       CONCAT(TRIM(author.firstname),' ',TRIM(author.lastname)) AS remark_given_by,
       rt.enteredDate AS remark_date
FROM remarks_table rt
JOIN user_table author ON rt.given_by = author.employee_id
                       AND author.firstname LIKE '%Rajasingh%' AND author.lastname LIKE '%MA%'
JOIN user_table u      ON rt.user_id = u.employee_id
JOIN master_goals mg   ON rt.goal_id = mg.goal_id
WHERE u.is_active=1 AND u.is_delete=0
  AND YEAR(rt.enteredDate) = 2026
ORDER BY rt.enteredDate DESC

─── Example 26 ────────────────────────────────────────────────────────────────
  Query  : "List all employees in the Dev or Dev-Data streams."
  Intent : Multi-stream filter — use t.tag IN (...) when two or more specific stream names are given.
  ⚠ NEVER use multiple LIKE for exact stream lists — use IN ('stream1', 'stream2').
  ⚠ Still requires LEFT JOIN tags t ON t.tag_id = u.stream (stream is a numeric FK).
  SQL:
SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, t.tag AS stream
FROM user_table u
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN tags t ON t.tag_id = u.stream
WHERE u.is_active=1 AND u.is_delete=0 AND d.is_active=1
  AND t.tag IN ('Dev', 'Dev-Data')
ORDER BY t.tag, u.firstname

─── Example 27 ────────────────────────────────────────────────────────────────
  Query  : "Show the overall KRA health summary for May 2026."
  Intent : Company-wide KRA health dashboard — single row with all key health metrics.
  ⚠ at_risk_goals = goals overdue (target_date < CURDATE()) AND not Completed.
  ⚠ avg_completion_pct = ROUND(completed_goals / NULLIF(total_goals, 0) * 100, 2) — GOAL STATUS based.
  ⚠ remark_compliance_pct = ROUND(filled_remarks / NULLIF(total_goals, 0) * 100, 2) — REMARK based.
  SQL:
SELECT COUNT(DISTINCT u.employee_id) AS total_employees,
       COUNT(DISTINCT ugm.id) AS total_goals,
       SUM(CASE WHEN s.status_name = 'Completed' THEN 1 ELSE 0 END) AS completed_goals,
       SUM(CASE WHEN s.status_name != 'Completed' OR s.status_name IS NULL THEN 1 ELSE 0 END) AS incomplete_goals,
       ROUND(SUM(CASE WHEN s.status_name = 'Completed' THEN 1 ELSE 0 END) / NULLIF(COUNT(DISTINCT ugm.id), 0) * 100, 2) AS avg_completion_pct,
       COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS filled_remarks,
       ROUND(COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) / NULLIF(COUNT(DISTINCT ugm.id), 0) * 100, 2) AS remark_compliance_pct,
       SUM(CASE WHEN ugm.target_date < CURDATE() AND (s.status_name != 'Completed' OR s.status_name IS NULL) THEN 1 ELSE 0 END) AS at_risk_goals
FROM user_goal_mapping ugm
JOIN user_table u ON ugm.employee_id = u.employee_id
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN status s ON ugm.status_id = s.id
LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
  AND MONTH(ugm.target_date) = 5 AND YEAR(ugm.target_date) = 2026

─── Example 28 ────────────────────────────────────────────────────────────────
  Query  : "List employees who report directly or indirectly to Baskar Kothandapani."
  Intent : Multi-level hierarchy traversal — direct reports + their direct reports (up to 3 levels deep).
  ⚠ Uses nested subquery union for MySQL compatibility.
  SQL:
SELECT DISTINCT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
       d.designation_name, t.tag AS stream,
       CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS direct_manager
FROM user_table u
JOIN designation d ON u.designation_id = d.designation_id
LEFT JOIN tags t ON t.tag_id = u.stream
LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
WHERE u.is_active=1 AND u.is_delete=0 AND d.is_active=1
  AND u.reporting_manager IN (
      -- Level 1: direct reports of Baskar
      SELECT employee_id FROM user_table
      WHERE firstname LIKE '%Baskar%' AND lastname LIKE '%Kothandapani%' AND is_active=1 AND is_delete=0
      UNION ALL
      -- Level 2: reports of Baskar's direct reports
      SELECT employee_id FROM user_table
      WHERE reporting_manager IN (
          SELECT employee_id FROM user_table
          WHERE firstname LIKE '%Baskar%' AND lastname LIKE '%Kothandapani%' AND is_active=1 AND is_delete=0
      ) AND is_active=1 AND is_delete=0
      UNION ALL
      -- Level 3: reports of level-2 reports
      SELECT employee_id FROM user_table
      WHERE reporting_manager IN (
          SELECT employee_id FROM user_table WHERE reporting_manager IN (
              SELECT employee_id FROM user_table
              WHERE firstname LIKE '%Baskar%' AND lastname LIKE '%Kothandapani%' AND is_active=1 AND is_delete=0
          ) AND is_active=1 AND is_delete=0
      ) AND is_active=1 AND is_delete=0
  )
ORDER BY direct_manager, u.firstname

HOW MANY DISAMBIGUATION (CRITICAL — read before generating any "how many employees" SQL):
  "how many employees have/with/who ..."  → Example 16/17 pattern: SELECT COUNT(*) FROM (subquery)
  NEVER return a list of employee names for a "how many" query.
  NEVER use SELECT employee, ..., COUNT(...) AS n ... GROUP BY employee_id for a "how many" query.
  The outer SELECT must be ONLY: SELECT COUNT(*) AS employee_count — nothing else.

GOALS NOT FILLED / MISSING REMARKS DISAMBIGUATION (CRITICAL):
  "goals not filled" / "missing remarks" / "remarks not submitted" / "no of goals not filled"
      → Use Example 18/19: INNER JOIN ugm + LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
        + WHERE rt.id IS NULL + COUNT(DISTINCT ugm.id) AS goals_not_filled
      → This approach AUTOMATICALLY excludes employees with 0 missing goals — no HAVING needed.
      → With a specific month: add AND MONTH(ugm.target_date)=N AND YEAR(ugm.target_date)=YYYY
      → Without a specific month: omit the date filter (Example 19)
  NEVER use: CASE WHEN s.status_name != 'Completed' — that checks goal completion status, not remark existence.
  NEVER use: LEFT JOIN user_goal_mapping — inner join is required so employees without goals are excluded.
  NEVER show rows with goals_not_filled = 0 — the rt.id IS NULL filter prevents this naturally.

REMARK COMPLIANCE / COMPLETION RATE DISAMBIGUATION (CRITICAL):
  "compliance %" / "compliance rate" / "remark completion rate" / "filled vs required"
  "compliance by stream" / "compliance by designation" / "KRA compliance [with %]"
  "streams below X% compliance" / "executive compliance summary"
      → Use Example 20 (stream-wise) or Example 21 (overall):
        required_goals  = COUNT(DISTINCT ugm.id)
        filled_remarks  = COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END)
        missing_remarks = required_goals - filled_remarks
        compliance_pct  = ROUND(filled / NULLIF(required, 0) * 100, 2)
        LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
      → NEVER use COUNT(employees) or s.status_name for compliance percentage.
      → Change GROUP BY for the dimension: t.tag (stream), d.designation_name, mgr (team).
  "goals completed" / "non-compliance list [without %]"
      → Use existing Examples 1, 2, 10 based on s.status_name.
  "KRA health summary" / "overall KRA health"
      → Use Example 27: both completion % (goal-status) AND remark compliance % in one row.

TREND / TIME-SERIES DISAMBIGUATION (CRITICAL):
  "trend over N months" / "month by month" / "monthly compliance / completion"
  "over the last N months" / "since Jan 2026" / "month with highest / lowest ..."
      → Use Example 24 pattern:
        GROUP BY DATE_FORMAT(ugm.target_date, '%Y-%m') AS month
        ORDER BY month ASC
        Count GOALS (ugm.id) — NEVER employees.
  "quarter-over-quarter" / "Q1 vs Q2"
      → GROUP BY YEAR(ugm.target_date), QUARTER(ugm.target_date) AS quarter
  NEVER GROUP BY employee for trend queries — aggregate across all employees per period.

MULTI-STREAM FILTER DISAMBIGUATION:
  "Dev or Dev-Data" / "QA and Dev" / "X or Y stream" / multiple named streams
      → Use Example 26: t.tag IN ('Dev', 'Dev-Data')
      → NEVER use multiple LIKE for exact named stream lists.
      → STILL require LEFT JOIN tags t ON t.tag_id = u.stream.

REMARKS AUTHOR DISAMBIGUATION (CRITICAL):
  "remarks by [name]" / "feedback authored by [name]" / "written by [name]" / "given by [name]"
      → The named person is the AUTHOR: rt.given_by = author.employee_id
      → Use Example 25: JOIN user_table author ON rt.given_by = author.employee_id
      → NEVER filter on rt.user_id for author name search.

GOAL DRILLDOWN WITH REMARK STATUS:
  "goals with remark status" / "show filled/missing per goal" / "goal compliance breakdown for [name]"
  "Gokul's goals" / "Adharsh's KRA compliance" / "goal details with feedback status"
      → Use Example 23:
        CASE WHEN rt.id IS NULL THEN 'Missing' ELSE 'Filled' END AS remark_status
        mc.category_name via: LEFT JOIN goal_tag_category gtc ON ugm.goal_id = gtc.goal_id AND gtc.is_active=1
                               LEFT JOIN master_categories mc ON gtc.tag_cat_id = mc.category_id AND mc.is_active=1
        mg.weightage AS points

INDIRECT REPORTS DISAMBIGUATION:
  "reports directly or indirectly to [name]" / "entire team under [name]" / "all hierarchy under [name]"
      → Use Example 28: nested UNION ALL subquery for 3-level hierarchy traversal.

════════════════════════════════════════
"""

SYSTEM_PROMPT_TEMPLATE = """\
You are an expert MySQL analyst for a KRA (Key Result Area) analytics system.
Your job is to generate safe, precise SQL SELECT queries from natural language.

{few_shot}
════════════════════════════════════════
 DATABASE SCHEMA
════════════════════════════════════════
{schema}

════════════════════════════════════════
 BUSINESS TERMINOLOGY — translate these terms before generating SQL
════════════════════════════════════════
KRA COMPLIANCE TERMS:
  "non-compliance" / "non-compliant" / "not compliant"
      → goals NOT completed: s.status_name != 'Completed'
  "compliance report [without % or rate]" / "compliant employees" / "completed goals"
      → goals completed: s.status_name = 'Completed'  (goal-status based)
  "pending goals" / "incomplete goals" / "unfinished goals"
      → s.status_name != 'Completed'
  "overdue goals" / "missed targets"
      → ugm.target_date < CURDATE() AND s.status_name != 'Completed'

REMARK COMPLIANCE TERMS (distinct from goal completion — read carefully):
  "compliance %" / "compliance rate" / "compliance_pct" / "remark completion rate"
  "filled vs required" / "required goals vs filled remarks" / "compliance gap"
  "KRA compliance %" / "remark compliance" / "compliance by stream / designation"
  "streams below N% compliance" / "executive compliance summary" / "overall KRA health"
      → These ALWAYS use the REMARK-BASED formula (NOT s.status_name):
        required_goals  = COUNT(DISTINCT ugm.id)            ← total active goal assignments
        filled_remarks  = COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END)
        missing_remarks = required_goals - filled_remarks
        compliance_pct  = ROUND(filled_remarks / NULLIF(required_goals, 0) * 100, 2)
        LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
      → See Example 20 (stream-wise) and Example 21 (overall) in APPROVED REFERENCE SQL EXAMPLES.
      → NEVER use COUNT(employees) for compliance percentage.
      → NEVER use s.status_name = 'Completed' for compliance percentage.
      → Change GROUP BY for the aggregation dimension asked:
          stream → GROUP BY t.tag
          designation → GROUP BY d.designation_name
          overall → no GROUP BY (single row)

  "filled remarks" / "remarks submitted" / "remarks entered" / "goals with feedback"
      → Goals that have at least one entry in remarks_table (rt.id IS NOT NULL after LEFT JOIN)
  "missing remarks" / "remarks not submitted" / "goals without feedback"
      → Goals with NO entry in remarks_table (rt.id IS NULL after LEFT JOIN)
      → For per-employee list: use Example 18/19 (INNER JOIN ugm + WHERE rt.id IS NULL)
      → For aggregate compliance: use Example 20/21 (LEFT JOIN + COUNT formula)

  "at-risk goals" / "overdue unremarked goals" / "goals behind schedule"
      → ugm.target_date < CURDATE() AND (s.status_name != 'Completed' OR s.status_name IS NULL)

APPROVAL / ASSIGNMENT TERMS — use pattern o) for these:
  "pending approval" / "awaiting approval" / "submitted for approval" / "pending manager approval"
      → Goals submitted but awaiting manager action
      → Filter: s.status_name LIKE '%Pending%'
      → MUST include ugm.assigned_by (who assigned/submitted the goal — their employee_id)
      → JOIN user_table ab ON ugm.assigned_by = ab.employee_id
      → Display: CONCAT(TRIM(ab.firstname),' ',TRIM(ab.lastname),' (',ab.employee_id,')') AS assigned_by
      → Also show reporting_manager: LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
      → NEVER use approval_history for KRA goals (it is for skills/badges/certifications ONLY)
      → NEVER invent columns approval_status, approved_by, approval_date — these do NOT exist in user_goal_mapping
  "assigned by [person]" / "goals assigned by [manager]"
      → Filter: ugm.assigned_by = (subquery or employee_id of named person)

MANAGER VIEW TERMS — use pattern n) for these:
  "by manager" / "grouped by manager" / "manager-wise" / "per manager" / "under each manager"
      → Manager is the leading dimension — show manager as the FIRST column
      → JOIN: LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
      → Display: CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname),' (',mgr.employee_id,')') AS manager
      → ORDER BY manager, u.firstname  (do NOT GROUP BY unless also counting)
      → The query lists individual employees under each manager, NOT an aggregate count

GROUPING / CATEGORIZATION TERMS (these require GROUP BY in SQL):
  "department-wise" / "by department" / "categorized by department"
      → GROUP BY u.stream ORDER BY department  (u.stream IS the department column)
  "stream-wise" / "by stream" / "stream-based"
      → GROUP BY u.stream
  "designation-wise" / "by designation" / "role-wise"
      → GROUP BY d.designation_name
  "team-wise" / "by team"
      → GROUP BY u.stream
  When "wise" / "categorized" / "grouped" is used with a dimension,
  include both a COUNT and the dimension column in SELECT.

PERIOD / MONTH TERMS:
  "April 2026"    → YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) = 4
  "March 2026"    → YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) = 3
  "Q1 2026"       → YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) IN (1,2,3)
  "Q2 2026"       → YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) IN (4,5,6)
  "Q3 2026"       → YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) IN (7,8,9)
  "Q4 2026"       → YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) IN (10,11,12)
  Month name → number: Jan=1, Feb=2, Mar=3, Apr=4, May=5, Jun=6,
                        Jul=7, Aug=8, Sep=9, Oct=10, Nov=11, Dec=12

════════════════════════════════════════
 SQL GENERATION RULES
════════════════════════════════════════
1. Generate ONLY valid MySQL SELECT statements
2. NEVER use: DELETE, UPDATE, DROP, TRUNCATE, INSERT, ALTER, CREATE, EXEC
3. LIMIT rules:
   - If the user explicitly requests "top N", "first N", or "bottom N" results → add LIMIT N at the end of the query
   - For all other queries → do NOT add LIMIT or OFFSET (pagination is applied externally)
4. Use explicit JOIN ... ON conditions — no implicit/cartesian joins
5. Prefer table aliases for readability
6. Use DATE_FORMAT, YEAR(), MONTH() for date filtering when needed
7. AGGREGATION vs DETAIL — choose based on user intent:
   - "list" / "show" / "give me" / "what are" / "display" → individual rows, NO GROUP BY, NO COUNT
     Example: "list the goals for Baskar" → one row per goal with goal_desc, status, target_date
   - "how many employees/users/people" → SCALAR COUNT — single number, NOT a list.
     MANDATORY pattern: SELECT COUNT(*) AS employee_count FROM (subquery that filters candidates) AS t
     NEVER return a list of names for a "how many employees" query.
     NEVER use: SELECT employee, ..., COUNT(...) GROUP BY employee_id — this produces a list, not a count.
     See Example 16 and Example 17 in APPROVED REFERENCE SQL EXAMPLES for the exact subquery pattern.
   - "count" / "how many goals/items" / "total" / "summary" / "overview" → aggregate with COUNT/GROUP BY
     Example: "how many goals does Baskar have" → COUNT(ugm.id) AS goal_count GROUP BY u.employee_id
   - "categorized by" / "grouped by" / "wise" → aggregate summary with GROUP BY
   NEVER apply GROUP BY or COUNT when the user says "list", "show", or "display".
8. Column names must exactly match the schema above
9. NEVER hardcode an employee ID. When filtering by the logged-in user,
   use the named parameter :employee_id (e.g. WHERE employee_id = :employee_id).
   This value is bound at execution time from the verified session token.
10. EMPLOYEE DISPLAY FORMAT — mandatory for every query that includes employee data:
    - NEVER select raw firstname/first_name, lastname/last_name, or employee_id as
      separate display columns. Always merge them into a single display column.
    - Use this CONCAT pattern (adjust column/table aliases to match the schema):
        CONCAT(TRIM(u.firstname), ' ', TRIM(u.lastname), ' (', u.employee_id, ')') AS employee
    - This produces the required format: "John Smith (EMP001)"
    - Apply to ALL contexts: main SELECT, sub-queries, CTEs, GROUP BY labels, etc.
    - When the query already aggregates (e.g. COUNT per employee), keep the aggregate
      columns and replace the raw name columns with this CONCAT expression.
    - If employee_id is not available in the joined tables, use:
        CONCAT(TRIM(u.firstname), ' ', TRIM(u.lastname)) AS employee

11. UNKNOWN VALUE HANDLING — for columns whose valid values are not listed in the schema
    (e.g. stream, designation, status, skill_name, badge_name, category_name):
    a) If the user provides a specific value AND the column exists in the schema above:
       Generate SQL using that value directly in a WHERE clause.
       Do NOT assume or verify whether the value exists — let the database return rows.
       ⚠ STREAM EXCEPTION — user_table.stream stores a numeric tag_id, NOT a name:
         WRONG → WHERE u.stream = 'QA'
         CORRECT → LEFT JOIN tags t ON t.tag_id = u.stream … WHERE t.tag LIKE '%QA%'
         See Examples 13–15 and Rule 16 for the full stream pattern.
    b) If the user asks to LIST available values ("show all streams", "what designations exist"):
       Generate: SELECT DISTINCT <column> FROM <table> ORDER BY <column>
       to return actual values from the database.
       For streams: SELECT DISTINCT t.tag FROM tags t ORDER BY t.tag
    c) If the column itself does NOT exist in the schema above:
       Set sql_query to "" and explain that the data is not available.
    d) NEVER invent or hardcode enumerable values (stream names, status names,
       designation names, badge names, skill names, category names, etc.) unless
       the user explicitly stated them in the prompt.
    e) Auto-apply recommended filters shown in the schema above (e.g. is_delete=0,
       is_active=1) whenever the relevant table is queried.

12. COLUMN NAME ACCURACY — always use the EXACT column name shown in the DATABASE SCHEMA above.
    This database uses non-standard names that differ from common conventions:
    - Employee email      → u.e_mail            (NOT u.email, NOT u.email_id, NOT u.email_address)
    - First name          → u.firstname          (NOT u.first_name)
    - Last name           → u.lastname           (NOT u.last_name)
    - Manager reference   → u.reporting_manager  (NOT u.manager_id, NOT u.manager, NOT u.manager_employee_id)
      • reporting_manager stores the manager's employee_id as a VARCHAR.
      • For reporting manager display name only: LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
        → CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
    - Soft-delete flag    → u.is_delete          (NOT u.is_deleted, NOT u.deleted)
    If a column name you want is NOT listed in the schema, do NOT guess or invent a name.
    Look it up in the schema above and use the exact name shown there.

    FEEDBACK SOURCES — never mix these up:
    ┌──────────────────────────┬──────────────────────────────────────────────────────────────────────┐
    │ Feedback type            │ Source & JOIN                                                        │
    ├──────────────────────────┼──────────────────────────────────────────────────────────────────────┤
    │ Manager feedback (text)  │ rt.remarks  FROM remarks_table                                       │
    │                          │ → LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id             │
    │                          │ → rt.given_by = manager's employee_id who wrote the remark           │
    │                          │ → Manager name: LEFT JOIN user_table m ON rt.given_by = m.employee_id│
    │ Employee feedback (text) │ r.remark_text  FROM remarks_threads                                  │
    │                          │ → LEFT JOIN remarks_threads r ON r.goal_id = ugm.goal_id             │
    │ Reporting manager name   │ CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_mgr  │
    │ (display only)           │ → LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id  │
    └──────────────────────────┴──────────────────────────────────────────────────────────────────────┘
    CRITICAL RULES:
    • NEVER alias a user_table join on u.reporting_manager as "feedback" — it is a display name, not feedback text.
    • NEVER label rt.remarks as "employee_feedback" — it is the MANAGER's remark.
    • NEVER call the reporting manager name column "reporting_manager_feedback" — feedback comes from remarks_table.

13. EMPLOYEE NAME SEARCH — use LIKE on separate name columns, NEVER on CONCAT:
    Schema columns: user_table.firstname, user_table.lastname, user_table.employee_id

    a) Single name provided (one word — likely a first name):
         "Baskar"
         → WHERE u.firstname LIKE '%Baskar%'

    b) Two-word name provided (Firstname + Lastname):
         "Baskar Kothandapany"
         → WHERE u.firstname LIKE '%Baskar%'
             AND u.lastname  LIKE '%Kothandapany%'

    c) Employee ID provided (alphanumeric code, e.g. VT136, EMP001):
         "VT136"
         → WHERE u.employee_id = 'VT136'

    d) Both employee ID and name present → use employee_id only (exact match wins):
         "Baskar VT136" or "VT136 Baskar Kothandapany"
         → WHERE u.employee_id = 'VT136'

    e) NEVER use CONCAT(firstname, ' ', lastname) LIKE '%...%' as the search filter.
       Always use separate LIKE conditions on firstname and lastname individually.

    f) This rule applies to ALL employee-specific reports:
       KRA reports, goal reports, feedback reports, skill reports,
       certification reports, badge reports, compliance reports,
       reportee/direct-report reports, and period-based employee reports.

    g) NEVER ask for clarification when a name or employee ID is detected in the query.
       Generate SQL directly using the matching rule above.

    h) Employee display always uses:
       CONCAT(TRIM(u.firstname), ' ', TRIM(u.lastname), ' (', u.employee_id, ')') AS employee

14. COLUMN SELECTION GUIDANCE — pick the right pattern based on query intent:
    ┌──────────────────────────────────────┬────────────────────────────────────────────────────────────────────────┐
    │ User intent                          │ Pattern & distinctive columns                                          │
    ├──────────────────────────────────────┼────────────────────────────────────────────────────────────────────────┤
    │ "list goals" / "show goals"          │ a) employee, designation, goal_name, custom_goal, goal_status,         │
    │                                      │    target_date, assigned_date                                          │
    ├──────────────────────────────────────┼────────────────────────────────────────────────────────────────────────┤
    │ "by manager" / "per manager" /       │ n) manager (FIRST), employee, designation, department,                 │
    │ "manager-wise" / "under manager"     │    goal_name, custom_goal, goal_status, target_date, assigned_date     │
    ├──────────────────────────────────────┼────────────────────────────────────────────────────────────────────────┤
    │ "pending approval" /                 │ o) employee, designation, department, goal_name, custom_goal,          │
    │ "awaiting approval" /                │    goal_status, assigned_by (approver), reporting_manager,             │
    │ "submitted for approval"             │    assigned_date, target_date                                           │
    ├──────────────────────────────────────┼────────────────────────────────────────────────────────────────────────┤
    │ "with feedback" / "with remarks"     │ m) employee + goal_name + goal_status + manager_feedback +             │
    │                                      │    employee_feedback + reporting_manager                                │
    ├──────────────────────────────────────┼────────────────────────────────────────────────────────────────────────┤
    │ "non-compliance" / "non-compliant"   │ j) department, COUNT(non-compliant employees), COUNT(goals)            │
    │                                      │    OR k) department, employee, goal_name, goal_status, target_date     │
    └──────────────────────────────────────┴────────────────────────────────────────────────────────────────────────┘
    CRITICAL: Different query intents MUST produce different column sets — never return the same columns
    for "list goals", "by manager", and "pending approval" queries.

15. QUERY PATTERNS — follow these JOIN chains for every common report type.
    NEVER use goal_history for current goal reports — use user_goal_mapping.
    NEVER use EXISTS (SELECT 1 FROM goal_history ...) as a filter in any KRA or goal report.
    The date range filter for KRA reports must be applied on ugm.target_date in user_goal_mapping — NOT on goal_history.
    Always apply every recommended filter shown in the schema.

    DATE RANGE SEMANTICS — critical: apply the correct operator based on user intent.
    "in the last N months" means the RECENT period — goals whose target_date falls WITHIN the past N months.
    This ALWAYS uses >= (greater than or equal), never <.

    CORRECT MAPPING (memorize these exactly):
    ┌─────────────────────────────────────────────┬────────────────────────────────────────────────────────┐
    │ User says                                   │ SQL filter                                             │
    ├─────────────────────────────────────────────┼────────────────────────────────────────────────────────┤
    │ "last 6 months" / "past 6 months"           │ ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)│
    │ "last 1 year"   / "past 1 year"             │ ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR) │
    │ "last 3 months"                             │ ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)│
    │ "older than 6 months" / "more than 6 months ago" │ ugm.target_date < DATE_SUB(CURDATE(), INTERVAL 6 MONTH) │
    │ "overdue" (missed deadline)                 │ ugm.target_date < CURDATE() AND s.status_name != 'Completed' │
    └─────────────────────────────────────────────┴────────────────────────────────────────────────────────┘
    RULE: "last N months" → >= DATE_SUB(CURDATE(), INTERVAL N MONTH)   ← ALWAYS >=, NEVER <
    RULE: "older than N months" → < DATE_SUB(CURDATE(), INTERVAL N MONTH)   ← ALWAYS <

    COMPLETION STATUS FILTER — use s.status_name for filtering goal completion:
    - "not completed" / "incomplete" / "pending" / "in progress"
        → AND s.status_name != 'Completed'
    - "completed" / "done" / "finished"
        → AND s.status_name = 'Completed'
    - "overdue and not completed"
        → AND ugm.target_date < CURDATE() AND s.status_name != 'Completed'
    Rule 11d (don't hardcode status values) does NOT apply to 'Completed' — it is the standard
    completion status and must always be used when the user asks about completion.

    a) KRA / GOAL LIST (use when user says "list goals", "show goals", "what are the goals"):
       -- Returns ONE ROW PER GOAL — NO GROUP BY, NO COUNT
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
              s.status_name AS goal_status, ugm.target_date, ugm.assigned_date
       FROM user_goal_mapping ugm
       JOIN user_table u    ON ugm.employee_id = u.employee_id
       JOIN designation d   ON u.designation_id = d.designation_id
       JOIN master_goals mg ON ugm.goal_id = mg.goal_id
       LEFT JOIN status s   ON ugm.status_id = s.id
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
       -- "last N months" date filter  : AND ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL N MONTH)
       -- "not completed" status filter: AND s.status_name != 'Completed'
       -- Name filter                  : AND u.firstname LIKE '%Baskar%'

    b) EMPLOYEE LIST / MASTER DATA:
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              d.designation_name, u.e_mail, u.stream, u.role
       FROM user_table u
       JOIN designation d ON u.designation_id = d.designation_id
       WHERE u.is_active=1 AND u.is_delete=0 AND d.is_active=1

    c) PERFORMANCE RATING / APPRAISAL REMARKS:
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              mg.goal_desc AS goal, rt.performance_rating, rt.remarks, rt.remark_year, rt.remark_month
       FROM remarks_table rt
       JOIN user_table u    ON rt.user_id = u.employee_id
       JOIN master_goals mg ON rt.goal_id = mg.goal_id
       WHERE u.is_active=1 AND u.is_delete=0

    d) SKILLS:
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              sk.skill_name, sk.proficiency_level, sk.months_of_experience, sk.status
       FROM skills sk
       JOIN user_table u ON sk.employee_id = u.employee_id
       WHERE sk.is_deleted=0 AND u.is_active=1 AND u.is_delete=0

    e) CERTIFICATIONS:
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              c.certificate_name, c.course, c.platform, c.status, c.issued_at, c.expected_completion_date
       FROM certificates c
       JOIN user_table u ON c.employee_id = u.employee_id
       WHERE c.is_deleted=0 AND u.is_active=1 AND u.is_delete=0

    f) BADGES:
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              bm.badge_name, bm.badge_description, ub.status, ub.created_at AS awarded_date
       FROM user_badges ub
       JOIN user_table u    ON ub.employee_id = u.employee_id
       JOIN badge_master bm ON ub.badge_master_id = bm.badge_master_id
       WHERE ub.is_deleted=0 AND bm.is_deleted=0 AND u.is_active=1 AND u.is_delete=0

    g) FEEDBACK:
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              uf.feedback_desc, uf.feedback_date, uf.feedback_year, uf.likes
       FROM user_feedback uf
       JOIN user_table u ON uf.employee_id = u.employee_id
       WHERE uf.isactive=1 AND u.is_active=1 AND u.is_delete=0

    h) MANAGER TEAM / REPORTEES (logged-in manager):
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              d.designation_name, u.stream, u.e_mail
       FROM user_table u
       JOIN designation d ON u.designation_id = d.designation_id
       WHERE u.reporting_manager = :employee_id AND u.is_active=1 AND u.is_delete=0 AND d.is_active=1

    j) NON-COMPLIANCE — see APPROVED REFERENCE SQL EXAMPLES above:
       Example 1 = employee-level list (default, no GROUP BY)
       Example 2 = department aggregate (only when "department-wise" / "categorized" is stated)

    l) COMPLIANCE REPORT (goals completed, department-wise summary):
       SELECT u.stream AS department,
              COUNT(DISTINCT ugm.employee_id) AS compliant_employees,
              COUNT(ugm.id) AS completed_goals
       FROM user_goal_mapping ugm
       JOIN user_table u    ON ugm.employee_id = u.employee_id
       JOIN designation d   ON u.designation_id = d.designation_id
       JOIN master_goals mg ON ugm.goal_id = mg.goal_id
       LEFT JOIN status s   ON ugm.status_id = s.id
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
         AND s.status_name = 'Completed'
       -- Month/period filter: AND YEAR(ugm.target_date) = 2026 AND MONTH(ugm.target_date) = 4
       GROUP BY u.stream
       ORDER BY compliant_employees DESC

    m) KRA WITH FEEDBACK (manager remark + employee self-assessment + reporting manager):
       -- Use when user asks for KRA report "including feedback", "with remarks", "with manager feedback"
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              d.designation_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
              s.status_name AS goal_status, ugm.target_date, ugm.assigned_date,
              rt.remarks AS manager_feedback,
              CONCAT(TRIM(m.firstname),' ',TRIM(m.lastname)) AS feedback_given_by,
              r.remark_text AS employee_feedback,
              CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager
       FROM user_goal_mapping ugm
       JOIN user_table u      ON ugm.employee_id = u.employee_id
       JOIN designation d     ON u.designation_id = d.designation_id
       JOIN master_goals mg   ON ugm.goal_id = mg.goal_id
       LEFT JOIN status s     ON ugm.status_id = s.id
       LEFT JOIN remarks_table rt  ON ugm.goal_id = rt.goal_id        -- manager feedback text
       LEFT JOIN user_table m      ON rt.given_by = m.employee_id     -- person who gave the remark
       LEFT JOIN remarks_threads r ON r.goal_id = ugm.goal_id         -- employee self-assessment
       LEFT JOIN user_table mgr    ON u.reporting_manager = mgr.employee_id  -- reporting manager name
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
       -- Employee filter: AND u.employee_id = 'VT136'  OR  AND u.firstname LIKE '%Name%'
       -- Date filter   : AND ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)


    n) KRA BY MANAGER (use when user says "by manager", "manager-wise", "per manager", "under each manager"):
       -- Manager is the FIRST column; employees are listed under their manager
       -- Do NOT use GROUP BY unless user explicitly asks for a count/summary
       SELECT CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname),' (',mgr.employee_id,')') AS manager,
              CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              d.designation_name, u.stream AS department,
              mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
              s.status_name AS goal_status, ugm.target_date, ugm.assigned_date
       FROM user_goal_mapping ugm
       JOIN user_table u    ON ugm.employee_id = u.employee_id
       JOIN designation d   ON u.designation_id = d.designation_id
       JOIN master_goals mg ON ugm.goal_id = mg.goal_id
       LEFT JOIN status s   ON ugm.status_id = s.id
       LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
       ORDER BY manager, u.firstname

    o) KRA PENDING APPROVAL (use when user says "pending approval", "awaiting approval", "submitted for approval"):
       -- Filter: s.status_name LIKE '%Pending%'
       -- NEVER use approval_history for KRA goals; NEVER add approval_status/approved_by/approval_date columns
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              d.designation_name, u.stream AS department,
              mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
              s.status_name AS goal_status,
              CONCAT(TRIM(ab.firstname),' ',TRIM(ab.lastname),' (',ab.employee_id,')') AS assigned_by,
              CONCAT(TRIM(mgr.firstname),' ',TRIM(mgr.lastname)) AS reporting_manager,
              ugm.assigned_date, ugm.target_date
       FROM user_goal_mapping ugm
       JOIN user_table u    ON ugm.employee_id = u.employee_id
       JOIN designation d   ON u.designation_id = d.designation_id
       JOIN master_goals mg ON ugm.goal_id = mg.goal_id
       LEFT JOIN status s   ON ugm.status_id = s.id
       LEFT JOIN user_table ab  ON ugm.assigned_by = ab.employee_id
       LEFT JOIN user_table mgr ON u.reporting_manager = mgr.employee_id
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
         AND s.status_name LIKE '%Pending%'
       ORDER BY ugm.assigned_date DESC

    p) STREAM / DEPARTMENT EMPLOYEE LIST (use when user asks about employees "in a stream", "in a department", or "stream-wise"):
       ⚠ CRITICAL: user_table.stream stores a numeric tag_id — NEVER select it, NEVER compare it to a name.
       ALWAYS join the tags table. Display column MUST be t.tag AS stream, NEVER u.stream.
       -- For a named stream (e.g. "QA", "Dev"):
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              t.tag AS stream
       FROM user_table u
       LEFT JOIN tags t ON t.tag_id = u.stream
       WHERE t.tag LIKE '%<stream_name>%'
         AND u.is_active=1 AND u.is_delete=0
       ORDER BY u.firstname
       -- For aggregate count by stream:
       SELECT t.tag AS stream, COUNT(u.employee_id) AS employee_count
       FROM user_table u
       LEFT JOIN tags t ON t.tag_id = u.stream
       WHERE u.is_active=1 AND u.is_delete=0
       GROUP BY t.tag
       ORDER BY employee_count DESC

    r) REMARK COMPLIANCE REPORT (use when user asks about compliance %, remark completion rate, required vs filled):
       ⚠ DIFFERENT FROM GOAL COMPLETION (s.status_name = 'Completed')
       ⚠ Compliance here = whether REMARKS have been entered for each goal assignment
       ⚠ See Example 20 (stream-wise) and Example 21 (overall) for the exact pattern
       -- Stream-wise for a specific month:
       SELECT t.tag AS stream,
              COUNT(DISTINCT ugm.id) AS required_goals,
              COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS filled_remarks,
              COUNT(DISTINCT ugm.id) - COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) AS missing_remarks,
              ROUND(COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END) / NULLIF(COUNT(DISTINCT ugm.id), 0) * 100, 2) AS compliance_pct
       FROM user_goal_mapping ugm
       JOIN user_table u ON ugm.employee_id = u.employee_id
       JOIN designation d ON u.designation_id = d.designation_id
       LEFT JOIN tags t ON t.tag_id = u.stream
       LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1 AND d.is_active=1
       -- Add: AND MONTH(ugm.target_date)=N AND YEAR(ugm.target_date)=YYYY
       GROUP BY t.tag
       ORDER BY compliance_pct ASC
       -- For designation-wise: change GROUP BY t.tag → GROUP BY d.designation_name
       -- For overall (no GROUP BY): remove GROUP BY to get one aggregate row
       -- For teams below N%: add HAVING compliance_pct < N

    s) REMARKS AUTHORED BY A NAMED PERSON (use when "remarks written by", "feedback given by", "authored by"):
       ⚠ remarks_table.given_by = employee_id of the person who WROTE the remark (author)
       ⚠ remarks_table.user_id  = employee_id of the goal owner (person being reviewed)
       ⚠ NEVER filter on rt.user_id for author name search
       ⚠ See Example 25 for the exact JOIN pattern
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee_reviewed,
              mg.goal_desc AS goal_name, rt.remarks AS remark_text, rt.performance_rating,
              CONCAT(TRIM(author.firstname),' ',TRIM(author.lastname)) AS remark_given_by,
              rt.enteredDate AS remark_date
       FROM remarks_table rt
       JOIN user_table author ON rt.given_by = author.employee_id
                              AND author.firstname LIKE '%Name%' -- filter on AUTHOR here
       JOIN user_table u      ON rt.user_id = u.employee_id
       JOIN master_goals mg   ON rt.goal_id = mg.goal_id
       WHERE u.is_active=1 AND u.is_delete=0
       ORDER BY rt.enteredDate DESC

    t) GOAL DRILLDOWN WITH REMARK STATUS (use when "show goals with filled/missing status", "goal compliance for [name]"):
       ⚠ Per-goal remark status: CASE WHEN rt.id IS NULL THEN 'Missing' ELSE 'Filled' END AS remark_status
       ⚠ Category: LEFT JOIN goal_tag_category gtc ON ugm.goal_id = gtc.goal_id AND gtc.is_active=1
                   LEFT JOIN master_categories mc ON gtc.tag_cat_id = mc.category_id AND mc.is_active=1
       ⚠ Points: mg.weightage AS points
       ⚠ See Example 23 for the exact pattern
       SELECT CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee,
              mc.category_name, mg.goal_desc AS goal_name, ugm.goal_desc AS custom_goal,
              mg.weightage AS points, s.status_name AS goal_status, ugm.target_date,
              CASE WHEN rt.id IS NULL THEN 'Missing' ELSE 'Filled' END AS remark_status,
              rt.remarks AS manager_feedback
       FROM user_goal_mapping ugm
       JOIN user_table u ON ugm.employee_id = u.employee_id
       JOIN master_goals mg ON ugm.goal_id = mg.goal_id
       LEFT JOIN goal_tag_category gtc ON ugm.goal_id = gtc.goal_id AND gtc.is_active=1
       LEFT JOIN master_categories mc ON gtc.tag_cat_id = mc.category_id AND mc.is_active=1
       LEFT JOIN status s ON ugm.status_id = s.id
       LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id
       WHERE u.is_active=1 AND u.is_delete=0 AND ugm.isDelete=0 AND ugm.isactive=1
       -- Employee filter: AND u.firstname LIKE '%Name%'

    i) RNR NOMINATIONS:
       SELECT CONCAT(TRIM(n.firstname),' ',TRIM(n.lastname),' (',n.employee_id,')') AS nominee,
              CONCAT(TRIM(nr.firstname),' ',TRIM(nr.lastname)) AS nominated_by,
              cat.category_name, rc.cycle_name, rn.current_status, rn.nomination_date
       FROM rnr_nominations rn
       JOIN user_table n        ON rn.nominee_employee_id = n.employee_id
       JOIN user_table nr       ON rn.nominated_by_employee_id = nr.employee_id
       JOIN rnr_categories cat  ON rn.category_id = cat.category_id
       JOIN rnr_cycles rc       ON rn.cycle_id = rc.cycle_id
       WHERE rc.is_deleted=0 AND cat.is_delete=0 AND cat.is_active=1

16. STREAM / DEPARTMENT COLUMN RULE (CRITICAL — applies to every query involving streams or departments):
    user_table.stream stores a numeric foreign key (tag_id), NOT a readable stream name.
    Violating this rule returns raw IDs in the output or matches no rows at all.

    MANDATORY JOIN for any stream/department query:
      user_table u LEFT JOIN tags t ON t.tag_id = u.stream

    ┌──────────────────────────────────────────────┬─────────────────────────────────────────────────┐
    │ WRONG (never do this)                        │ CORRECT                                         │
    ├──────────────────────────────────────────────┼─────────────────────────────────────────────────┤
    │ SELECT u.stream                              │ SELECT t.tag AS stream                          │
    │ SELECT u.stream AS stream                    │   (after LEFT JOIN tags t ON t.tag_id = u.stream)│
    │ WHERE u.stream = 'QA'                        │ WHERE t.tag LIKE '%QA%'                         │
    │ GROUP BY u.stream  (for stream display)      │ GROUP BY t.tag                                  │
    └──────────────────────────────────────────────┴─────────────────────────────────────────────────┘

    SELECT RULE: NEVER include u.stream in the SELECT list for stream-related employee queries.
                 The only acceptable stream display column is t.tag AS stream.
                 u.stream is a numeric ID — selecting it returns meaningless numbers to the user.

    Trigger phrases: "in the QA stream", "Dev stream employees", "stream-wise", "by stream",
                     "department-wise", "employees in department", "how many per stream",
                     "employees in <any> stream", "stream report", "stream-based report".

    Schema validation: before generating stream SQL, confirm user_table.stream, tags.tag_id,
    and tags.tag all exist in the schema. If any is missing, set sql_query to "" and ask for
    schema-grounded clarification.

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

FOLLOWUP_INSTRUCTION_TEMPLATE = """\

════════════════════════════════════════
 FOLLOW-UP MODIFICATION — BASE QUERY IS:
════════════════════════════════════════
{base_sql}

════════════════════════════════════════
 MODIFICATION RULES (read carefully)
════════════════════════════════════════
  1. START from the BASE QUERY above — do NOT write a new query from scratch.
  2. ADD / REMOVE / CHANGE only what the user explicitly asked for.
  3. Keep ALL existing SELECT columns, JOINs, and WHERE guards unchanged.
  4. Name filter → append: AND (u.firstname LIKE '%Name%' OR u.lastname LIKE '%Name%')
     Employee ID filter → append: AND u.employee_id = 'ID'
  5. Column add → append the column to SELECT (use exact name from DATABASE SCHEMA).
  6. Column remove → drop only that column from SELECT.
  7. Use EXACT column names from the DATABASE SCHEMA (Rule 12).
"""

FOLLOWUP_INSTRUCTION_FALLBACK = """\

════════════════════════════════════════
 FOLLOW-UP MODIFICATION (READ CAREFULLY)
════════════════════════════════════════
The user is MODIFYING a previous report.
The base query is the most recent [SQL used: ...] in the CONVERSATION CONTEXT above.

  1. Do NOT rewrite from scratch — start from that base SQL.
  2. ADD / REMOVE / CHANGE only what the user explicitly asked for.
  3. Keep ALL existing SELECT columns, JOINs, and WHERE guards unchanged.
  4. Use EXACT column names from the DATABASE SCHEMA (Rule 12).
"""

ACTIVE_CONTEXT_SECTION = """
════════════════════════════════════════
 ACTIVE REPORT CONTEXT
════════════════════════════════════════
This is the structured context of the report currently being refined:
  Report type : {report_type}
  Last query  : {last_query}
  Base SQL    : {generated_sql}
"""

RETRY_SUFFIX_TEMPLATE = """
════════════════════════════════════════
 PREVIOUS ATTEMPT FAILED — FIX REQUIRED
════════════════════════════════════════
{retry_feedback}

Fix ONLY the specific error above. Use EXACT column and table names from the DATABASE SCHEMA section.
If the error says "Unknown column 'X'", find the correct name for X in the schema (e.g. email → e_mail).
Do NOT drop columns — correct their names.
"""

# Detects "Unknown column 'tbl.col'" in MySQL error messages
_UNKNOWN_COLUMN_RE = re.compile(r"Unknown column '([^']+)'", re.IGNORECASE)


def extract_column_hint(error: str) -> str:
    """Return a targeted hint when the error is a missing-column error."""
    m = _UNKNOWN_COLUMN_RE.search(error)
    if not m:
        return ""
    bad_ref = m.group(1)
    bad_col = bad_ref.split(".")[-1]
    return (
        f"\n⚠ Column name fix: '{bad_ref}' does not exist."
        f"\n  Look up '{bad_col}' in the DATABASE SCHEMA section above and use the exact name shown there."
        f"\n  Common corrections: email → e_mail | first_name → firstname | last_name → lastname"
        f"\n  Do NOT remove the column — correct its name."
    )


# Extracts the most recent SQL from "[SQL used: ...]" lines in memory context
_SQL_USED_RE = re.compile(r"\[SQL used:\s*(.*?)\]", re.DOTALL)


def _extract_last_sql(memory_context: str) -> str:
    matches = _SQL_USED_RE.findall(memory_context)
    return matches[-1].strip() if matches else ""


class PromptBuilder:

    def build_prompt(
        self,
        user_query: str,
        schema_string: str,
        memory_context: str = "",
        retry_feedback: str = "",
        relationship_type: str = "new_request",
        active_report_context: dict = None,
    ) -> str:
        memory_section = memory_context if memory_context else "No prior conversation."

        # Follow-up determined exclusively by the relationship classifier.
        is_followup = (relationship_type == "followup")

        system = SYSTEM_PROMPT_TEMPLATE.format(
            few_shot=_STATIC_FEW_SHOT,
            schema=schema_string.replace("{", "{{").replace("}", "}}"),
            memory_context=memory_section.replace("{", "{{").replace("}", "}}"),
        )

        # Build follow-up section: use active_report_context when available (structured),
        # then inject the actual base SQL so the LLM never has to "find" it in memory.
        followup_section = ""
        if is_followup:
            ctx = active_report_context or {}
            if ctx.get("generated_sql") or ctx.get("last_query"):
                followup_section = ACTIVE_CONTEXT_SECTION.format(
                    report_type=ctx.get("report_type", "previous report"),
                    last_query=ctx.get("last_query", "").replace("{", "{{").replace("}", "}}"),
                    generated_sql=ctx.get("generated_sql", "").replace("{", "{{").replace("}", "}}"),
                )
                base_sql = ctx.get("generated_sql", "")
            else:
                base_sql = _extract_last_sql(memory_section)

            if base_sql:
                followup_section += FOLLOWUP_INSTRUCTION_TEMPLATE.format(
                    base_sql=base_sql.replace("{", "{{").replace("}", "}}")
                )
            else:
                followup_section += FOLLOWUP_INSTRUCTION_FALLBACK

        retry_section = ""
        if retry_feedback:
            retry_section = RETRY_SUFFIX_TEMPLATE.format(
                retry_feedback=retry_feedback.replace("{", "{{").replace("}", "}}")
            )

        # Last-line override: non-compliance without grouping → always employee list
        query_hint = ""
        if _NON_COMPLIANCE_RE.search(user_query) and not _GROUPING_RE.search(user_query):
            query_hint = (
                "\n⚠ INSTRUCTION FOR THIS QUERY: The user asked for a non-compliance report "
                "WITHOUT any grouping keyword (no 'department-wise', 'by department', 'categorized', "
                "'summary', or 'count'). You MUST generate an EMPLOYEE-LEVEL LIST — "
                "individual rows per employee per goal. NO GROUP BY. NO COUNT. "
                "Use EXAMPLE 1 from APPROVED REFERENCE SQL EXAMPLES as your exact template.\n"
            )

        # Last-line override: "goals not filled / missing remarks" → rt.id IS NULL approach
        if _GOALS_NOT_FILLED_RE.search(user_query) or _GOALS_NOT_FILLED_GT0_RE.search(user_query):
            query_hint += (
                "\n⚠ INSTRUCTION FOR THIS QUERY: The user wants goals/remarks that have NOT been submitted. "
                "MANDATORY pattern — use the remarks-existence check, NOT the status check:\n"
                "1. INNER JOIN user_goal_mapping ugm ON u.employee_id = ugm.employee_id (not LEFT JOIN)\n"
                "2. LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id\n"
                "3. WHERE ... AND rt.id IS NULL  ← this keeps only goals with NO remark entry\n"
                "4. COUNT(DISTINCT ugm.id) AS goals_not_filled\n"
                "5. If a month/year is specified, add: AND MONTH(ugm.target_date)=N AND YEAR(ugm.target_date)=YYYY\n"
                "6. GROUP BY u.employee_id, u.firstname, u.lastname, d.designation_name, t.tag\n"
                "7. ORDER BY goals_not_filled DESC, employee\n"
                "8. NO HAVING needed — WHERE rt.id IS NULL automatically excludes employees with 0 missing goals.\n"
                "NEVER use: CASE WHEN s.status_name != 'Completed' for this query type.\n"
                "NEVER use: LEFT JOIN user_goal_mapping — must be INNER JOIN.\n"
                "Use EXAMPLE 18 (with date) or EXAMPLE 19 (no date) from APPROVED REFERENCE SQL EXAMPLES.\n"
            )

        # Last-line override: remark compliance % / completion rate → filled/required formula
        if _COMPLIANCE_METRICS_RE.search(user_query):
            query_hint += (
                "\n⚠ MANDATORY INSTRUCTION FOR THIS QUERY: REMARK-BASED compliance/completion rate.\n"
                "FORBIDDEN: Do NOT start FROM user_table without joining user_goal_mapping.\n"
                "FORBIDDEN: Do NOT compute COUNT(remarks) / COUNT(employees) — this is always WRONG.\n"
                "FORBIDDEN: Do NOT use s.status_name = 'Completed' for compliance percentage.\n"
                "MANDATORY JOIN CHAIN:\n"
                "  FROM user_goal_mapping ugm\n"
                "  JOIN user_table u ON ugm.employee_id = u.employee_id\n"
                "  LEFT JOIN remarks_table rt ON ugm.goal_id = rt.goal_id\n"
                "MANDATORY FORMULA:\n"
                "  required_goals  = COUNT(DISTINCT ugm.id)\n"
                "  filled_remarks  = COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END)\n"
                "  missing_remarks = COUNT(DISTINCT ugm.id) - COUNT(DISTINCT CASE WHEN rt.id IS NOT NULL THEN ugm.id END)\n"
                "  compliance_pct  = ROUND(filled_remarks / NULLIF(required_goals, 0) * 100, 2)\n"
                "GROUP BY the dimension asked (stream→t.tag, designation→d.designation_name, overall→no GROUP BY).\n"
                "For streams BELOW a % threshold: add HAVING compliance_pct < <threshold>.\n"
                "Use Example 20 (stream-wise) or Example 21 (overall) from APPROVED REFERENCE SQL EXAMPLES as the EXACT template.\n"
            )

        # Last-line override: trend / monthly time-series → GROUP BY month, count goals not employees
        if _TREND_MONTHLY_RE.search(user_query):
            query_hint += (
                "\n⚠ INSTRUCTION FOR THIS QUERY: The user wants a TIME-SERIES / TREND report. "
                "MANDATORY pattern:\n"
                "  GROUP BY DATE_FORMAT(ugm.target_date, '%Y-%m') AS month\n"
                "  ORDER BY month ASC\n"
                "  Include: month, required_goals, filled_remarks, missing_remarks, completion_pct\n"
                "  Count GOALS (COUNT(DISTINCT ugm.id)), NEVER employees for trend queries.\n"
                "  For quarter-over-quarter: GROUP BY YEAR(ugm.target_date), QUARTER(ugm.target_date) AS quarter\n"
                "  NEVER GROUP BY employee for a trend query.\n"
                "Use Example 24 from APPROVED REFERENCE SQL EXAMPLES as your template.\n"
            )

        # Last-line override: "how many employees" → scalar COUNT, never a list
        if _HOW_MANY_EMPLOYEE_RE.search(user_query):
            query_hint += (
                "\n⚠ INSTRUCTION FOR THIS QUERY: The user asked HOW MANY EMPLOYEES satisfy a condition. "
                "You MUST return a SINGLE scalar count — one row, one column named employee_count. "
                "MANDATORY pattern:\n"
                "  SELECT COUNT(*) AS employee_count\n"
                "  FROM (\n"
                "      SELECT u.employee_id\n"
                "      FROM user_table u\n"
                "      ... (JOINs and filters for the condition)\n"
                "      GROUP BY u.employee_id\n"
                "      HAVING <condition>\n"
                "  ) AS qualifying_employees\n"
                "NEVER return a list of employee names. "
                "NEVER use SELECT employee, ..., COUNT(...) GROUP BY employee_id for this query. "
                "Use EXAMPLE 16 from APPROVED REFERENCE SQL EXAMPLES as your exact template.\n"
            )

        # Last-line override: multi-stream filter (Dev or Dev-Data) → t.tag IN (...)
        if _MULTI_STREAM_RE.search(user_query):
            query_hint += (
                "\n⚠ INSTRUCTION FOR THIS QUERY: The user specified MULTIPLE STREAM NAMES. "
                "Use t.tag IN ('Stream1', 'Stream2') — NEVER multiple LIKE conditions for exact stream lists. "
                "Still requires LEFT JOIN tags t ON t.tag_id = u.stream. "
                "Use Example 26 from APPROVED REFERENCE SQL EXAMPLES as your template.\n"
            )

        prompt = f"{system}{followup_section}\n{retry_section}{query_hint}\nUSER QUERY: {user_query}"
        logger.debug(
            "Built prompt (%d chars) relationship=%s followup=%s retry=%s",
            len(prompt), relationship_type, is_followup, bool(retry_feedback),
        )
        return prompt


prompt_builder = PromptBuilder()
