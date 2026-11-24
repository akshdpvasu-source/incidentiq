# IncidentIQ - 5-minute demo script

**Before you start:** the database is empty, the API is running (`uvicorn src.api_server:app --port 8000`),
Streamlit is open on the **Pipeline** page, an LLM is configured (cloud key or Ollama), and
the CloudWatch screenshot is ready in another tab.

## 1. Problem (30 s)

> "IT teams handle thousands of incident tickets. The data arrives from several tools, is messy, and
> managers still can't answer simple questions like *which team misses SLAs* or *what broke last month*
> without an analyst. IncidentIQ cleans the data, proves nothing was lost, and lets anyone ask
> questions in English, safely."

## 2. Pipeline (60 s)

1. Click **Run full pipeline**. Three batches load: 20,000 file rows, the 200-row dirty file, 2,000 API rows.
2. Open the dirty-file tab of the cleaning report: **200 raw = 158 cleaned + 42 rejected**, reconciled = Yes.
3. Point at "Fixed by rule": misspelled categories (`netwrok`), three date formats and `High`/`1 - Critical`
   priorities were repaired, not dropped.
4. Scroll to **Batches**: every run gets an ID, and input = loaded + rejected. Click **Download all rejected records**.

## 3. Dashboard insights (60 s)

1. KPI cards: 22,158 tickets, 1,772 open, 15.9% breach, MTTR 17 h.
2. **SLA by team**: Infra-Team-B breaches about **2× more** than any other team (28% vs 14%).
3. **Peaks** heatmap: the dark cells are **Monday 9-11 AM**; Friday evenings light up after monthly releases.
4. **Services**: **VPN Gateway** flagged in October, about 4× the previous month.

## 4. Ask IncidentIQ (90 s)

1. Click **Why did SLA breaches rise last month?** Open **Generated SQL**: it is one read-only SELECT.
   Read the answer: it should name *possible factors* from the rows (e.g. VPN Gateway), not invented causes.
   Rehearse this one: small local models can pick the wrong months. If it misses, ask
   **Which affected service had the most tickets last month?** (answer: VPN Gateway).
2. Click **Which team has the highest SLA breach rate?** It returns Infra-Team-B, matching the dashboard.
3. Click **Delete all tickets**. The red **Blocked** badge appears. Say: "Even if the model wrote a DELETE,
   the connection is read-only and SQLite's authorizer refuses other tables."
4. Optional: open **Data Quality & Audit** → AI query log shows the blocked attempt.

## 5. Ticket Assistant (45 s)

1. Pick a recent open ticket (e.g. a VPN or database incident) → **Analyse ticket**.
2. Show the **Problem / Actions taken / Current status** summary (max 80 words).
3. Show the 5 similar past incidents with similarity scores and the **Suggested fix** citing their ticket IDs.

## 5b. Optional: live role-play, employee → agent (3-5 min)

For an interview where you want to show the help-desk loop instead of (or after) the dashboard tour, follow
**[docs/interview_roleplay.md](docs/interview_roleplay.md)**: upload `sample_data/demo_past_tickets.csv`, raise a
VPN ticket as an employee, then take it as an agent, use the similar cases and the AI chat, resolve it, and track it.

## 6. Cloud and close (15 s)

Show the S3 bucket (`raw/`, `processed/`, `rejected/`, `reports/`) and the CloudWatch log line from the
Lambda (file name, size, 200 rows).

> "Every number on screen comes from SQL, every AI query is checked and logged, and nothing is silently
> dropped. That is what makes the AI trustworthy enough for an operations team."

---

## 10 likely judge questions

1. **How do you know no data was lost?** Each batch stores input rows, loaded rows and rejected rows;
   the app and the tests check input = loaded + rejected. Rejected rows keep their original payload and reason.
2. **What stops the AI from deleting data?** Four layers: write-intent check, static SQL validation
   (one SELECT, approved tables, no comments or `;`), a read-only SQLite connection, and an authorizer
   that blocks other tables at engine level. 26 attack strings are tested.
3. **Can the AI make up numbers?** Numbers come only from query results. The explanation prompt forbids
   new numbers, and a grounding check flags any number not present in the rows.
4. **How accurate is text-to-SQL?** 16/20 (80%) on our benchmark with a free 3B local model; failures are
   listed in `docs/test_results.md`. A larger or cloud model should do better; the SQL is always shown so users can check it.
5. **Why synthetic data?** No access to real tickets and no privacy risk. We planted four known
   patterns so we can prove the analytics find them.
6. **How does similar-incident search work?** TF-IDF over ticket text with cosine similarity; the LLM may
   only use the top-5 retrieved resolutions and must cite their IDs. Below 0.2 similarity it says so.
7. **What happens if the same file is loaded twice?** A new batch is created and tickets are upserted
   (latest extract wins), so there are no duplicates.
8. **Does it need the cloud?** No. S3 and Lambda are optional; without credentials the app shows
   "S3 disabled" and works fully locally. The LLM can be local (Ollama).
9. **How would this scale?** Swap SQLite for PostgreSQL, schedule the pipeline with Airflow, store
   embeddings in a vector database, and keep the same guard in front of the LLM.
10. **Why are recent breach rates so high?** Tickets still open past their SLA count as breached, so the
    newest months include the open backlog. Resolved-only breach rate is flat (~9%); ask IncidentIQ for "breach rate per month for resolved tickets only" to show it.
11. **How does the agent's AI chat stay grounded?** It only sees the ticket and the top-5 retrieved past
    cases, must cite ticket ids, every cited id is checked against what was retrieved, and advice from outside
    the cases must be labelled. It has no tools, so it cannot change anything.
12. **Can the AI read employee names and emails?** No. They are stored in `portal_requests`, which is not on
    the text-to-SQL allow-list; tests prove both the static check and the database-level block refuse it.
13. **Does the system learn from fixes?** Yes: a resolved ticket's notes are indexed immediately, so the next
    similar ticket can cite it. A human writes the resolution; the AI never closes tickets.
