"""Central settings for IncidentIQ.

Every path and tunable number lives here so the rest of the code never
hard-codes them. Secrets and overrides come from a `.env` file
(copy `.env.example` to `.env`).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------- folders
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"                # generated CSVs, never edited
CLEANED_DIR = DATA_DIR / "cleaned"
REJECTED_DIR = DATA_DIR / "rejected"
REFERENCE_DIR = DATA_DIR / "reference"    # teams / agents roster ("HR system")
API_SOURCE_DIR = DATA_DIR / "api_source"  # the mock API's own backing file
REPORTS_DIR = DATA_DIR / "reports"
DB_DIR = ROOT / "db"
DB_PATH = Path(os.getenv("DB_PATH", str(DB_DIR / "incidentiq.db")))
SCHEMA_PATH = DB_DIR / "schema.sql"
SQL_DIR = ROOT / "sql"

for _folder in (RAW_DIR, CLEANED_DIR, REJECTED_DIR, REFERENCE_DIR,
                API_SOURCE_DIR, REPORTS_DIR, DB_DIR):
    _folder.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------- synthetic data
SEED = int(os.getenv("SEED", "42"))
N_TICKETS = int(os.getenv("N_TICKETS", "20000"))
N_API_TICKETS = int(os.getenv("N_API_TICKETS", "2000"))
N_DIRTY_TICKETS = 200

# The data is generated "as if" it was extracted at AS_OF. Using a fixed
# clock (instead of datetime.now()) keeps every number reproducible.
DATA_START = "2025-11-01 00:00:00"
AS_OF = "2026-11-01 00:00:00"
VPN_SPIKE_MONTH = "2026-10"     # planted pattern A
WEAK_TEAM = "Infra-Team-B"      # planted pattern B
OPEN_SHARE = 0.08               # ~8% of tickets still open

PRIORITY_SHARES = {"P1": 0.03, "P2": 0.12, "P3": 0.45, "P4": 0.40}
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}
PRIORITY_NAMES = {"P1": "Critical", "P2": "High", "P3": "Medium", "P4": "Low"}
STATUSES = ["Open", "In Progress", "Resolved", "Closed"]
OPEN_STATUSES = ("Open", "In Progress")
APPROVED_CATEGORIES = [
    "Network", "Email", "VPN", "Database", "Application Error", "Hardware",
    "Access/Password", "Cloud Infrastructure", "Security",
]

# ---------------------------------------------------------------- mock API
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
API_TIMEOUT = 10      # seconds per request
API_RETRIES = 3       # attempts before giving up
API_PAGE_SIZE = 500

# -------------------------------------------------------------------- LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# -------------------------------------------------------------- AI safety
APPROVED_TABLES = {"tickets", "teams", "agents", "categories", "resolutions", "batches"}
MAX_ROWS = 200                 # LIMIT added to every AI query
QUERY_TIMEOUT_SECONDS = 5      # AI queries running longer are aborted
SIMILARITY_THRESHOLD = 0.2

# --------------------------------------------------------- AWS (optional)
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
AWS_REGION = os.getenv("AWS_REGION", "us-east-1").strip()
AWS_PROFILE = os.getenv("AWS_PROFILE", "").strip()
