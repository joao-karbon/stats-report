---
name: stats-report
description: Post an end-of-session cost and activity report for the CURRENT session to the #claude-stats-report Slack channel, and update every Linear ticket the session touched with its cost, a Cost Verified label, and a recomputed cost-tier estimate. Use when the user runs /stats-report, or asks to log/record this session's stats, cost, or usage.
allowed-tools: Bash, Read, mcp__claude_ai_Slack__slack_send_message, mcp__linear-server__get_issue, mcp__linear-server__list_comments, mcp__linear-server__save_comment, mcp__linear-server__save_issue
---

# /stats-report

Collect this session's metrics, write the judgment lines yourself, post to Slack. Run this at the end of a task or session, from inside that same session.

Channel: `#claude-stats-report` → `channel_id: C0BLNJRG83W` (private).

## 1. Collect

```bash
python3 ~/.claude/skills/stats-report/collect.py <SESSION_ID>
```

Get `<SESSION_ID>` from your scratchpad path — it is the directory component immediately above `scratchpad` (`/private/tmp/claude-<n>/<cwd-slug>/<SESSION_ID>/scratchpad`). If you cannot determine it, omit the argument and the script falls back to the most recently modified transcript for the current working directory — verify the returned `first_prompt` matches this session before posting.

The script returns one JSON object: cost, per-model token breakdown, effort, subagents, workflows, compactions, churn, tool/MCP counts, and git/PR context. Read it; do not recompute anything from it by hand.

### Cross-check the agent tiers against the spawn log

`~/.claude/agent-spawn.log` is tab-separated `timestamp, session_id, agent_type, model, effort, source, description` — one row per allowed spawn, written by the PreToolUse gate in `~/.claude/hooks/agent-model-gate.mjs`. It records the model the agent type *pins*; `subagents[].models` / `subagents[].efforts` in the JSON records what the transcript shows the agent *actually ran*. Pull this session's rows:

```bash
grep -F "<SESSION_ID>" ~/.claude/agent-spawn.log
```

Use it two ways:

- **Fill `subagent_models`** in the metrics block (below) — the log gives the type→tier mapping in one line without walking every subagent object.
- **Flag a pin that did not hold.** If the log says `scanner → haiku` but that agent's transcript shows `claude-opus-5`, the definition is being ignored and every future scanner is mis-tiered. That is a defect in the setup, not a session cost note — say so in the prose and name both models.

Two things the log does not cover: spawns predating the gate (installed 2026-08-04), and denied spawns (nothing is logged when the gate blocks). Rows missing for a session with subagents means the hook was not loaded — worth one line, because it means nothing was gating the tiers that session. Where the two sources disagree on what ran, the transcript wins.

### On cost accuracy

Cost is computed as tokens × `rates.json`, because no cost figure is persisted anywhere on disk — `stats-cache.json`'s `costUSD` is always `0` and is all-time cumulative regardless.

