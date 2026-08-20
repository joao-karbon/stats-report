---
name: stats-report
description: Post an end-of-session cost and activity report for the CURRENT session to a Slack channel. Use when the user runs /stats-report, or asks to log/record this session's stats, cost, or usage to Slack. Builds a record for later cost-per-task analysis.
allowed-tools: Bash, Read, <your-slack-mcp-tool>
---

# /stats-report

Collect this session's metrics, write the judgment lines yourself, post to Slack. Run this at the end of a task or session, from inside that same session.

Channel: `#your-channel-name` → `channel_id: <YOUR_CHANNEL_ID>` (private). **Set this up before use — see the README.**

## 1. Collect

```bash
python3 ~/.claude/skills/stats-report/collect.py <SESSION_ID>
```

Get `<SESSION_ID>` from your scratchpad path — it is the directory component immediately above `scratchpad` (`/private/tmp/claude-<n>/<cwd-slug>/<SESSION_ID>/scratchpad`). If you cannot determine it, omit the argument and the script falls back to the most recently modified transcript for the current working directory — verify the returned `first_prompt` matches this session before posting.

The script returns one JSON object: cost, per-model token breakdown, effort, subagents, workflows, compactions, churn, tool/MCP counts, and git/PR context. Read it; do not recompute anything from it by hand.

### Cross-check the agent tiers against the spawn log

`~/.claude/agent-spawn.log` is tab-separated `timestamp, session_id, agent_type, model, effort, source, description` — one row per allowed spawn, written by a PreToolUse gate on the Agent tool (see `agent-model-gate.mjs` in the Claude Code hooks docs if you want to set one up). It records the model the agent type *pins*; `subagents[].models` / `subagents[].efforts` in the JSON records what the transcript shows the agent *actually ran*. Pull this session's rows:

```bash
grep -F "<SESSION_ID>" ~/.claude/agent-spawn.log
```

Use it two ways:

- **Fill `subagent_models`** in the metrics block (below) — the log gives the type→tier mapping in one line without walking every subagent object.
- **Flag a pin that did not hold.** If the log says `scanner → haiku` but that agent's transcript shows `claude-opus-5`, the definition is being ignored and every future scanner is mis-tiered. That is a defect in the setup, not a session cost note — say so in the prose and name both models.

If you have no such gate/log set up, this section simply doesn't apply — `subagent_models` falls back to `subagents[].agent_type` + `subagents[].models` from the JSON, no log required.

### On cost accuracy

Cost is computed as tokens × `rates.json`, because no cost figure is persisted anywhere on disk — `stats-cache.json`'s `costUSD` is always `0` and is all-time cumulative regardless.

The formula is calibrated against the `/status` → Usage panel and should reproduce it closely. It uses **list** rates, cache read at 0.1× input, and cache writes at 1.25× (5m TTL) / 2.0× (1h TTL).

Two known gaps, both intentional:
- Internal haiku calls that generate the session title and away-recap are not written to the transcript, so they are missed (~$0.0006/session).
- `rates.json` ships public list rates; if your account's negotiated rates differ, edit `rates.json` — it is the single source of truth for pricing.

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

Ticket: prefer the PR title/number from `git.pr`. Otherwise parse the branch name (e.g. `claude/ckts-242-investigation` → `CKTS-242`). If neither yields one, use `none`.

## 3. Post

Send with your Slack MCP tool to `<YOUR_CHANNEL_ID>`. Post directly — the user has authorized this; do not ask for confirmation and do not use a draft.

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
ticket: CKTS-242
pr: https://github.com/org/repo/pull/123
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
- Every key appears every time, even when the value is `0` or `none` — stable keys are what make the channel parseable.
- Money to 2 decimals, token counts as bare integers, no thousands separators.
- `models` comes from `cost.by_model_usd` (cost per model across the main loop *and* every agent). `models`, `top_tools`, `subagent_models`, and `workflow_detail` are space-separated `name=value` pairs; cap `top_tools` at the 5 highest.
- `subagent_models` is `type=model×count` per agent type across direct subagents and workflow fleets — e.g. `scanner=haiku×3 explorer=sonnet×2 verifier=opus×1`. Take it from `~/.claude/agent-spawn.log` if you have one (see above), falling back to `subagents[].agent_type` + `subagents[].models` otherwise. This is the key that makes a mis-tiered fan-out greppable across the channel rather than visible only in the prose.
- `workflow_agents` is the summed `fleet.agents`, i.e. transcripts actually on disk. If it disagrees with a workflow's `declared_agent_count`, trust the transcript count for cost and say so in the prose — a killed run leaves fewer transcripts than it declared.
- Use `wall_ms` from `span_ms`. Do **not** report `measured_active_ms` as session duration — it only covers the turns that happened to emit a `turn_duration` sample and can undercount by an order of magnitude.
- `files_touched` / `churn_files` are counted from `Edit`/`Write`/`MultiEdit`/`NotebookEdit` tool calls in the transcript, so they are independent of the working tree — a deleted or branch-switched worktree no longer zeroes them, and there is no need to hand-correct from the PR. They count *attempted* writes by path: a file edited and later reverted still counts, and a subagent's edits land in the subagent's transcript, not the parent's. `churn_files` is the subset written more than once.

