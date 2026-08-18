#!/usr/bin/env python3
"""Merge one live session's cost into a ticket's running stats-report comment.

Pure function of (existing comment body, new session record) -> (new comment body,
totals, cost tier). Makes no MCP calls itself -- the calling skill fetches the existing
comment via list_comments, and posts the result via save_comment/save_issue.

The comment carries its own state: a labeled ```json fenced block at the end holding
every session recorded so far, so re-running this script needs no other data store and
a same-session rerun (e.g. /stats-report run twice) replaces its own row instead of
duplicating it. Linear does NOT hide HTML comments (`<!-- ... -->` renders as literal
visible text in the comment, unlike GitHub) -- an earlier version of this script relied
on that and was wrong. The fenced block is visible by necessity; it's labeled
"do not edit" rather than pretending to be hidden.

Usage:
  render_session_comment.py <ticket_id> <existing_body_file_or_'none'> <session_json_file> [boundaries_file]

session_json_file: {"session_id", "first_message_at" (ISO8601), "cost_usd",
  "models" (dict model->usd), "pr_url" (nullable)}

boundaries_file defaults to boundaries.json, a local snapshot (tiers/tier_value/boundaries)
copied from ticket-cost-report's data/reassignment_plan.json -- see the Recalibration note
in SKILL.md for how it drifts and gets refreshed.

Prints JSON: {body, total_cost_usd, session_count, tier, tier_value} to stdout.
"""

import json
import re
import sys
from pathlib import Path

from tier_lib import TIER_VALUE, tier_for_cost

MARKER_RE = re.compile(
    r"<sub>stats-report session data — do not edit</sub>\s*```json\s*(.*?)\s*```", re.DOTALL
)
SKILL_DIR = Path(__file__).parent


def parse_existing(body):
    if not body or body == "none":
        return []
    m = MARKER_RE.search(body)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def fmt_money(v):
    return f"${v:.2f}"


def render(ticket, sessions, boundaries):
    """Render the table with a running per-session Estimate column.

    Each row's estimate is the tier for the *cumulative* cost through that session, so
    the column shows the ticket's estimate history rather than a per-session tier (a
    single session's own cost rarely crosses a tier boundary by itself). A row where the
    cumulative tier moved from the previous row's is rendered `PREV → NEW` so a
    tier change is visible in the table itself, not just in the summary line above it.
    """
    sessions = sorted(sessions, key=lambda s: s["first_message_at"])

    rows = []
    running = 0.0
    prev_tier = None
    final_tier = final_tier_value = None
    for s in sessions:
        running = round(running + s["cost_usd"], 2)
        cur_tier = tier_for_cost(running, boundaries) if boundaries else None

        if cur_tier is None:
            estimate_cell = "n/a"
        elif prev_tier is not None and cur_tier != prev_tier:
            estimate_cell = f"{prev_tier} → {cur_tier}"
        else:
            estimate_cell = cur_tier
        if cur_tier is not None:
            prev_tier = cur_tier
            final_tier, final_tier_value = cur_tier, TIER_VALUE[cur_tier]

        models_line = " ".join(f"{m}={fmt_money(c)}" for m, c in s.get("models", {}).items())
        pr = s.get("pr_url") or "n/a"
        date = s["first_message_at"][:10]
        rows.append(
            f"| {date} | `{s['session_id']}` | {fmt_money(s['cost_usd'])} | {estimate_cell} | {models_line or 'n/a'} | {pr} |"
        )
    total = running

    table = "\n".join(
        [
            "| Date | Session | Cost | Estimate | Models | PR |",
            "|---|---|---|---|---|---|",
            *rows,
        ]
    )

    tier_line = f"{final_tier} (estimate={final_tier_value})" if final_tier else "n/a (no cost boundaries yet)"

    body = f"""**Claude Code cost summary** (auto-generated, `stats-report`/`ticket-cost-report` skills)

| | |
|---|---|
| Sessions | {len(sessions)} |
| Total cost | {fmt_money(total)} |
| Cost tier | {tier_line} |

{table}

---
<sub>stats-report session data — do not edit</sub>

```json
{json.dumps(sessions, indent=2)}
```
"""
    return body, total, final_tier, final_tier_value


def main():
    args = sys.argv[1:]
    ticket = args[0]
    existing_body = Path(args[1]).read_text() if args[1] != "none" else None
    session = json.loads(Path(args[2]).read_text())
    boundaries_file = Path(args[3]) if len(args) > 3 else SKILL_DIR / "boundaries.json"

    sessions = parse_existing(existing_body)
    sessions = [s for s in sessions if s["session_id"] != session["session_id"]]
    sessions.append(session)

    boundaries = None
    if boundaries_file.exists():
        boundaries = json.loads(boundaries_file.read_text())["boundaries"]

    body, total, tier, tier_value = render(ticket, sessions, boundaries)

    json.dump(
        {
            "body": body,
            "total_cost_usd": total,
            "session_count": len(sessions),
            "tier": tier,
            "tier_value": tier_value,
        },
        sys.stdout,
        indent=2,
    )


if __name__ == "__main__":
    main()
