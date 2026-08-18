"""Shared cost-tier constants and lookup, used by both the one-time migration script
and the live per-session ticket-comment updater.

TIER_VALUE is Linear's actual stored numeric value per tier under the Extended T-Shirt
scale -- confirmed empirically (get_issue on 5 manually-set tickets after the CKTS team's
scale switch), not a fresh 1-7 sequence. See SKILL.md.
"""

TIERS = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]
TIER_VALUE = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8, "XXL": 13, "XXXL": 21}
VALUE_TIER = {v: t for t, v in TIER_VALUE.items()}


def quantile_boundaries(costs, k):
    costs = sorted(costs)
    n = len(costs)

    def pct(p):
        idx = (n - 1) * p
        f = int(idx)
        c = min(f + 1, n - 1)
        return costs[f] + (costs[c] - costs[f]) * (idx - f)

    return [pct(i / k) for i in range(k + 1)]


def tier_for_cost(cost, boundaries):
    for i in range(len(TIERS)):
        lo, hi = boundaries[i], boundaries[i + 1]
        if cost < hi or i == len(TIERS) - 1:
            if cost >= lo or i == 0:
                return TIERS[i]
    return TIERS[-1]
