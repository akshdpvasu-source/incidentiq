-- Q3. MTTR (mean time to resolve, hours) by category and month. Resolved tickets only.
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
)
SELECT category, opened_month AS month,
       COUNT(*) AS resolved_tickets,
       ROUND(AVG(resolution_hours), 2) AS mttr_hours
FROM base
WHERE resolution_hours IS NOT NULL
GROUP BY category, opened_month
ORDER BY month, category;
