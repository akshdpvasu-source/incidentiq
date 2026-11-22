-- IncidentIQ schema (SQLite). Safe to run many times (IF NOT EXISTS everywhere).
-- Addition to the brief: batches.file_size (Phase 2 asks to record it).

CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, source_name TEXT, source_type TEXT,
  ingested_at TEXT, input_rows INTEGER, loaded_rows INTEGER,
  rejected_rows INTEGER, status TEXT, file_size INTEGER);

CREATE TABLE IF NOT EXISTS teams (
  team_id INTEGER PRIMARY KEY AUTOINCREMENT, team_name TEXT UNIQUE, region TEXT);

CREATE TABLE IF NOT EXISTS agents (
  agent_id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT UNIQUE,
  team_id INTEGER REFERENCES teams(team_id), experience_level TEXT);

CREATE TABLE IF NOT EXISTS categories (
  category_id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, subcategory TEXT,
  affected_service TEXT, UNIQUE(category, subcategory, affected_service));

CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY, batch_id TEXT REFERENCES batches(batch_id),
  category_id INTEGER REFERENCES categories(category_id),
  team_id INTEGER REFERENCES teams(team_id),
  agent_id INTEGER REFERENCES agents(agent_id),
  priority TEXT, status TEXT, opened_at TEXT, resolved_at TEXT,
  resolution_hours REAL, sla_target_hours REAL, sla_breached INTEGER,
  ticket_age_days REAL, opened_hour INTEGER, opened_weekday TEXT, opened_month TEXT,
  reopen_count INTEGER, short_description TEXT, description TEXT);

CREATE TABLE IF NOT EXISTS resolutions (
  resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id TEXT REFERENCES tickets(ticket_id),
  resolution_notes TEXT, resolution_code TEXT);

CREATE TABLE IF NOT EXISTS rejected_records (
  reject_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT REFERENCES batches(batch_id),
  raw_ticket_id TEXT, reason_code TEXT, raw_payload TEXT);

CREATE TABLE IF NOT EXISTS ai_query_log (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT, asked_at TEXT, question TEXT,
  generated_sql TEXT, validation_status TEXT, row_count INTEGER, answer_summary TEXT);

CREATE INDEX IF NOT EXISTS idx_tickets_opened ON tickets(opened_at);
CREATE INDEX IF NOT EXISTS idx_tickets_team ON tickets(team_id);
CREATE INDEX IF NOT EXISTS idx_tickets_cat ON tickets(category_id);
CREATE INDEX IF NOT EXISTS idx_resolutions_ticket ON resolutions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_rejected_batch ON rejected_records(batch_id);

-- Convenience view used by the 8 analytics queries (not exposed to the AI).
CREATE VIEW IF NOT EXISTS v_tickets AS
SELECT t.*, tm.team_name, tm.region, c.category, c.subcategory, c.affected_service,
       a.agent_name, a.experience_level
FROM tickets t
LEFT JOIN teams tm     ON tm.team_id = t.team_id
LEFT JOIN categories c ON c.category_id = t.category_id
LEFT JOIN agents a     ON a.agent_id = t.agent_id;
