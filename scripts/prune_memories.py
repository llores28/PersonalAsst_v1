"""Run memory eviction for a user's Mem0 store.

Designed for nightly cron (e.g. 03:00 UTC). Idempotent -- under the cap is a
no-op. Safe to invoke ad-hoc as well.

Examples
--------

  # Dry-run for the configured owner -- reports what would happen, no writes.
  python -m scripts.prune_memories --user-id 999999999 --dry-run

  # Real run with default cap (8000) -> target (7200).
  python -m scripts.prune_memories --user-id 999999999

  # Tighter cap for testing the policy.
  python -m scripts.prune_memories --user-id 999999999 --cap 100 --target-after 80
"""

import argparse
import asyncio
import json
import sys


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-id", required=True, help="Telegram user ID (string OK)")
    ap.add_argument("--cap", type=int, default=8000,
                    help="Max memories before eviction kicks in")
    ap.add_argument("--target-after", type=int, default=7200,
                    help="Memory count after eviction (must be < cap)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen without writing or deleting")
    args = ap.parse_args()
    if args.target_after >= args.cap:
        ap.error("--target-after must be strictly less than --cap")
    return args


def main() -> int:
    args = _parse_args()
    # Lazy import — only loaded for real invocations, so `--help` works in
    # environments without a configured `.env` (e.g. CI smoke tests).
    from src.memory.eviction_runner import prune_user_memories

    report = asyncio.run(prune_user_memories(
        user_id=str(args.user_id),
        cap=args.cap,
        target_after=args.target_after,
        dry_run=args.dry_run,
    ))
    print(json.dumps(report, indent=2))
    # Exit code: 0 on success (including under-cap no-op), 1 on error.
    return 1 if "error" in report else 0


if __name__ == "__main__":
    sys.exit(main())
