"""Phase 6e - find similar past incidents (TF-IDF) and draft a suggested fix.

    index = SimilarIncidentIndex()          # built from resolved tickets
    matches = index.find_similar("VPN keeps failing with error 0x1A2B", top_k=5)
    suggest_fix(text, matches)              # LLM may use ONLY these resolutions

If the best similarity is below SIMILARITY_THRESHOLD (0.2) no fix is suggested.
"""
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from src import config
from src.ai import llm_client
from src.load_db import read_table

NO_MATCH = "No sufficiently similar past incidents found."
SYSTEM = ("You are an IT support assistant. Draft a short 'Suggested fix' (3-5 numbered steps) for "
          "the new incident using ONLY the past resolutions provided. Cite the ticket IDs you used in "
          "square brackets, e.g. [INC0101234]. Do not add steps that are not in the past resolutions. "
          "If the past resolutions do not fit the new incident, say so.")


class SimilarIncidentIndex:
    """TF-IDF over short_description + description of resolved tickets."""

    def __init__(self, db_path=None):
        self.docs = read_table(
            "SELECT t.ticket_id, t.priority, t.short_description, t.description, c.category, "
            "c.subcategory, r.resolution_notes, r.resolution_code "
            "FROM tickets t JOIN resolutions r ON r.ticket_id = t.ticket_id "
            "LEFT JOIN categories c ON c.category_id = t.category_id "
            "WHERE t.resolved_at IS NOT NULL AND r.resolution_notes IS NOT NULL", db_path=db_path)
        if self.docs.empty:
            raise ValueError("No resolved tickets in the database yet. Run the pipeline first.")
        text = (self.docs["short_description"].fillna("") + ". " + self.docs["description"].fillna(""))
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=2,
                                          sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(text)

    def find_similar(self, text, top_k=5, exclude_id=None):
        """Top-k most similar resolved tickets with their similarity scores."""
        scores = linear_kernel(self.vectorizer.transform([text]), self.matrix).ravel()
        result = self.docs.assign(similarity=scores.round(3))
        if exclude_id:
            result = result[result["ticket_id"] != exclude_id]
        cols = ["ticket_id", "similarity", "category", "subcategory", "short_description",
                "resolution_notes", "resolution_code"]
        return result.nlargest(top_k, "similarity")[cols].reset_index(drop=True)


def suggest_fix(text, matches, threshold=config.SIMILARITY_THRESHOLD):
    """Return {"status", "suggestion", "cited_ids", "invalid_citations", "provider"}."""
    if matches.empty or matches["similarity"].max() < threshold:
        return {"status": "no_match", "suggestion": NO_MATCH, "cited_ids": [],
                "invalid_citations": [], "provider": None}
    context = "\n".join(f"[{r.ticket_id}] (similarity {r.similarity}) {r.resolution_notes}"
                        for r in matches.itertuples())
    try:
        draft = llm_client.ask_llm(SYSTEM, f"New incident:\n{text}\n\nPast resolutions:\n{context}",
                                   max_tokens=400)
        provider = llm_client.LAST_PROVIDER
    except llm_client.LLMUnavailableError:
        draft = "LLM unavailable - the most similar past resolutions are:\n" + "\n".join(
            f"{i}. {r.resolution_notes} [{r.ticket_id}]" for i, r in enumerate(matches.itertuples(), 1))
        provider = "offline fallback (no LLM)"
    cited = sorted(set(re.findall(r"INC\d{7}", draft)))
    allowed = set(matches["ticket_id"])
    return {"status": "ok", "suggestion": draft, "cited_ids": [c for c in cited if c in allowed],
            "invalid_citations": [c for c in cited if c not in allowed], "provider": provider}
