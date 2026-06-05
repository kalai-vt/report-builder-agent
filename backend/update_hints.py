import json

with open('data/kra_schema_llm_context.json') as f:
    schema = json.load(f)

hints = {
    "user_table": (
        "Primary employee master. Always JOIN this when employee name, email, stream, designation, or manager is needed. "
        "ALWAYS apply: is_active = 1 AND is_delete = 0. "
        "Key non-standard columns: e_mail (not email), firstname/lastname (not first_name/last_name), "
        "reporting_manager (VARCHAR stores manager employee_id - NOT manager_id). "
        "Manager JOIN: LEFT JOIN user_table m ON u.reporting_manager = m.employee_id. "
        "Employee display: CONCAT(TRIM(u.firstname),' ',TRIM(u.lastname),' (',u.employee_id,')') AS employee."
    ),
    "user_goal_mapping": (
        "PRIMARY table for ALL KRA/goal reports. Use this - NOT goal_history - for current active goals. "
        "ALWAYS apply: isDelete = 0 AND isactive = 1. "
        "Standard KRA report JOIN chain: "
        "FROM user_goal_mapping ugm "
        "JOIN user_table u ON ugm.employee_id = u.employee_id "
        "JOIN master_goals mg ON ugm.goal_id = mg.goal_id "
        "LEFT JOIN status s ON ugm.status_id = s.id. "
        "Date filter for past N years: ugm.target_date >= DATE_SUB(CURDATE(), INTERVAL N YEAR). "
        "DO NOT use goal_history for current goal reports."
    ),
    "master_goals": (
        "Goal definition/description table. Always JOIN with user_goal_mapping: "
        "JOIN master_goals mg ON ugm.goal_id = mg.goal_id. "
        "Key columns: goal_desc (goal name/description), weightage, target_date (master target date), is_required."
    ),
    "goal_history": (
        "Tracks HISTORICAL CHANGES to goal dates only - NOT current goal state. "
        "Do NOT use this for KRA reports or goal status reports. "
        "Only use when the user explicitly asks about date change history or audit trail of goal modifications."
    ),
    "status": (
        "Lookup table for goal status names. "
        "JOIN: LEFT JOIN status s ON ugm.status_id = s.id. "
        "Use s.status_name for the human-readable status. "
        "Key columns: id (int PK), status_name (varchar)."
    ),
    "designation": (
        "Designation/job-title lookup. JOIN: JOIN designation d ON u.designation_id = d.designation_id. "
        "ALWAYS apply: is_active = 1. "
        "Key columns: designation_name, designation_level (numeric hierarchy level)."
    ),
    "remarks_table": (
        "Stores performance ratings and manager remarks on employee goals. "
        "Use for: performance rating reports, appraisal remarks, rating summary. "
        "JOIN chain: FROM remarks_table rt JOIN user_table u ON rt.user_id = u.employee_id "
        "JOIN master_goals mg ON rt.goal_id = mg.goal_id. "
        "Key columns: performance_rating (numeric), remarks (text), remark_year, remark_month, given_by (who gave the rating)."
    ),
    "remarks_threads": (
        "Threaded comments/discussions on goals. Use for: feedback threads, goal comment history. "
        "JOIN: FROM remarks_threads rt JOIN user_table u ON rt.user_id = u.employee_id. "
        "Key columns: remark_text, remark_type, status, by_lead, is_agree, is_approve."
    ),
    "skills": (
        "Employee skills. Use for: skill reports, proficiency listings, skill gaps. "
        "ALWAYS apply: is_deleted = 0. "
        "JOIN: FROM skills sk JOIN user_table u ON sk.employee_id = u.employee_id. "
        "Key columns: skill_name, proficiency_level, months_of_experience, status."
    ),
    "certificates": (
        "Employee certification requests and completions. Use for: certification reports, pending/approved certs. "
        "ALWAYS apply: is_deleted = 0. "
        "JOIN: FROM certificates c JOIN user_table u ON c.employee_id = u.employee_id. "
        "status column is a direct string on this table (not FK to status table). "
        "Key columns: certificate_name, course, platform, status, issued_at, expected_completion_date."
    ),
    "certification_approvals": (
        "Approval records for certification requests. Use for: certification approval status/history. "
        "ALWAYS apply: is_deleted = 0. "
        "JOIN: FROM certification_approvals ca JOIN certificates c ON ca.cid = c.cid "
        "JOIN user_table u ON c.employee_id = u.employee_id. "
        "Key columns: phase, level, action, remarks, acted_at."
    ),
    "certification_completion": (
        "Completion records for certifications. Use for: completed certification details. "
        "JOIN: FROM certification_completion cc JOIN certificates c ON cc.certification_request_id = c.cid. "
        "Key columns: actual_completion_date, certification_link, current_status."
    ),
    "user_badges": (
        "Badges awarded to employees. Use for: badge reports, recognition lists, awarded badges. "
        "ALWAYS apply: is_deleted = 0. "
        "JOIN chain: FROM user_badges ub JOIN user_table u ON ub.employee_id = u.employee_id "
        "JOIN badge_master bm ON ub.badge_master_id = bm.badge_master_id. "
        "Key columns: status, created_at (award date), highlighted_skill, description."
    ),
    "badge_master": (
        "Badge definitions/types. ALWAYS apply: is_deleted = 0. "
        "JOIN: JOIN badge_master bm ON ub.badge_master_id = bm.badge_master_id. "
        "Key columns: badge_name, badge_description, weightage, approval_level."
    ),
    "approval_history": (
        "Approval actions for badges, skills, and certifications. "
        "ALWAYS apply: is_deleted = 0. "
        "category column indicates type: 'skill', 'badge', 'certification'. "
        "JOIN user_table u ON approval_history.approver_id = u.employee_id for approver details. "
        "Key columns: category, status, notes, created_at, approval_level."
    ),
    "user_feedback": (
        "Feedback given to/about employees. Use for: feedback reports, feedback history. "
        "ALWAYS apply: isactive = 1. "
        "JOIN: FROM user_feedback uf JOIN user_table u ON uf.employee_id = u.employee_id. "
        "Key columns: feedback_desc, feedbackGivenBy (employee_id of giver), feedback_date, feedback_year, likes."
    ),
    "rnr_nominations": (
        "Rewards and Recognition nominations. Use for: RnR reports, nomination status, winner lists. "
        "JOIN chain: FROM rnr_nominations rn "
        "JOIN rnr_cycles rc ON rn.cycle_id = rc.cycle_id "
        "JOIN rnr_categories cat ON rn.category_id = cat.category_id "
        "JOIN user_table nominee ON rn.nominee_employee_id = nominee.employee_id "
        "JOIN user_table nominator ON rn.nominated_by_employee_id = nominator.employee_id. "
        "Key columns: current_status, final_outcome, nomination_date, justification."
    ),
    "rnr_cycles": (
        "RnR award cycles (quarters/years). ALWAYS apply: is_deleted = 0. "
        "JOIN: JOIN rnr_cycles rc ON rn.cycle_id = rc.cycle_id. "
        "Key columns: cycle_name, cycle_year, cycle_sequence, status, nomination_start_date, nomination_end_date."
    ),
    "rnr_categories": (
        "RnR award categories. ALWAYS apply: is_delete = 0 AND is_active = 1. "
        "JOIN: JOIN rnr_categories cat ON rn.category_id = cat.category_id. "
        "Key columns: category_name, category_description."
    ),
    "recommendations": (
        "Peer/manager recommendations. Use for: recommendation reports. "
        "ALWAYS apply: is_deleted = 0. "
        "JOIN: FROM recommendations r "
        "JOIN user_table recommender ON r.recommended_by = recommender.employee_id "
        "JOIN user_table recipient ON r.recommended_to = recipient.employee_id. "
        "Key columns: position, recommendation_text, status, created_at."
    ),
    "notification_bell": (
        "User notifications. Use for: notification reports, unread notification counts. "
        "JOIN: FROM notification_bell nb JOIN user_table u ON nb.user_id = u.employee_id. "
        "Key columns: type, title, message, is_read, created_at."
    ),
    "remark_approvals": (
        "Approval actions on performance remarks. Use for: remark approval status. "
        "JOIN: FROM remark_approvals ra JOIN remarks_table rt ON ra.remark_id = rt.id "
        "JOIN user_table u ON ra.approver_id = u.employee_id. "
        "Key columns: action, rejection_reason, action_at."
    ),
    "disagree_remarks_master": (
        "Records of employee disagreements with appraisal remarks. "
        "JOIN: FROM disagree_remarks_master drm JOIN remarks_table rt ON drm.remark_id = rt.id. "
        "Key columns: is_agree, disagree_comments, conflict_resolved_comment, disagree_date."
    ),
    "master_categories": (
        "Goal/tag category master. ALWAYS apply: is_active = 1. "
        "Key columns: category_name."
    ),
    "tags": (
        "Tags for goals. ALWAYS apply: is_active = 1. "
        "Key columns: tag."
    ),
}

updated = 0
for table in schema['tables']:
    name = table['table_name']
    if name in hints:
        table['llm_usage_hint'] = hints[name]
        updated += 1

with open('data/kra_schema_llm_context.json', 'w') as f:
    json.dump(schema, f, indent=2)
print('Done. Updated', updated, 'table hints.')
