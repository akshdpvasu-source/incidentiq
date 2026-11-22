-- Q5. Repeat-offender services: tickets per service per month vs the previous calendar month.
--     flagged = 1 when tickets >= :threshold x previous month AND tickets >= :min_tickets.
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
),
monthly AS (
  SELECT affected_service, category, opened_month AS month, COUNT(*) AS tickets
  FROM base
  GROUP BY affected_service, category, opened_month
),
compared AS (
  SELECT m.affected_service, m.category, m.month, m.tickets, p.tickets AS prev_tickets
  FROM monthly m
  JOIN monthly p
    ON p.affected_service = m.affected_service
   AND p.category = m.category
   AND p.month = strftime('%Y-%m', m.month || '-01', '-1 month')
)
SELECT affected_service, category, month, tickets, prev_tickets,
       ROUND(1.0 * tickets / prev_tickets, 2) AS growth_ratio,
       CASE WHEN tickets >= :threshold * prev_tickets AND tickets >= :min_tickets
            THEN 1 ELSE 0 END AS flagged
FROM compared
ORDER BY flagged DESC, growth_ratio DESC, tickets DESC, affected_service, month;
