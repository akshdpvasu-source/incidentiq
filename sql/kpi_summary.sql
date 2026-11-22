-- KPI cards for the dashboard: totals, open backlog, SLA breach %, MTTR.
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
)
SELECT COUNT(*) AS total_tickets,
       COALESCE(SUM(CASE WHEN status IN ('Open', 'In Progress') THEN 1 ELSE 0 END), 0) AS open_tickets,
       ROUND(100.0 * SUM(sla_breached) / NULLIF(COUNT(*), 0), 2) AS sla_breach_pct,
       ROUND(AVG(resolution_hours), 2) AS mttr_hours
FROM base;
