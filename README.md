# stats-report

A Claude Code skill that posts an end-of-session cost and activity report to a Slack channel — cost breakdown by model, token usage, subagent/workflow fan-out, cache efficiency, tool churn, and git/PR context. Meant to run as `/stats-report` at the end of a session, so you build up a greppable log for cost-per-task analysis over time.

It ships in two parts:

- `collect.py` — reads the current session's transcript straight off disk and emits one JSON object with everything the report needs. No API calls, no network access beyond what your Slack MCP tool does.
- `SKILL.md` — the instructions Claude follows to run the script, write two lines of honest judgment about how the session went, and post the result.

## Requirements

- [Claude Code](https://claude.com/claude-code) with a working directory for skills (`~/.claude/skills/`).
- A Slack MCP integration already connected in your Claude Code setup, with a `slack_send_message`-shaped tool available. This repo doesn't wire one up for you — connect whatever Slack MCP server you use first.
- A Slack channel to post reports to. A private channel works well since these reports can mention repo names, branch names, and PR links.

## Setup

1. **Create a Slack channel** (a private one is recommended) to receive reports, e.g. `#claude-stats-report`.

2. **Get its channel ID.** In Slack, open the channel → *View channel details* → the ID is at the bottom (starts with `C`). Or ask your Slack MCP tool to look it up by name.

3. **Copy this skill into place:**

   ```bash
   cp -r stats-report ~/.claude/skills/stats-report
   ```

4. **Edit `~/.claude/skills/stats-report/SKILL.md`:**
   - Replace `<YOUR_CHANNEL_ID>` (both occurrences) with your channel's ID.
   - Replace `#your-channel-name` with your channel's name (cosmetic, just for the docs).
   - Replace `<your-slack-mcp-tool>` in the frontmatter `allowed-tools` line, and the "Send with your Slack MCP tool" line in step 3, with the actual tool name your Slack MCP server exposes (e.g. `mcp__slack__slack_send_message` — check `/mcp` or your tool list for the exact name).

5. **Check `rates.json`.** It ships public list pricing for the current Claude model lineup. If your account has negotiated enterprise rates, or if you're on a subscription plan rather than metered/API billing, edit it:
   - Per-model `input`/`output` are USD per million tokens.
   - `plan` is `"metered"` (per-token billing — the dollar figure approximates your invoice) or `"subscription"` (flat fee + rate limits — the dollar figure is a consumption proxy only, **not** real spend). You can also override this per run with `--plan=subscription` or `STATS_REPORT_PLAN=subscription` instead of editing the file.

6. **Try it.** From inside any Claude Code session, run `/stats-report`. It should read the transcript, write a short summary and two judgment lines, and post to your channel.

## How cost is computed

There's no cost figure persisted anywhere on disk by Claude Code itself, so `collect.py` recomputes it from raw token counts × `rates.json`, matching the same list-rate math the `/status` → Usage panel uses (cache reads at 0.1× input rate, cache writes at 1.25× for 5-minute TTL / 2.0× for 1-hour TTL). See the "On cost accuracy" section in `SKILL.md` for the known gaps and the metered-vs-subscription distinction — read that before trusting the numbers on a subscription account.

## Linear ticket cost tiers

`boundaries.json` (used by `render_session_comment.py` to write the Estimate column and
tier on each ticket's Linear comment) maps cumulative session cost to a T-shirt tier:

| tier | cost range |
| --- | --- |
| XS | under $6 |
| S | $6 – $9 |
| M | $9 – $12 |
| L | $12 – $16 |
| XL | $16 – $24 |
| XXL | $24 – $37 |
| XXXL | $37+ |

These boundaries are quantiles of an initial 101-ticket sample from the **Cockatoos** team's
space in Linear, so the tiers start roughly evenly populated for that team's ticket mix and
`Extended T-Shirt` estimate scale. **They are not universal** — a different team's ticket
size distribution, Linear estimate scale, or model-cost mix will shift where the boundaries
should fall. Before using this against another team or Linear workspace, recompute
`boundaries.json` (and `tier_lib.py`'s `TIER_VALUE` mapping, if the target scale's stored
values differ) against that project's own sample rather than reusing these numbers as-is.

## Optional: agent-tier cross-check

If you run subagents/workflows and want the report to flag when an agent type didn't get the model tier you expect (e.g. a `scanner` that's supposed to run on Haiku but actually ran on Opus), the skill can cross-reference `~/.claude/agent-spawn.log` — a tab-separated log of `timestamp, session_id, agent_type, model, effort, source, description`. This repo doesn't include a hook to produce that log; if you have a PreToolUse gate on your Agent tool that writes one, point the skill at it. Without it, the skill falls back to whatever `collect.py` sees directly in the transcript, no log required.

## What's NOT in this repo

- Any specific Slack channel ID or name — that's yours to create and fill in.
- A Slack MCP server — bring your own.
- Negotiated pricing — `rates.json` ships public list rates only.

## License

MIT — see [LICENSE](LICENSE).
