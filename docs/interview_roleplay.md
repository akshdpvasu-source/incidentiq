# Interview role-play: company → employee → IT agent (about 5 minutes)

You play three people in one demo:

| Hat | What you do | Page |
|---|---|---|
| **The company** | Hand IncidentIQ its history of past tickets (a CSV) | Pipeline |
| **An employee** | Report a new problem | Raise a Ticket (Employee) |
| **An IT agent** | Pick up that ticket, get help from past cases and from the AI, fix it | Agent Desk (IT Agent) |

The point you are making: *past tickets are a knowledge base. IncidentIQ cleans them, finds similar
solved cases for a new ticket, and lets an AI help the agent, without the AI being able to change anything.*

## Before the interview (do this once, then rehearse twice)

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, paste your Gemini key after `GEMINI_API_KEY=` (the file already says
   `LLM_PROVIDER=gemini`). No key? It still works: summaries and chat fall back to the most similar
   past resolutions, labelled "offline fallback". Gemini's free tier has rate limits, so do not
   hammer it right before the interview.
3. `streamlit run app/streamlit_app.py`. The sidebar should say `gemini (key set)`.
4. **Reset between rehearsals:** stop Streamlit, delete `db/incidentiq.db`, start again. The database is
   created fresh and the sample CSV in `sample_data/` is never modified.

## The script

### 1. The company loads its history (60 s)

*Say:* "This is the company's ticket history: 2,500 past tickets exported from their help-desk tool."

1. **Pipeline** → *Upload your own CSV* → choose `sample_data/demo_past_tickets.csv`.
2. Click **Ingest, clean and load demo_past_tickets.csv**.
3. Point at the report: **2,500 raw = 2,500 cleaned + 0 rejected, reconciled = Yes**. The sidebar now shows
   2,500 tickets.

*Say:* "Every file gets a batch ID and I can prove no row was lost. For messy files, see the dirty-data demo."

### 2. The employee raises a ticket (60 s)

*Say:* "Now I am an employee. My VPN does not work from home."

1. **Raise a Ticket (Employee)**. Name: anything. Site: *Home office*. Urgency: *High*.
2. Title: `Cannot connect to VPN from home`
3. Description: `VPN connection fails with a timeout during authentication for remote users. Started this morning and a few colleagues have the same problem.`
4. Leave Category on **Not sure - let IncidentIQ decide** and click **Submit ticket**.
5. Point at the blue note: it guessed **VPN / Cannot connect** from similar solved tickets (no AI involved,
   just text similarity). Note the ticket number (e.g. `INC0900001`).

### 3. The agent handles it (2.5 min)

*Say:* "Now I switch roles. I am a service-desk agent opening my queue."

1. **Agent Desk (IT Agent)**. Pick who you are working as. The queue shows the new ticket with its SLA
   deadline. Click the row (the most urgent open ticket is opened automatically).
2. Click **Take this ticket**. Status becomes *In Progress* and it is recorded under your name.
3. **AI suggestions** tab: the **5 most similar solved tickets** are already there (instant, no AI call).
   Expect fixes such as *restarting the VPN concentrator and clearing stale sessions* and *renewing the
   expired client certificate*.
4. Click **Get AI summary + suggested fix**. The fix cites the ticket IDs it used.
5. **Chat with AI** tab: click *What is the quickest fix to try first?*, then type a follow-up such as
   `Draft a short, friendly reply to the user.` Show that answers cite past tickets and that advice not
   from past tickets is labelled *General advice (not from past tickets)*.
6. **Resolve** tab: click *Use the AI's suggested fix as a starting point*, edit it into something you would
   really do, keep the pre-selected resolution code, click **Resolve ticket**. A green banner shows the
   time taken and whether the SLA was met.

### 4. Close the loop (45 s)

1. **Raise a Ticket** → *Track a ticket* → your number is already filled in: **Fixed**, with how it was fixed.
2. *Say:* "That fix is now part of the knowledge base." **Optional bonus:** raise the same VPN problem again,
   open it in the Agent Desk, and your own resolved ticket appears among the similar cases.
