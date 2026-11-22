# Planted patterns

The generator (`src/generate_data.py`, seed 42) deliberately hides four patterns in the
synthetic data. The analytics and the AI assistant must rediscover them. The numbers below come
from the real run on the loaded database (main file + dirty file + API batch = 22,158 tickets).
They are checked automatically in `tests/test_analytics.py`.

| | Pattern | How it is planted | Where to see it | Measured |
|---|---|---|---|---|
| A | **VPN Gateway spike** in `2026-10` | 2 extra VPN Gateway tickets for every normal one in that month; those tickets also take 1.5× longer to fix | Q5 repeat offenders, Dashboard → Services | 159 tickets in 2026-10 vs 39 in 2026-09 (×4.1); 141 vs a 42/month average in the main file (×3.4) |
| B | **Infra-Team-B breaches SLA ~2× more** | Resolution times ×1.35 for that team; 4 of its 5 agents are Junior | Q2 breach by team, Dashboard → SLA by team | 28.2% vs 14.4% for all other teams (×1.96) |
| C | **Monday 09:00–11:00 peak** and **release evenings** (first Friday of each month, 18:00–22:00) | Hour-of-week weights: Monday 9–10 h ×1.6, release evenings ×12, release tickets skewed to Application Error / Cloud | Q4 heatmap, Dashboard → Peaks | Busiest cells: Monday 09 h (897), Monday 10 h (771); next is Tuesday 10 h (445). Friday evenings stand out from other evenings |
| D | **Password reset = most frequent and fastest** | Highest subcategory weight; resolution speed factor 0.12 | Q1 top issues | 2,451 tickets (11.1%), average 2.3 h; next fastest is Account locked at 7.1 h |

## Other realism rules

- Priorities: P1 3%, P2 12%, P3 45%, P4 40%. SLA targets P1 = 4 h, P2 = 8 h, P3 = 24 h, P4 = 72 h.
- Resolution time is log-normal (sigma 0.75) with a median per priority, scaled by subcategory speed,
  agent experience and the planted effects above.
- 8% of tickets are still Open / In Progress (more likely for recent tickets). Overall SLA breach rate
  is 15.9% (resolved tickets only: about 9%).
- Descriptions and resolution notes are built from per-subcategory templates, so similar tickets share
  vocabulary and the TF-IDF search finds good matches.

## Side effect worth explaining to judges

The monthly breach rate climbs in the most recent months (17% in July → 37% in October). That is
mainly the **open backlog**: 661 October tickets are still open, and an open ticket counts as
breached once it passes its SLA target. Resolved-only breach rate stays flat at about 9% a month.
The VPN Gateway spike adds on top of that in October. The AI assistant should call these
"possible factors", not proven causes.

## Dirty test file (`data/raw/incidents_dirty_test.csv`)

200 rows. Each defect sits in its own block of rows, so the expected counts are exact
(see the docstring in `src/dirty_data.py`):

| Defect | Rows | Expected result |
|---|---|---|
| Empty priority / "ASAP", "N/A", "9" … | 15 | rejected `INVALID_PRIORITY` |
| Unparsable `opened_at` ("not a date", "2026-13-45 …") | 5 | rejected `BAD_DATE` |
| `resolved_at` 3 h before `opened_at` | 8 | rejected `RESOLVED_BEFORE_OPENED` |
| Category "Coffee Machine", "Parking" … | 4 | rejected `INVALID_CATEGORY` |
| Exact duplicate rows (same `ticket_id`) | 10 | rejected `DUPLICATE_ID` (first copy kept) |
| Priority "High", "1 - Critical", "2", "low", "p4" … | 20 | fixed → P1–P4 |
| Category "netwrok", "NETWORK", "Network ", "  vpn ", "Emial" … | 33 | fixed (fuzzy match) |
| Dates as `05/31/2026 14:30` and `31-May-2026 14:30` | 30 rows | fixed (parsed) |
| Status "RESOLVED", "closed ", "in-progress" | 10 | fixed |
| Negative or empty `reopen_count` | 8 | fixed → 0 |

Result: **200 raw = 158 cleaned + 42 rejected**.
