-- Q7. Oldest open tickets at highest breach risk: top :top_n oldest per priority.
--     sla_used_pct = age in hours / SLA target (above 100 = already breached).
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
),
open_tickets AS (
  SELECT ticket_id, priority, status, team_name, category, affected_service, opened_at,
         ticket_age_days, sla_target_hours,
         ROUND(100.0 * ticket_age_days * 24 / sla_target_hours, 1) AS sla_used_pct
  FROM base
  WHERE status IN ('Open', 'In Progress')
),
ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY priority ORDER BY ticket_age_days DESC, ticket_id) AS age_rank_in_priority,
         RANK() OVER (ORDER BY priority, ticket_age_days DESC) AS risk_rank
  FROM open_tickets
)
SELECT * FROM ranked
WHERE age_rank_in_priority <= :top_n
ORDER BY risk_rank, ticket_id;
