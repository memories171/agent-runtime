from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_runtime.finance.monitoring import detect_review_indicators


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic financial transaction-screening benchmark")
    parser.add_argument("--dataset", default="benchmarks/financial/transaction_screening.jsonl")
    parser.add_argument("--output", default="financial-benchmark-results.json")
    args = parser.parse_args()

    rows = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    tp = fp = tn = fn = 0
    for row in rows:
        prediction = bool(detect_review_indicators(row["transactions"])["review_required"])
        actual = bool(row["label"])
        tp += int(prediction and actual)
        fp += int(prediction and not actual)
        tn += int(not prediction and not actual)
        fn += int(not prediction and actual)
        results.append({"task_id": row["task_id"], "expected_review": actual, "predicted_review": prediction,
                        "passed": prediction == actual})
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    report = {"dataset": args.dataset, "total": len(rows), "accuracy": (tp + tn) / len(rows) if rows else 0.0,
              "precision": precision, "recall": recall, "f1": f1,
              "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn}, "results": results,
              "disclaimer": "Synthetic demo benchmark only; not a validated AML model or regulatory effectiveness test."}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("total", "accuracy", "precision", "recall", "f1", "confusion_matrix")}, ensure_ascii=False, indent=2))
    print(f"saved: {args.output}")


if __name__ == "__main__":
    main()
