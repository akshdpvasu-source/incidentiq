-- Q6. Month-over-month volume and SLA breach trend using LAG().
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
),
monthly AS (
  SELECT opened_month AS month, COUNT(*) AS tickets, SUM(sla_breached) AS breached,
         ROUND(100.0 * SUM(sla_breached) / COUNT(*), 2) AS breach_rate_pct
  FROM base
  GROUP BY opened_month
)
SELECT month, tickets,
       LAG(tickets) OVER w AS prev_tickets,
       tickets - LAG(tickets) OVER w AS volume_change,
       ROUND(100.0 * (tickets - LAG(tickets) OVER w) / LAG(tickets) OVER w, 2) AS volume_change_pct,
       breached, breach_rate_pct,
       ROUND(breach_rate_pct - LAG(breach_rate_pct) OVER w, 2) AS breach_rate_change
FROM monthly
WINDOW w AS (ORDER BY month)
ORDER BY month;
