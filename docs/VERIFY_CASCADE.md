# The verify cascade

One downward retry after a **mechanical** check fails. Off by default (HDW-517).

## What it detects

Only structural faults in our own answer, caught by the verification gate that already exists:

- empty or truncated output,
- invalid JSON against a schema the caller supplied,
- malformed tool-call arguments,
- a run where nothing could be checked at all (reported as "not checked", never as a pass).

The check runs in our process on our own output, so a failure is our observation, not a host's
opinion.

## What it does **not** detect

It does not judge quality. It cannot tell a correct answer from a wrong one, a helpful answer from
a useless one, or a well-written answer from a clumsy one. A cascade under this rule fires on
*malformed drafts*, not on *unsatisfying drafts*. There is no quality score anywhere in the router,
and none is added here: what is reported is a check result (`pass` / `fail` / `not checked`).

It also does not react to a host regenerating a request, to a host's own lint/test result, or to a
thumbs-down. None of those signals exist in our five endpoints today, and guessing at a regenerate
would let an ordinary follow-up silently downgrade the next answer.

## Turning it on

```yaml
router:
  tier_order: descend        # required: round-robin repeats a tier before it descends
  tier_direction: cloud_first  # required: a cascade may only move toward the cheap end
  verify_cascade: true
  # verify_cascade_model: "qwen3:8b"   # optional: that model or no cascade at all
  # verify_cascade_tasks: [code]       # which task classes may cascade (default: code)
```

`tier_order: descend` is required because the cascade targets a candidate from a **strictly lower**
tier than the model that drafted; round-robin ordering can hand a tier a second attempt before any
tier descends, which is the opposite of what a cascade needs.

`tier_direction: cloud_first` is required for the same reason aimed at money: a cascade may only
move toward the cheap end, and under `local_first` the walk already starts there, so the next tier
down the walk is `openrouter_auto` or `paid` — a cascade that charges. Refusing it at load time
keeps `cascade_to` honest, since that column is separate from `escalated_to` precisely because it
means "did not spend more". An invalid combination is refused at load time with a message naming
the fix.

Cascading on a task is decided by `verify_cascade_tasks`, using the same task vocabulary as the
score table. When the cascade is enabled for a task, the gate runs for that task even under
`verify: "off"` — a cascade with no gate could never fire, and silently never firing is worse than a
wider gate.

## The rules it keeps

- **One hop.** A failing second answer is returned, flagged, exactly as a failing first answer is
  today. There is no third model and no walk down every tier.
- **Upward first.** An escalation is a better answer; a cascade is a cheaper answer of last resort.
  The cascade runs only after the escalation path returns nothing.
- **Strictly lower tier**, in the configured direction. A candidate on the same tier is not a valid
  target, whatever its score.
- **Pins win.** A pinned or `pin_strict` request never cascades: the pin is an instruction, not a
  suggestion.
- **Walls are respected.** An exhausted lower tier is seen as absent, so a wall means no descent —
  never a bypass.
- **No failure-memory record.** A malformed answer is not a provider outage; the drafting model is
  not remembered as failed.
- **No streaming.** A stream does not retry or cascade; the caller already has the tokens.
- **Downward, never toward money.** A cascade may not target a tier that charges money when the
  failed model's tier did not, so `tier_direction: local_first` is refused at load time; on the
  `cloud_first` walk everything below the drafting tier's `free_cloud` floor is free.
- **Honest logging.** The outcome row records `cascade_to` separately from `escalated_to`, because
  `escalated_to` means "spent more" and the ledger reads it as such.

## What a person sees

- `checks passed`, `checks failed`, `not checked` — the existing words, unchanged.
- `cascade_to: <model>` on the outcome row when the second answer is the one returned, whether it
  passed or not.
- A note on the result naming the model that drafted and the model that cascaded, e.g.
  `openrouter/free: drafts checks failed, cascading to qwen3:8b`.
