-- Q8. Agent workload vs resolution speed, ranked within each team with RANK().
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
),
agent_stats AS (
  SELECT team_name, agent_name,
         COUNT(*) AS tickets_handled,
         SUM(CASE WHEN resolution_hours IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
         ROUND(AVG(resolution_hours), 2) AS avg_resolution_hours,
         ROUND(100.0 * SUM(sla_breached) / COUNT(*), 2) AS breach_rate_pct
  FROM base
  WHERE agent_name IS NOT NULL
  GROUP BY team_name, agent_name
)
SELECT *,
       RANK() OVER (PARTITION BY team_name ORDER BY tickets_handled DESC) AS workload_rank_in_team,
       RANK() OVER (PARTITION BY team_name ORDER BY avg_resolution_hours ASC) AS speed_rank_in_team
FROM agent_stats
ORDER BY team_name, workload_rank_in_team, agent_name;
