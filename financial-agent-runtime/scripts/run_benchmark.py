from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agent_runtime.benchmark.runner import load_jsonl, run_matrix


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an Agent Runtime model × harness benchmark matrix")
    parser.add_argument("--dataset", default="benchmarks/smoke.jsonl")
    parser.add_argument("--deployments", required=True, help='JSON object, e.g. {"baseline":"http://localhost:8000","memory-v2":"http://localhost:8100"}')
    parser.add_argument("--models", required=True, help="Comma-separated model names")
    parser.add_argument("--tenant", default="benchmark")
    parser.add_argument("--user", default="runner")
    parser.add_argument("--scopes", default="agent:execute,memory:read,memory:write,approval:approve")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--output", default="benchmark-results.json")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    headers = {
        "X-Tenant-ID": args.tenant,
        "X-User-ID": args.user,
        "X-Scopes": args.scopes,
    }
    report = await run_matrix(
        load_jsonl(args.dataset),
        json.loads(args.deployments),
        [model.strip() for model in args.models.split(",") if model.strip()],
        headers,
        args.timeout,
    )
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"saved: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