3. **Dashboard** shows the new ticket in the numbers; **Agent Desk → Timeline** shows the audit trail
   (submitted → assigned → resolved → every AI question).

## Example tickets that work (verified against the sample file)

| Title / description (short) | Auto-category | Typical past fix it finds |
|---|---|---|
| Cannot connect to VPN from home (timeout during authentication) | VPN / Cannot connect | Restart VPN concentrator; renew expired client certificate |
| Printer on 3rd floor not printing (paper jam error, no paper stuck) | Hardware / Printer issue | Clear print queue and restart spooler; replace worn fuser |
| Account locked after too many attempts (forgot password, cannot log in) | Access/Password / Account locked | Unlock account in Active Directory; reset password |
| Outlook not syncing (no new emails since yesterday) | Email / Outlook sync | Recreate OST cache; install Outlook update |
| Reports are very slow (queries take 20+ minutes, app times out) | Database / Slow query | Add missing index; kill runaway session |

Exact ticket IDs and similarity scores differ if you also loaded the 22,000-ticket generated dataset.

**The honest moment (recommended):** submit `Coffee machine broken` / `The coffee machine in the kitchen is
making a strange noise and gives no coffee.` with Category on *Not sure*. IncidentIQ answers *"We could not work
out the category"* instead of guessing. Say: "A wrong guess is worse than asking, so below a similarity of 0.30
it stays quiet." (During development a looser rule filed this under *virtual machines* because of the shared
word "machine"; that is why the bar is stricter than the 0.2 used for suggesting fixes.)

## If something goes wrong

| Problem | What to do |
|---|---|
| Sidebar says `no key - using Ollama` | `.env` not read: check the file name is exactly `.env`, restart Streamlit |
| AI replies say "offline fallback" | No key, no internet, or a rate limit. The demo still works; say so and move on |
| Gemini rate limit (HTTP 429) | Wait a minute. Fewer AI clicks: the similar-case list needs no AI at all |
| Agent Desk says no portal tickets | Raise one first on *Raise a Ticket* |
| Want a clean slate | Stop Streamlit, delete `db/incidentiq.db`, restart, load the CSV again |

## Questions you may be asked about this part

1. **Why not let the AI fix tickets by itself?** It only advises. A human decides and resolves. The AI has no
   tools: it cannot change a ticket or touch any system.
2. **How do you stop it making things up?** It only sees this ticket and the retrieved past cases, must cite
   ticket IDs, and every cited ID is checked against what was retrieved; invented IDs are flagged. Advice from
   outside the cases must be labelled.
3. **What if no past ticket is similar?** The agent sees "no strongly similar past ticket" (best score below
   0.2) and the one-shot fix says so rather than inventing one.
4. **How does similar-ticket search work?** TF-IDF + cosine similarity over ticket text (no AI), built from
   solved tickets that have resolution notes.
5. **Can the text-to-SQL AI read requester names and emails?** No. Those live in `portal_requests`, which is not
   on the AI's allow-list; a test proves both the static check and the database-level block refuse it.
6. **Does it learn?** Resolved tickets become part of the knowledge base when the index is rebuilt, which
   happens immediately after you resolve one.
7. **What is missing for production?** Logins and roles (anyone can open either page), notifications to the
   employee, assignment rules and escalation, a way for the agent to correct the category, a real database
   (PostgreSQL) for concurrent users, and a privacy review for what is sent to a cloud LLM.
8. **What data goes to Gemini?** The ticket text and the retrieved past resolutions. It is synthetic here. Free
   API tiers typically have weaker privacy terms than paid ones, so real company tickets would need a paid or
   private model. Check Google's current terms before using anything real.

## Do not claim

- That it is production-ready or secure for real employees: there is **no authentication**.
- That the data is real: it is synthetic.
- That it was tested against the real Gemini API: the code path is unit-tested with a fake HTTP layer, and
  the rest of the app was tested with no LLM configured. **Run the demo once with your key before the interview.**
