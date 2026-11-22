-- Q1. Top recurring issues: ticket count and share % per category/subcategory,
--     plus average resolution time (shows "Password reset" = most frequent + fastest).
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
)
SELECT category, subcategory,
       COUNT(*) AS ticket_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS share_pct,
       ROUND(AVG(resolution_hours), 2) AS avg_resolution_hours
FROM base
GROUP BY category, subcategory
ORDER BY ticket_count DESC, category, subcategory;
