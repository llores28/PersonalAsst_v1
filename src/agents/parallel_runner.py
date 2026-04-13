"""Parallel multi-agent fan-out runner (M1).

Runs up to MAX_PARALLEL_BRANCHES independent agent tasks concurrently via
asyncio.gather(), then merges the results into a single coherent response.

Usage:
    from src.agents.parallel_runner import run_parallel_tasks, ParallelTask

    tasks = [
        ParallelTask(domain="gmail",    prompt="Check my inbox"),
        ParallelTask(domain="calendar", prompt="What's on my schedule today?"),
    ]
    merged = await run_parallel_tasks(tasks, user_telegram_id=user_id)

Safety:
- Max 3 parallel branches (budget multiplier guard).
- Each branch gets its own scoped skill set — no cross-branch state.
- Budget check before spawning: if daily_pct >= 80, falls back to sequential.
- Results are merged with clear section headers so the user sees one response.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MAX_PARALLEL_BRANCHES = 3
_BUDGET_WARN_PCT = 80.0


@dataclass
class ParallelTask:
    """A single branch task for parallel execution."""
    domain: str
    prompt: str
    context_hint: str = ""
    agent_override: Optional[object] = field(default=None, repr=False)


async def _check_budget_ok() -> bool:
    """Return True if daily spend is below the warning threshold."""
    try:
        import os
        from src.db.session import async_session
        from src.db.models import DailyCost
        from sqlalchemy import select, func
        from datetime import date

        daily_cap = float(os.getenv("DAILY_COST_CAP_USD", "5.00"))
        if daily_cap <= 0:
            return True
        async with async_session() as session:
            row = await session.execute(
                select(func.coalesce(func.sum(DailyCost.total_cost_usd), 0))
                .where(DailyCost.date == date.today())
            )
            today_cost = float(row.scalar_one())
        pct = today_cost / daily_cap * 100
        return pct < _BUDGET_WARN_PCT
    except Exception as e:
        logger.debug("Budget check failed (parallel_runner): %s — allowing", e)
        return True


async def _run_single_task(
    task: ParallelTask,
    user_telegram_id: int,
    user_name: str,
) -> str:
    """Run one parallel branch. Returns the agent response string."""
    try:
        from agents import Runner, RunConfig
        from src.agents.orchestrator import create_orchestrator_async
        from src.temporal import append_temporal_context

        enriched = append_temporal_context(task.prompt)
        agent = await create_orchestrator_async(
            user_telegram_id,
            user_name,
            task_context=f"[parallel branch: {task.domain}] {task.context_hint}".strip(),
        )
        result = await Runner.run(agent, enriched, run_config=RunConfig())
        return result.final_output or "(no output)"
    except Exception as e:
        logger.error("Parallel branch '%s' failed: %s", task.domain, e)
        return f"[{task.domain}]: ⚠️ Could not complete — {e}"


def _merge_results(tasks: list[ParallelTask], results: list[str]) -> str:
    """Merge parallel branch results with section headers."""
    if len(results) == 1:
        return results[0]

    sections: list[str] = []
    for task, result in zip(tasks, results):
        header = f"**{task.domain.replace('_', ' ').title()}**"
        sections.append(f"{header}\n{result}")
    return "\n\n---\n\n".join(sections)


async def run_parallel_tasks(
    tasks: list[ParallelTask],
    user_telegram_id: int,
    user_name: str = "there",
) -> str:
    """Fan-out tasks to multiple agent branches in parallel, merge results.

    Args:
        tasks:              List of ParallelTask (max MAX_PARALLEL_BRANCHES honoured).
        user_telegram_id:   Telegram user ID for session scoping.
        user_name:          Display name for persona prompts.

    Returns:
        Merged response string with section headers per domain.
    """
    if not tasks:
        return "(no tasks)"

    capped = tasks[:MAX_PARALLEL_BRANCHES]
    if len(tasks) > MAX_PARALLEL_BRANCHES:
        logger.warning(
            "parallel_runner: %d tasks requested, capped to %d",
            len(tasks), MAX_PARALLEL_BRANCHES,
        )

    budget_ok = await _check_budget_ok()
    if not budget_ok:
        logger.warning(
            "parallel_runner: budget >= %d%% — falling back to sequential execution",
            _BUDGET_WARN_PCT,
        )
        results = []
        for task in capped:
            r = await _run_single_task(task, user_telegram_id, user_name)
            results.append(r)
        return _merge_results(capped, results)

    logger.info(
        "parallel_runner: spawning %d branches for user %d",
        len(capped), user_telegram_id,
    )
    results = await asyncio.gather(
        *[_run_single_task(t, user_telegram_id, user_name) for t in capped],
        return_exceptions=False,
    )
    return _merge_results(capped, list(results))
