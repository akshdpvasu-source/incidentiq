# IncidentIQ - AI-powered IT incident intelligence

IncidentIQ turns raw IT incident tickets into trusted analytics and a safe AI assistant.
It is an end-to-end data pipeline built for the HCL Hackathon capstone:

1. **Ingestion**: CSV files and a paginated REST API (FastAPI mock), one audited batch per run.
2. **Transformation**: 10 counted cleaning rules; every row is either loaded or rejected with a reason.
3. **Storage**: SQLite (star-style schema) plus optional Amazon S3 and Lambda/CloudWatch.
4. **SQL analytics**: 8 tested queries (CTEs, `LAG`, `ROW_NUMBER`, `RANK`).
5. **GenAI**: text-to-SQL chat behind a SQL guard, ticket summaries, similar-incident fix suggestions.
6. **Help-desk workflow**: employees raise tickets, agents handle them with similar solved cases and an
   AI copilot, and every resolution feeds the knowledge base (see below).

Everything is shown in a Streamlit dashboard you can demo in 5 minutes. **All data is synthetic**
(Faker + templates); no real people or companies.

Architecture: see the Mermaid diagram in [docs/architecture.md](docs/architecture.md).

## Features

| Page | What you can do |
|---|---|
| Pipeline | Generate data, **upload your own CSV**, ingest a file or the API, clean, load, see the batch audit and download rejects |
| Raise a Ticket (Employee) | Report a problem (category guessed from similar solved tickets if unsure), then track its status and fix |
| Agent Desk (IT Agent) | Queue of raised tickets; take one; instant similar solved cases; AI summary + suggested fix; **chat with the AI about that ticket**; resolve it |
| Dashboard | KPI cards, filters (dates, team, priority, category), charts for all 8 queries, AI weekly report |
| Ask IncidentIQ | Ask questions in English; see the answer, the SQL, the rows and a Valid/Blocked/Failed badge |
| Ticket Assistant | 3-part summary of a ticket, top-5 similar past incidents, a suggested fix citing ticket IDs |
| Data Quality & Audit | Rejected reasons chart, reconciliation per batch, log of every AI question |
| Cloud | S3 status, upload outputs, links to objects (optional) |

## Help-desk workflow: Raise a Ticket → Agent Desk

A complete loop on top of the analytics, built for role-play demos (`docs/interview_roleplay.md`):

1. **Load history.** Upload a ticket CSV on the Pipeline page (`sample_data/demo_past_tickets.csv` is ready to use:
   2,500 past tickets with descriptions and resolution notes) and click *Ingest, clean and load*.
2. **Employee raises a ticket.** The ticket is saved as `Open` with an id like `INC0900001`, routed to a team,
   and gets an SLA deadline from its urgency. If the category is "Not sure", it is guessed from the most similar
   solved tickets (TF-IDF vote, only when the match is strong enough; otherwise the employee is asked).
3. **Agent picks it up.** The Agent Desk lists portal tickets (urgent and oldest first). *Take this ticket*
   assigns it; the **AI suggestions** tab shows the 5 most similar solved tickets instantly and, on request, an
   AI summary and a suggested fix that cites ticket ids.
4. **Chat with the AI.** The *Chat with AI* tab answers follow-up questions about this one ticket. The model sees
   the ticket plus the retrieved past cases, must cite ticket ids (checked against what was retrieved), and must
   label advice that does not come from past tickets. It can only advise; it has no tools.
5. **Resolve.** The agent writes the resolution; time taken and SLA status are recorded. The resolution joins the
   knowledge base straight away, so the next similar ticket finds it.
6. **Employee tracks it.** *Track a ticket* shows status, who is handling it, and how it was fixed.

Portal tickets are stored in the same `tickets` table, so the dashboard and all 8 queries include them.
Requester names/emails (`portal_requests`) and the audit trail (`ticket_events`) are **not** on the text-to-SQL
allow-list, so the AI chat on the Ask page cannot read them. There is **no login**: this is a demo.

CSV upload expects at least `ticket_id, opened_at, priority, category`; the other columns of the generated files
(`description`, `resolution_notes`, `resolution_code`, ...) are optional but make similar-case search useful.

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

For the help-desk demo you do not need the API or the generator: start Streamlit, upload
`sample_data/demo_past_tickets.csv` on the Pipeline page, then use *Raise a Ticket* and *Agent Desk*.

If port 8000 is busy, start the API with `--port 8765` and set `API_URL=http://127.0.0.1:8765`
in `.env`. The Pipeline page also has a **Start mock API** button.

Command-line alternatives:

```bash
python src/pipeline.py                 # ingest + clean + load (main file, dirty file, API if up)
python src/analytics.py                # print all 8 queries
python tests/run_benchmark.py          # 20-question AI benchmark
pytest -q                              # 150 tests
```

## Project layout

```
src/        config, generator, API, ingestion, transform, loader, analytics, S3, pipeline, portal
src/ai/     llm_client, sql_guard, text_to_sql, summarizer, similar_incidents, report, agent_chat
sql/        q1..q8 + kpi_summary (all share the same filter block)
app/        streamlit_app.py + views/ (one file per page)
tests/      pytest suites, benchmark questions and runner
sample_data/ ready-made ticket CSV for the Pipeline upload (help-desk demo)
lambda/     optional S3-triggered Lambda
docs/       architecture, planted patterns, AWS setup, test results, interview role-play guide
```

## Key results (see `docs/test_results.md`)

- 22,158 tickets loaded; every batch reconciles (dirty file: 200 = 158 + 42).
- 8 queries match hand-calculated answers; planted patterns A-D are rediscovered.
- 150 automated tests pass on pandas 2.3 and pandas 3.0 (102 original + 48 for the help-desk workflow).
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
- The help-desk pages have **no authentication or roles**: anyone can open either page, and the "agent" is a
  picker. Tracking needs only a ticket number. Do not put real employee data in it.
- The category guess is a similarity vote, not a trained classifier, and the agent cannot correct it in the UI.
- The Agent Desk chat quality depends on the model and on how similar the past tickets are; with no LLM it falls
  back to listing the most similar past resolutions. The Gemini code path (including a fix for 2.5 Flash returning
  empty replies) is unit-tested with a fake HTTP layer but was not run against the live Gemini API on the build machine.
- Free cloud-LLM tiers have rate limits and typically weaker privacy terms; the ticket text is sent to the provider.
