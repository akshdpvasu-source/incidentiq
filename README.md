# IncidentIQ - AI-powered IT incident intelligence

IncidentIQ turns raw IT incident tickets into trusted analytics and a safe AI assistant.
It is an end-to-end data pipeline built for the HCL Hackathon capstone:

1. **Ingestion**: CSV files and a paginated REST API (FastAPI mock), one audited batch per run.
2. **Transformation**: 10 counted cleaning rules; every row is either loaded or rejected with a reason.
3. **Storage**: SQLite (star-style schema) plus optional Amazon S3 and Lambda/CloudWatch.
4. **SQL analytics**: 8 tested queries (CTEs, `LAG`, `ROW_NUMBER`, `RANK`).
5. **GenAI**: text-to-SQL chat behind a SQL guard, ticket summaries, similar-incident fix suggestions.

Everything is shown in a Streamlit dashboard you can demo in 5 minutes. **All data is synthetic**
(Faker + templates); no real people or companies.

Architecture: see the Mermaid diagram in [docs/architecture.md](docs/architecture.md).

## Features

| Page | What you can do |
|---|---|
| Pipeline | Generate data, ingest a file or the API, clean, load, see the batch audit and download rejects |
| Dashboard | KPI cards, filters (dates, team, priority, category), charts for all 8 queries, AI weekly report |
| Ask IncidentIQ | Ask questions in English; see the answer, the SQL, the rows and a Valid/Blocked/Failed badge |
| Ticket Assistant | 3-part summary of a ticket, top-5 similar past incidents, a suggested fix citing ticket IDs |
| Data Quality & Audit | Rejected reasons chart, reconciliation per batch, log of every AI question |
| Cloud | S3 status, upload outputs, links to objects (optional) |

## Setup (Windows, macOS or Linux, Python 3.10+)

```bash
cd incidentiq
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Then choose **one** AI option in `.env`:

- **Cloud LLM (best answers):** set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...`
  (or `openai` / `gemini` with their key).
- **Free and local:** install [Ollama](https://ollama.com), then run `ollama pull llama3.2`.
  IncidentIQ uses it automatically when no key is set. A bigger model (e.g. `qwen2.5-coder:7b`,
  set `OLLAMA_MODEL`) gives better SQL.

Without either, everything except the AI explanations still works (summaries and fixes fall back
to plain extracts and are clearly labelled).

## Run order

```bash
python src/generate_data.py                      # 1. synthetic data (about 3 s)
uvicorn src.api_server:app --port 8000           # 2. mock API, in a second terminal
streamlit run app/streamlit_app.py               # 3. dashboard -> Pipeline -> Run full pipeline
```

If port 8000 is busy, start the API with `--port 8765` and set `API_URL=http://127.0.0.1:8765`
in `.env`. The Pipeline page also has a **Start mock API** button.

Command-line alternatives:

```bash
python src/pipeline.py                 # ingest + clean + load (main file, dirty file, API if up)
python src/analytics.py                # print all 8 queries
python tests/run_benchmark.py          # 20-question AI benchmark
pytest -q                              # 102 tests
```

## Project layout

```
src/        config, generator, API, ingestion, transform, loader, analytics, S3, pipeline
src/ai/     llm_client, sql_guard, text_to_sql, summarizer, similar_incidents, report
sql/        q1..q8 + kpi_summary (all share the same filter block)
app/        streamlit_app.py + views/ (one file per page)
tests/      pytest suites, benchmark questions and runner
lambda/     optional S3-triggered Lambda
docs/       architecture, planted patterns, AWS setup, test results
```

## Key results (see `docs/test_results.md`)

- 22,158 tickets loaded; every batch reconciles (dirty file: 200 = 158 + 42).
- 8 queries match hand-calculated answers; planted patterns A-D are rediscovered.
- 102 automated tests pass on pandas 2 and pandas 3.
- AI benchmark with free local llama3.2 3B: **16/20 = 80%**; all attacks blocked and logged.

## Screenshots

Add your own before the demo (Pipeline report, Dashboard peaks heatmap, a Blocked question,
Ticket Assistant, CloudWatch log):

| Pipeline | Dashboard | Ask IncidentIQ | Ticket Assistant |
|---|---|---|---|
| `docs/screenshots/pipeline.png` | `docs/screenshots/dashboard.png` | `docs/screenshots/ask.png` | `docs/screenshots/assistant.png` |

## Limitations and honest disclaimers

- The data is synthetic with deliberately planted patterns; real ticket data is messier.
- Breach rates for the latest months are inflated by still-open tickets (see `docs/planted_patterns.md`).
- The LLM can write wrong-but-safe SQL. The guard guarantees safety, not correctness; always check
  the SQL shown under each answer. Accuracy with cloud models was not measured on the build machine.
- "Why" answers describe *possible factors* from the returned rows, not proven causes.
- `response_hours` is always empty: the source data has no first-response timestamp.
- The grounding check is a heuristic: it flags numbers in an explanation that are not in the rows.
- SQLite is single-user; a real deployment would use PostgreSQL and a scheduler (e.g. Airflow).
- S3/Lambda were unit-tested with fakes but not run against a real AWS account during the build.
