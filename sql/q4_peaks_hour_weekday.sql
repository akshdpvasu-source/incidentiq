-- Q4. Incident peaks: tickets opened per weekday x hour of day (heatmap input).
WITH base AS (   -- filtered tickets joined to team / category / agent (see db/schema.sql)
  SELECT * FROM v_tickets
  WHERE (:start_date IS NULL OR opened_at >= :start_date)
    AND (:end_date   IS NULL OR opened_at <  :end_date)
    AND (:team       IS NULL OR team_name = :team)
    AND (:priority   IS NULL OR priority  = :priority)
    AND (:category   IS NULL OR category  = :category)
)
SELECT opened_weekday AS weekday,
       CASE opened_weekday WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
            WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END AS weekday_num,
       opened_hour AS hour,
       COUNT(*) AS tickets
FROM base
GROUP BY opened_weekday, opened_hour
ORDER BY weekday_num, hour;
