-- Q2. SLA breach rate by team and priority.
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
)
SELECT team_name, priority,
       COUNT(*) AS tickets,
       SUM(sla_breached) AS breached,
       ROUND(100.0 * SUM(sla_breached) / COUNT(*), 2) AS breach_rate_pct
FROM base
GROUP BY team_name, priority
ORDER BY team_name, priority;