### Call out an expensive fleet

`cost.by_model_usd` spans the main loop and every agent, so it is where a mis-tiered fan-out becomes visible; `subagent_models` says which agent type put it there. If a large share of session cost sits on a model that only agents ran, say so explicitly in the ⚠️ line with the dollar figure. That is the highest-value signal this report produces; do not bury it in the metrics block.

## 4. Report back

Give the user the Slack permalink the tool returns, the total cost, and — only if something in the numbers is actually worth acting on — one line on it.

### Do not report `cache_share_of_input` as a problem

It is `cache_read / (input + cache_read + cache_write)`. At 1.0 it means every input token was billed at **0.1×** instead of full rate — the best attainable value. Driving it down means more cache misses and a *bigger* bill. It is a health check, not a defect, and it reads ~1.00 on essentially every session, so it carries no signal worth a line of prose. Do not write "the entire cost is context replay, the lever is session length and `/compact`" — that framing is wrong twice over (see below).

### What actually drives the number

Decompose the cost before commenting on it. Per token, at input rate `r`:

| component | rate | typical share |
| --- | --- | --- |
| `tokens_cache_read` | `0.1 r` | ~60% |
| `tokens_cache_write_1h` | **`2.0 r`** | ~35% |
| `tokens_output` | `5 r` | ~10% |

Two consequences the old guidance missed:

- **Cache writes are about a third of the bill**, not a rounding error. 1 written token costs the same as 20 replayed ones. Dumping a large tool payload (an MCP issue dump, a long file, a verbose build log) into context is charged at `2.0 r` on the way in *and* `0.1 r` on every request after.
- **`/compact` is not a free win.** It buys a smaller replay by paying a fresh write at `2.0 r`. Compacting context `C` down to `C'` breaks even after `n = 20 · C' / (C − C')` more requests — e.g. 200k → 40k pays back after ~5. Recommend it only when the session is demonstrably continuing; never as a wrap-up.

### The two numbers that do carry signal

- **`api_requests`.** Cost is close to linear in it in practice. Every avoided round-trip is real money. Batching independent tool calls into one message, and replacing poll loops with one blocking wait (`gh run watch`, not ten `gh run view`s), is the cheapest available saving.
- **`tokens_cache_read / api_requests`** — mean context per request. It grows with session length, which is what makes long sessions superlinear rather than merely long. When this is high, the fix is pushing wide reads into subagents (their output never joins the parent prefix), not compaction.

### Compare against your own baseline before commenting

Once you have a few dozen reports in your channel, compute your own baselines rather than reusing anyone else's numbers — cost-per-request and cache-write share both vary a lot by workload (coding vs. MCP/API-heavy sessions). Suggested checks to calibrate:

| check | formula | flag when |
| --- | --- | --- |
| cost vs requests | fit `cost ≈ a + b × api_requests` on your own history | actual `> 1.4 × expected` |
| context per request | `tokens_cache_read / api_requests` | notably above your own median |
| write share | `tokens_cache_write_1h × 2.0 / (that + cache_read × 0.1)` | `> 0.5` |

What each one means, when it trips:
- **Over the cost line** — the session paid more per round-trip than its request count explains. Usually a fat context (check the next row) or a heavier model where a lighter one would have done.
- **Context per request is high** — the transcript is carrying dead weight, typically an abandoned wrong turn. Name it, and say a fresh session was the cheaper move.
- **Write share > 0.5** — large payloads were pulled into context (MCP dumps, long files, verbose logs) rather than read out-of-band. This is the one that stock advice always misses.

State whichever is actually anomalous. If nothing trips, say nothing — a report with no advice is better than a report repeating a stock line.

## 5. Optional: writing a cost tier back to an issue tracker

Some setups extend this skill to also write a recomputed cost tier into an issue tracker's
estimate/points field for the ticket this session touched. If yours does, don't treat "cost is
additive across sessions, so the tier only holds steady or rises" as license to always overwrite
whatever is already there.

That property only holds when you're comparing this session's recomputed tier against a tier
*this same tracking mechanism* previously wrote to that ticket — summing another session's cost
onto a running total can't produce a lower total. It says nothing about a value that was already
on the ticket for an unrelated reason, e.g. a human's up-front scope estimate set at ticket
creation, sized for the ticket's full work rather than for cost observed so far.

Before writing:

- **No prior cost-tracking history on this ticket** (this is the first session your tooling has
  recorded against it): write the recomputed tier unconditionally, even if it's lower than
  whatever estimate is already there. There's no accumulated-cost history for a decrease to
  violate — a pre-existing scope estimate above a freshly-computed single-session tier is the
  normal case, not an exception to guard against.
- **Prior sessions are already tracked**: the new cumulative tier is guaranteed to be ≥ the last
  tracked value. If it somehow comes out lower, your tier boundaries changed underneath you —
  stop and flag it instead of silently overwriting.