The formula is calibrated against the `/status` → Usage panel and reproduces it to the cent (verified: a frozen model in a live session read $1.89 against `/status`'s $1.90). It uses **list** rates, cache read at 0.1× input, and cache writes at 1.25× (5m TTL) / 2.0× (1h TTL).

Two known gaps, both intentional:
- Internal haiku calls that generate the session title and away-recap are not written to the transcript, so they are missed (~$0.0006/session).
- `rates.json` ships public list rates; if the account's negotiated rates differ, edit `rates.json` — it is the single source of truth for pricing.

If a number looks wrong, cross-check against `/status` → Usage before posting.

### Metered vs subscription — check this before you write "cost"

Token collection is plan-agnostic and correct on any account. The **dollar figure is not**: on a metered account it approximates the invoice, but on a subscription there is no per-token charge, so the same number is only a consumption proxy.

**The transcript carries no plan marker** — `service_tier` is always `standard` and there is no billing/entitlement field — so this cannot be auto-detected. It comes from `plan` in `rates.json`, overridable per run with `--plan=subscription` or `STATS_REPORT_PLAN=subscription`.

Read `cost.plan` / `cost.is_real_money` from the output and label accordingly:

- `metered` → report as cost: `cost_usd: 14.90`.
- `subscription` → the key **must** read `cost_usd_notional`, and the prose must not call it spend. Lead with tokens and `cache_share_of_input` instead, since what actually binds on a subscription is the rate limit, not a bill.

If `rates.json` says `metered` but the session is plainly on a subscription account (or vice versa), stop and confirm with the user rather than posting a mislabelled figure — a channel that mixes real and notional dollars under one key is worse than no channel, because the later cost-per-ticket analysis silently averages them together.

## 2. Write the judgment lines

The script cannot infer these. Write them from what actually happened in this session, in your own words:

- **Summary** — one line on what the session set out to do and whether it landed.
- **Went well** — one line, specific. Not "made good progress".
- **Didn't go well** — one line, honest. Wrong turns, rework, a bug you shipped and had to fix, a wrong assumption, time lost to a tool. If the session genuinely had no friction, write `none`; do not manufacture a flaw, and do not soften a real one.

Use `title`, `recap`, and `first_prompt` from the JSON as memory aids, not as the text — `recap` is written for a returning user and reads wrong in a log.

Tickets and PRs are **plural, not singular** — `git.tickets` is every ticket id this session is
actually driving: found across the branch name, the PR title/head-ref, and any id targeted by a
`Closes`/`Fixes`/`Resolves` keyword in the PR body. `git.related_prs` is every other PR of yours
since this session started that mentions one of those ticket ids (catches a cross-package change
that shipped as two PRs from one continuous session). Report all of them, not just the first. If
`git.tickets` is empty, use `none`.

**`git.mentioned_tickets` is a separate field — never run step 4 on it.** These are ticket ids
that appear somewhere in the PR body *without* a closing keyword: pure background/lineage prose
like "builds on the mobile analytics catalogue (CKTS-276)". Relying on an agent to eyeball the PR
body and filter these out by hand failed three times in a row — most recently CKTS-276 itself,
which got a cost comment and an estimate bump for a session that never touched it, caught only
after the fact and reverted — so the split is now done in `collect.py` before you ever see the
JSON, not left as a judgment call. If `git.mentioned_tickets` is non-empty, name those ids in your
step-5 report and ask the user whether any should actually be attributed; do not decide that
yourself, and do not fold them into `git.tickets`.

## 3. Post

Send with `mcp__claude_ai_Slack__slack_send_message` to `C0BLNJRG83W`. Post directly — the user has authorized this; do not ask for confirmation and do not use a draft.

Prose first, then a fenced metrics block of `key: value` lines so the channel stays greppable for later cost-per-task analysis.

````
*<title>* — `<repo>` / `<branch>`
<one-line summary>

✅ <what went well>
⚠️ <what did not>
<optional: one line on anything notable — compaction, heavy churn, a killed workflow, a runaway subagent fleet>

```
date: 2026-07-29
session_id: e7e06fe8
first_message_at: 2026-07-29T14:02:11Z
tickets: CKTS-242
prs: https://github.com/org/repo/pull/123
cost_usd: 13.85
cost_main_usd: 13.81
cost_subagents_usd: 0.00
cost_workflow_agents_usd: 0.05
models: claude-opus-5=11.92 claude-sonnet-5=1.89 claude-haiku-4-5=0.05
effort: xhigh
api_requests: 58
tokens_output: 50135
tokens_cache_read: 9348195
tokens_cache_write_1h: 562772
cache_share_of_input: 0.99
compactions: 0
compaction_dropped_tokens: 0
subagents: 0
subagent_types: none
subagent_models: none
workflows: 1
workflow_agents: 2
workflow_detail: stats-report-fleet-probe=completed/2agents
files_touched: 6
churn_files: 1
top_tools: Bash=40 Edit=5 Write=5
mcp_calls: 1
wall_ms: 7626463
interruptions: 8
```
````

Rules for the metrics block:
- `tickets` and `prs` are comma-separated when there's more than one (e.g. `tickets: CKTS-242,CKTS-243`), `none` when empty — always these plural keys, never the old singular `ticket:`/`pr:`.
- `first_message_at` is `first_message_at` from the JSON verbatim (UTC ISO8601) — the session's actual start, independent of when this report gets posted.
- Every key appears every time, even when the value is `0` or `none` — stable keys are what make the channel parseable.
- Money to 2 decimals, token counts as bare integers, no thousands separators.
- `models` comes from `cost.by_model_usd` (cost per model across the main loop *and* every agent). `models`, `top_tools`, `subagent_models`, and `workflow_detail` are space-separated `name=value` pairs; cap `top_tools` at the 5 highest.
- `subagent_models` is `type=model×count` per agent type across direct subagents and workflow fleets — e.g. `scanner=haiku×3 explorer=sonnet×2 verifier=opus×1`. Take it from `~/.claude/agent-spawn.log` (see above), falling back to `subagents[].agent_type` + `subagents[].models` when the log has no rows for this session. This is the key that makes a mis-tiered fan-out greppable across the channel rather than visible only in the prose.
- `workflow_agents` is the summed `fleet.agents`, i.e. transcripts actually on disk. If it disagrees with a workflow's `declared_agent_count`, trust the transcript count for cost and say so in the prose — a killed run leaves fewer transcripts than it declared.
- Use `wall_ms` from `span_ms`. Do **not** report `measured_active_ms` as session duration — it only covers the turns that happened to emit a `turn_duration` sample and can undercount by an order of magnitude.
- `files_touched` / `churn_files` are counted from `Edit`/`Write`/`MultiEdit`/`NotebookEdit` tool calls in the transcript, so they are independent of the working tree — a deleted or branch-switched worktree no longer zeroes them, and there is no need to hand-correct from the PR. They count *attempted* writes by path: a file edited and later reverted still counts, and a subagent's edits land in the subagent's transcript, not the parent's. `churn_files` is the subset written more than once.

### Call out an expensive fleet

`cost.by_model_usd` spans the main loop and every agent, so it is where a mis-tiered fan-out becomes visible; `subagent_models` says which agent type put it there. If a large share of session cost sits on a model that only agents ran — `claude-fable-5` or `claude-opus-5` on a fleet of scanners — say so explicitly in the ⚠️ line with the dollar figure. A measured example: one killed `deep-research` run put 54 agents on `claude-fable-5` for **$52.66**, 22% of that session. That is the highest-value signal this report produces; do not bury it in the metrics block.

## 4. Update Linear

For every ticket id in `git.tickets`, post directly — this is pre-authorized the same as the
Slack post, no per-run confirmation:

1. **Find the existing stats-report comment, if any.** `list_comments(issueId: <ticket>)` and
   look for a body containing `**Claude Code cost summary**` (the marker
   `render_session_comment.py` writes). At most one should exist per ticket — if this is the
   ticket's first session, there won't be one.
2. **Merge this session in.**
   ```bash
   echo '<existing comment body, or the literal string none>' > /tmp/existing_body.md
   echo '{"session_id": "<SESSION_ID>", "first_message_at": "<first_message_at>", "cost_usd": <cost.total_usd>, "models": <cost.by_model_usd>, "pr_url": "<this session's PR url for this ticket, or null>"}' > /tmp/session.json
   python3 ~/.claude/skills/stats-report/render_session_comment.py <TICKET-ID> /tmp/existing_body.md /tmp/session.json
   ```
   This reads any prior sessions already recorded in the existing comment's trailing
   `` ```json `` block (labeled "do not edit" — **visible**, not hidden: Linear renders
   `<!-- ... -->` as literal text, unlike GitHub, so don't try to hide state in an HTML
   comment), replaces this session's own row if `/stats-report` is somehow rerun on the same
   session (dedupes by `session_id`, so it never double-counts), and recomputes the total cost
   and cost tier from `~/.claude/skills/stats-report/boundaries.json` (a local snapshot, not a
   read into another skill's directory — see Recalibration drift below). It prints `{body,
   total_cost_usd, session_count, tier, tier_value}`.

   The session table carries an **Estimate** column: the tier for the *cumulative* cost
   through that row, not that session's own cost in isolation (a single session rarely
   crosses a boundary by itself — the running total is what actually moves the ticket's
   estimate). When a row's cumulative tier differs from the row above it, the cell reads
   `PREV → NEW` (e.g. `S → L`) so a tier change is visible directly in the table, not only
   in the summary line above it or in step 4's `save_issue` call.
3. **Post the comment.** `save_comment(issueId: <ticket>, body: <the rendered body>)` to create,
   or `save_comment(id: <existing comment's id>, body: <the rendered body>)` to update in place —
   never stack a second cost-summary comment on the same ticket.
4. **Apply the `Cost Verified` label** (`f02c8ce0-6d3e-420e-b2b0-216b334e320f` on the CKTS team)
   if the ticket doesn't already carry it: `get_issue(<ticket>)` to read current `labelIds`,
   `save_issue` with that set plus the new label id if missing. Never drop an existing label.
5. **Write the recomputed estimate.** `save_issue(id: <ticket>, estimate: <tier_value>)`. Cost
   per ticket is additive across sessions, so the tier only ever holds steady or moves up as more
   sessions land on the same ticket — always write it, there's no "only if it increases" case to
   guard for. (A tier moving *down* only happens via the separate, manually-triggered full-dataset
   boundary recalibration described below — never from this per-session step.)

If `git.tickets` is empty, skip this step entirely — there's nothing to attach the cost to.

### Recalibration drift

`~/.claude/skills/stats-report/boundaries.json` is a local, self-contained snapshot of
`{tiers, tier_value, boundaries}` — this skill never reaches into `ticket-cost-report`'s
directory at runtime. It was copied from `ticket-cost-report/data/reassignment_plan.json` at
the time of the Fibonacci → T-shirt migration. As more `Cost Verified` sessions accumulate,
periodically re-run `ticket-cost-report`'s full pipeline (see its own SKILL.md's Recalibration
note) to recompute the boundaries against the larger dataset, then re-copy the refreshed
`{tiers, tier_value, boundaries}` into this file. This step never recomputes boundaries itself,
only reads the last snapshot copied here.

## 5. Report back

Give the user the Slack permalink the tool returns, the total cost, and — only if something in the numbers is actually worth acting on — one line on it. If step 4 ran, also name which ticket(s) got updated and their new cumulative cost/tier — the write happened without asking, so the report is what makes it visible.

### Do not report `cache_share_of_input` as a problem

It is `cache_read / (input + cache_read + cache_write)`. At 1.0 it means every input token was billed at **0.1×** instead of full rate — the best attainable value. Driving it down means more cache misses and a *bigger* bill. It is a health check, not a defect, and it reads ~1.00 on essentially every session, so it carries no signal worth a line of prose. Do not write "the entire cost is context replay, the lever is session length and `/compact`" — that framing is wrong twice over (see below), and it was repeated verbatim across the first 20 reports in the channel.

### What actually drives the number

Decompose the cost before commenting on it. Per token, at input rate `r`:

| component | rate | typical share |
| --- | --- | --- |
| `tokens_cache_read` | `0.1 r` | ~60% |
| `tokens_cache_write_1h` | **`2.0 r`** | ~35% |
| `tokens_output` | `5 r` | ~10% |

Two consequences the old guidance missed:

- **Cache writes are about a third of the bill**, not a rounding error. 1 written token costs the same as 20 replayed ones. Dumping a large tool payload (an MCP issue dump, a long file, a verbose build log) into context is charged at `2.0 r` on the way in *and* `0.1 r` on every request after. Measured: two Linear-heavy audit sessions ran **79%** and **85%** cache-write.
- **`/compact` is not a free win.** It buys a smaller replay by paying a fresh write at `2.0 r`. Compacting context `C` down to `C'` breaks even after `n = 20 · C' / (C − C')` more requests — e.g. 200k → 40k pays back after ~5. Recommend it only when the session is demonstrably continuing; never as a wrap-up.

### The two numbers that do carry signal

- **`api_requests`.** Cost is close to linear in it: across 20 measured sessions, `cost ≈ 0.22 + 0.181 × api_requests` with **R² = 0.92**. Every avoided round-trip is ~$0.18. Batching independent tool calls into one message, and replacing poll loops with one blocking wait (`gh run watch`, not ten `gh run view`s), is the cheapest available saving.
- **`tokens_cache_read / api_requests`** — mean context per request. It grows with session length, which is what makes long sessions superlinear rather than merely long: <60 requests averages ~108k/req, >220 requests averages ~372k/req. When this is high, the fix is pushing wide reads into subagents (their output never joins the parent prefix), not compaction.

### Compare against the baseline before commenting

Both baselines come from the first 20 sessions in this channel. Compute, then only speak if a threshold trips:

| check | baseline | flag when |
| --- | --- | --- |
| cost vs requests | `expected = 0.22 + 0.181 × api_requests` | `cost_usd > 1.4 × expected` |
| context per request | `tokens_cache_read / api_requests` | `> 250_000` |
| write share | `tokens_cache_write_1h × 2.0 / (that + cache_read × 0.1)` | `> 0.5` |

What each one means, when it trips:
- **Over the cost line** — the session paid more per round-trip than its request count explains. Usually a fat context (check the next row) or Opus where Sonnet would have done. Reference class: coding sessions run $0.17/request, Linear/MCP-heavy ones $0.36, a pure-Sonnet session ran $0.064.
- **Context per request > 250k** — the transcript is carrying dead weight, typically an abandoned wrong turn. Name it, and say a fresh session was the cheaper move.
- **Write share > 0.5** — large payloads were pulled into context (MCP dumps, long files, verbose logs) rather than read out-of-band. This is the one that stock advice always misses.

State whichever is actually anomalous. If nothing trips, say nothing — a report with no advice is better than a report repeating a stock line.
