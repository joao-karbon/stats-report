#!/usr/bin/env python3
"""Collect per-session metrics from a Claude Code transcript for /stats-report.

Emits a single JSON object on stdout. Cost is computed from per-message token
counts times the rates in rates.json; the formula is calibrated against the
/status Usage panel (list rates, cache read 0.1x input, cache write 1.25x for
5m TTL and 2.0x for 1h TTL).

Usage: collect.py [SESSION_ID] [--project-dir SLUG]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
RATES_PATH = Path(__file__).parent / "rates.json"


def load_rates() -> dict:
    with RATES_PATH.open() as fh:
        return json.load(fh)


def slug_for(cwd: str) -> str:
    """Claude Code's project-dir slug: non-alphanumerics collapse to '-'."""
    return "".join(c if c.isalnum() else "-" for c in cwd)


def find_transcript(session_id: str | None, cwd: str) -> Path:
    candidates: list[Path] = []
    if session_id:
        candidates = sorted(PROJECTS.glob(f"*/{session_id}.jsonl"))
    if not candidates:
        project = PROJECTS / slug_for(cwd)
        if project.is_dir():
            candidates = sorted(
                project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
            )
    if not candidates:
        sys.exit(f"no transcript found (session_id={session_id!r} cwd={cwd!r})")
    return candidates[0]


def read_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def blank_usage() -> dict:
    return {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write_5m": 0,
        "cache_write_1h": 0,
        "requests": 0,
    }


