"""Run the 20 benchmark questions through the text-to-SQL pipeline and score them.

    python tests/run_benchmark.py          # uses the real LLM from .env / Ollama
    python tests/run_benchmark.py --gold   # feeds the gold SQL instead (checks the harness = 100%)

Checks per question (expected answers are computed from gold_sql on the live DB):
  top_value     the gold first-row value appears in the AI's first row
  scalar        a number in the AI's first row matches the gold number (+-1%)
  contains_all  every gold value appears somewhere in the AI's result
  refused       the request is Blocked or answered with CANNOT_ANSWER
Results are written to docs/benchmark_results.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.ai import llm_client
from src.ai.sql_guard import execute_readonly
from src.ai.text_to_sql import answer_question

QUESTIONS = Path(__file__).parent / "benchmark_questions.json"


def _norm(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return str(value).strip().lower()


def _close(a, b):
    return isinstance(a, float) and isinstance(b, float) and abs(a - b) <= max(0.05, abs(b) * 0.01)


def score(item, result):
    """Return (passed, note)."""
    if item["check"] == "refused":
        return result["status"] == "Blocked" or result["sql"] == "CANNOT_ANSWER", result["status"]
    if result["status"] != "Valid" or result["dataframe"] is None or result["dataframe"].empty:
        return False, f"{result['status']}: {result['reason'][:80]}"
    gold = execute_readonly(item["gold_sql"])
    got = result["dataframe"]
    first_row = [_norm(v) for v in got.iloc[0].tolist()]
    if item["check"] == "top_value":
        want = _norm(gold.iloc[0, 0])
        if item.get("expected") and want != _norm(item["expected"]):
            return False, f"gold answer {want} differs from planted {item['expected']}"
        return any(v == want or _close(v, want) for v in first_row), f"want {want}, got {first_row}"
    if item["check"] == "scalar":
        want = _norm(gold.iloc[0, 0])
        return any(_close(v, want) for v in first_row if isinstance(v, float)), f"want {want}, got {first_row}"
    cells = [_norm(v) for v in got.to_numpy().ravel()]
    missing = [w for w in (_norm(v) for v in gold.to_numpy().ravel())
               if not any(c == w or _close(c, w) for c in cells)]
    return not missing, f"missing {missing[:5]}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", action="store_true", help="use gold SQL instead of the LLM")
    args = parser.parse_args()
    items = json.loads(QUESTIONS.read_text())
    rows, passed, blocked = [], 0, 0
    start = time.time()
    for item in items:
        gen = (lambda q, e=None, p=None, s=item["gold_sql"]: s) if args.gold else None
        result = answer_question(item["question"], explain_answer=False, generate_fn=gen)
        ok, note = score(item, result)
        passed += ok
        blocked += result["status"] == "Blocked"
        rows.append({"id": item["id"], "question": item["question"], "check": item["check"],
                     "passed": bool(ok), "status": result["status"], "sql": result["sql"], "note": note})
        print(f"[{'PASS' if ok else 'FAIL'}] Q{item['id']:>2} {item['question'][:60]:<60} {note[:70]}")
    accuracy = 100.0 * passed / len(items)
    mode = "gold SQL" if args.gold else (llm_client.LAST_PROVIDER or config.LLM_PROVIDER)
    print(f"\nMode: {mode}\nAccuracy: {passed}/{len(items)} = {accuracy:.0f}%   "
          f"Blocked: {blocked}   Failures: {[r['id'] for r in rows if not r['passed']]}   "
          f"Time: {time.time() - start:.0f}s")
    out = config.ROOT / "docs" / ("benchmark_results_gold.json" if args.gold else "benchmark_results.json")
    out.write_text(json.dumps({"mode": mode, "accuracy_pct": accuracy, "passed": passed,
                               "total": len(items), "blocked": blocked, "results": rows}, indent=2))
    print(f"Saved {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