def accumulate(entry: dict, into: dict) -> None:
    """Add one message's usage. Caller must ensure this runs once per message.id.

    Claude Code writes a separate transcript entry per content block (thinking,
    text, each tool_use), and every one repeats the whole message's usage
    object. Summing them multiplies token counts by blocks-per-message.
    """
    usage = (entry.get("message") or {}).get("usage") or {}
    into["input"] += usage.get("input_tokens", 0) or 0
    into["output"] += usage.get("output_tokens", 0) or 0
    into["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
    creation = usage.get("cache_creation") or {}
    write_1h = creation.get("ephemeral_1h_input_tokens", 0) or 0
    write_5m = creation.get("ephemeral_5m_input_tokens", 0) or 0
    if not (write_1h or write_5m):
        # Older entries only carry the aggregate; attribute it to the 5m bucket.
        write_5m = usage.get("cache_creation_input_tokens", 0) or 0
    into["cache_write_1h"] += write_1h
    into["cache_write_5m"] += write_5m
    into["requests"] += 1


def cost_for(model: str, usage: dict, rates: dict) -> float:
    table = rates["models"]
    rate = table.get(model)
    if rate is None:
        # Tolerate date-suffixed ids (claude-haiku-4-5-20251001) by longest-prefix match.
        matches = [k for k in table if model.startswith(k)]
        rate = table[max(matches, key=len)] if matches else rates["_default_for_unknown_model"]
    mult = rates["_multipliers"]
    rin, rout = rate["input"], rate["output"]
    return (
        usage["input"] * rin
        + usage["output"] * rout
        + usage["cache_read"] * rin * mult["cache_read"]
        + usage["cache_write_5m"] * rin * mult["cache_write_5m"]
        + usage["cache_write_1h"] * rin * mult["cache_write_1h"]
    ) / 1_000_000


WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def scan_transcript(path: Path) -> dict:
    per_model: dict[str, dict] = defaultdict(blank_usage)
    efforts: Counter = Counter()
    tools: Counter = Counter()
    agent_spawns: Counter = Counter()
    compactions: list[dict] = []
    turn_ms = 0
    turns = 0
    churn: dict[str, int] = {}
    # Counted from the write-tool calls themselves. `file-history-delta` entries
    # are the harness's own backup log and stopped tracking edits as of CLI
    # 2.1.219 — a 99-edit session emits none — so they are unioned in below
    # rather than relied on.
    writes: Counter = Counter()
    prompts: list[str] = []
    interruptions = 0
    timestamps: list[str] = []
    counted: set[str] = set()
    meta = {"title": None, "recap": None, "cwd": None, "branch": None, "version": None}

    for entry in read_jsonl(path):
        kind = entry.get("type")

        if kind == "assistant":
            message = entry.get("message") or {}
            model = message.get("model") or "unknown"
            # Locally-injected messages (cancellations, error stubs) carry no usage.
            if model == "<synthetic>":
                continue
            # One API call is split across several entries (one per content
            # block), each repeating the same usage — count it only once.
            key = message.get("id") or entry.get("requestId") or entry.get("uuid")
            if key not in counted:
                counted.add(key)
                accumulate(entry, per_model[model])
                if entry.get("effort"):
                    efforts[entry["effort"]] += 1
            for key in ("cwd", "version"):
                meta[key] = meta[key] or entry.get(key)
            meta["branch"] = meta["branch"] or entry.get("gitBranch")
            if entry.get("timestamp"):
                timestamps.append(entry["timestamp"])
            for block in (entry.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or "unknown"
                    tools[name] += 1
                    if name in WRITE_TOOLS:
                        args = block.get("input") or {}
                        target = args.get("file_path") or args.get("notebook_path")
                        if target:
                            writes[target] += 1
                    if name == "Agent":
                        sub = (block.get("input") or {}).get("subagent_type") or "default"
                        agent_spawns[sub] += 1

        elif kind == "system":
            sub = entry.get("subtype")
            if sub == "turn_duration":
                turn_ms += entry.get("durationMs", 0) or 0
                turns += 1
            elif sub == "away_summary":
                meta["recap"] = entry.get("content")
            elif sub == "compact_boundary":
                cm = entry.get("compactMetadata") or {}
                compactions.append(
                    {
                        "trigger": cm.get("trigger"),
                        "pre_tokens": cm.get("preTokens"),
                        "post_tokens": cm.get("postTokens"),
                        "dropped_tokens": cm.get("cumulativeDroppedTokens"),
                        "duration_ms": cm.get("durationMs"),
                    }
                )

        elif kind == "ai-title":
            meta["title"] = entry.get("aiTitle")

        elif kind == "last-prompt":
            if entry.get("lastPrompt"):
                prompts.append(entry["lastPrompt"])

        elif kind == "queue-operation":
            interruptions += 1

        elif kind == "file-history-delta":
            tracked = entry.get("trackingPath")
            version = (entry.get("backup") or {}).get("version") or 1
            if tracked:
                churn[tracked] = max(churn.get(tracked, 0), version)

    for target, count in writes.items():
        churn[target] = max(churn.get(target, 0), count)

    return {
        "per_model": dict(per_model),
        "efforts": dict(efforts),
        "tools": dict(tools),
        "agent_spawns": dict(agent_spawns),
        "compactions": compactions,
        "turn_ms": turn_ms,
        "turns": turns,
        "churn": churn,
        "prompts": prompts,
        "interruptions": interruptions,
        "timestamps": timestamps,
        "meta": meta,
    }


def scan_one_agent(jsonl: Path, rates: dict) -> dict:
    info = scan_transcript(jsonl)
    agent_meta = {}
    metafile = Path(str(jsonl)[: -len(".jsonl")] + ".meta.json")
    if metafile.exists():
        try:
            with metafile.open() as fh:
                agent_meta = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    models = {
        model: {**usage, "cost_usd": round(cost_for(model, usage, rates), 4)}
        for model, usage in info["per_model"].items()
    }
    return {
        "agent_type": agent_meta.get("agentType"),
        "description": agent_meta.get("description"),
        "spawn_depth": agent_meta.get("spawnDepth"),
        "models": models,
        "efforts": info["efforts"],
        "tools": info["tools"],
        "cost_usd": round(sum(m["cost_usd"] for m in models.values()), 4),
    }


def roll_up(agents: list[dict]) -> dict:
    """Aggregate a fleet of agents into per-model totals plus a fleet cost."""
    per_model: dict[str, dict] = defaultdict(blank_usage)
    for agent in agents:
        for model, usage in agent["models"].items():
            for key in per_model[model]:
                per_model[model][key] += usage[key]
    return {
        "agents": len(agents),
        "cost_usd": round(sum(a["cost_usd"] for a in agents), 4),
        "models": {
            model: {**usage, "cost_usd": round(
                sum(a["models"][model]["cost_usd"] for a in agents if model in a["models"]), 4
            )}
            for model, usage in per_model.items()
        },
    }


def scan_direct_subagents(session_dir: Path, rates: dict) -> list[dict]:
    """Agents spawned by the Agent tool: subagents/agent-*.jsonl."""
    return [
        scan_one_agent(p, rates)
        for p in sorted((session_dir / "subagents").glob("agent-*.jsonl"))
    ]


def scan_workflows(session_dir: Path, rates: dict) -> list[dict]:
    """Workflow runs plus their agent fleets.

    Fleet transcripts are nested at subagents/workflows/<runId>/agent-*.jsonl,
    NOT alongside direct subagents. Missing them silently reports a 100-agent
    fan-out as costing nothing.
    """
    fleet_root = session_dir / "subagents" / "workflows"
    out = []
    seen_runs: set[str] = set()

    for wf in sorted((session_dir / "workflows").glob("wf_*.json")):
        try:
            with wf.open() as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        run_id = data.get("runId") or wf.stem
        seen_runs.add(run_id)
        agents = [
            scan_one_agent(p, rates)
            for p in sorted((fleet_root / run_id).glob("agent-*.jsonl"))
        ]
        out.append(
            {
                "run_id": run_id,
                "name": data.get("workflowName"),
                "declared_agent_count": data.get("agentCount"),
                "status": data.get("status"),
                "duration_ms": data.get("durationMs"),
                "error": (data.get("error") or "").split("\n")[0] or None,
                "fleet": roll_up(agents),
            }
        )

    # A fleet whose run json is absent (killed before it was written) still cost money.
    if fleet_root.is_dir():
        for run_dir in sorted(fleet_root.iterdir()):
            if not run_dir.is_dir() or run_dir.name in seen_runs:
                continue
            agents = [
                scan_one_agent(p, rates) for p in sorted(run_dir.glob("agent-*.jsonl"))
            ]
            if not agents:
                continue
            out.append(
                {
                    "run_id": run_dir.name,
                    "name": None,
                    "declared_agent_count": None,
                    "status": "unknown (no run record on disk)",
                    "duration_ms": None,
                    "error": None,
                    "fleet": roll_up(agents),
                }
            )
    return out


def git_context(cwd: str) -> dict:
    def run(*args: str) -> str | None:
        try:
            res = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return res.stdout.strip() if res.returncode == 0 else None

    if run("git", "rev-parse", "--is-inside-work-tree") != "true":
        return {"repo": False}

    ctx: dict = {"repo": True}
    ctx["branch"] = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    ctx["remote"] = run("git", "config", "--get", "remote.origin.url")
    ctx["commits_ahead"] = run("git", "rev-list", "--count", "@{u}..HEAD")
    ctx["uncommitted"] = run("git", "diff", "--shortstat")
    ctx["staged"] = run("git", "diff", "--cached", "--shortstat")

    pr = run(
        "gh", "pr", "view", "--json", "number,title,url,state,additions,deletions"
    )
    if pr:
        try:
            ctx["pr"] = json.loads(pr)
        except json.JSONDecodeError:
            pass
    return ctx


PLAN_BASIS = {
    "metered": (
        "per-token billing — tokens x rates.json, approximates the invoice; "
        "calibrated against /status Usage"
    ),
    "subscription": (
        "CONSUMPTION PROXY, NOT SPEND — this account pays a flat fee and is "
        "rate-limited, so no per-token charge is incurred. Compare tokens and "
        "cache_share across sessions; do not read the dollar figure as money owed."
    ),
}


def resolve_plan(rates: dict) -> str:
    for flag in sys.argv[1:]:
        if flag.startswith("--plan="):
            return flag.split("=", 1)[1].strip()
        if flag == "--subscription":
            return "subscription"
    env = os.environ.get("STATS_REPORT_PLAN")
    if env:
        return env.strip()
    return rates.get("plan", "metered")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    session_id = args[0] if args else os.environ.get("CLAUDE_SESSION_ID")
    cwd = os.getcwd()

    rates = load_rates()
    plan = resolve_plan(rates)
    if plan not in PLAN_BASIS:
        sys.exit(f"unknown plan {plan!r}; expected one of {sorted(PLAN_BASIS)}")
    transcript = find_transcript(session_id, cwd)
    session_dir = transcript.with_suffix("")
    info = scan_transcript(transcript)

    resolved_cwd = info["meta"]["cwd"] or cwd
    subagents = scan_direct_subagents(session_dir, rates) if session_dir.is_dir() else []
    workflows = scan_workflows(session_dir, rates) if session_dir.is_dir() else []

    main_models = {
        model: {**usage, "cost_usd": round(cost_for(model, usage, rates), 4)}
        for model, usage in info["per_model"].items()
    }
    main_cost = sum(m["cost_usd"] for m in main_models.values())
    sub_cost = sum(a["cost_usd"] for a in subagents)
    wf_cost = sum(w["fleet"]["cost_usd"] for w in workflows)
    wf_agents = sum(w["fleet"]["agents"] for w in workflows)

    totals = blank_usage()
    for usage in info["per_model"].values():
        for key in totals:
            totals[key] += usage[key]
    for agent in subagents:
        for usage in agent["models"].values():
            for key in totals:
                totals[key] += usage[key]
    for workflow in workflows:
        for usage in workflow["fleet"]["models"].values():
            for key in totals:
                totals[key] += usage[key]

    # Cost by model across main loop + every agent, so an expensive fleet tier is visible.
    by_model: dict[str, float] = defaultdict(float)
    for model, usage in main_models.items():
        by_model[model] += usage["cost_usd"]
    for agent in subagents:
        for model, usage in agent["models"].items():
            by_model[model] += usage["cost_usd"]
    for workflow in workflows:
        for model, usage in workflow["fleet"]["models"].items():
            by_model[model] += usage["cost_usd"]

    billable_input = (
        totals["input"]
        + totals["cache_read"]
        + totals["cache_write_5m"]
        + totals["cache_write_1h"]
    )
    cache_share = (
        (totals["cache_read"] + totals["cache_write_5m"] + totals["cache_write_1h"])
        / billable_input
        if billable_input
        else 0.0
    )

    mcp_calls = {k: v for k, v in info["tools"].items() if k.startswith("mcp__")}
    churned = {p: v for p, v in info["churn"].items() if v > 1}

    span_ms = None
    if len(info["timestamps"]) >= 2:
        from datetime import datetime

        try:
            lo = min(info["timestamps"])
            hi = max(info["timestamps"])
            fmt = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
            span_ms = int((fmt(hi) - fmt(lo)).total_seconds() * 1000)
        except ValueError:
            pass

    report = {
        "session_id": transcript.stem,
        "transcript": str(transcript),
        "title": info["meta"]["title"],
        "recap": info["meta"]["recap"],
        "cwd": resolved_cwd,
        "cli_version": info["meta"]["version"],
        "first_prompt": info["prompts"][0] if info["prompts"] else None,
        "prompt_count": len(info["prompts"]),
        "api_requests": totals["requests"],
        "measured_active_ms": info["turn_ms"],
        "measured_active_samples": info["turns"],
        "span_ms": span_ms,
        "interruptions": info["interruptions"],
        "cost": {
            "total_usd": round(main_cost + sub_cost + wf_cost, 4),
            "main_usd": round(main_cost, 4),
            "subagents_usd": round(sub_cost, 4),
            "workflow_agents_usd": round(wf_cost, 4),
            "by_model_usd": {m: round(c, 4) for m, c in sorted(by_model.items(), key=lambda kv: -kv[1])},
            "plan": plan,
            "is_real_money": plan == "metered",
            "basis": PLAN_BASIS[plan],
        },
        "agent_counts": {"direct_subagents": len(subagents), "workflow_agents": wf_agents},
        "tokens": {**totals, "cache_share_of_input": round(cache_share, 4)},
        "models": main_models,
        "efforts": info["efforts"],
        "subagents": subagents,
        "workflows": workflows,
        "compactions": info["compactions"],
        "tools": dict(sorted(info["tools"].items(), key=lambda kv: -kv[1])),
        "mcp_calls": mcp_calls,
        "agent_spawns": info["agent_spawns"],
        "churn": dict(sorted(churned.items(), key=lambda kv: -kv[1])),
        "files_touched": len(info["churn"]),
        "git": git_context(resolved_cwd),
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
