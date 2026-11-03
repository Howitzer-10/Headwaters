# Decisions report: "paid" vocabulary

Docs-only change. No Python, no ticket packet, no code.

- Branch: `howitzer-10-decisions-paid-vocabulary`
- Decision commit: `f95557c` — `HDW-DECISIONS: separate paid tier from paid product vocabulary`
- Files changed: `docs/DECISIONS.md` (this report is added separately)

## What was added

1. **One table row, D19**, in `docs/DECISIONS.md`. It says the word "paid" is never written bare
   because the product uses it for two different things:
   - a **paid tier** is a provider whose use costs the person money, gated by the existing local
     `features.paid_scopes` flag — the sense HDW-503 / 504 / 514 already use;
   - a **paid product** is a capability sold to the person as part of the app (for example
     intelligent paid routing). `features.paid_scopes` is a local boolean in `rules.yaml`, not an
     entitlement check, and selling a capability needs a real entitlement or licence check that does
     not exist yet.

   Code, docs, CLI output and UI text must qualify which sense is meant (`paid tier`,
   `licensed feature`), so a grep can tell them apart. The stated why: the two senses would
   otherwise be conflated exactly the way environment-variable names and provider names were before
   D18, and an entitlement bug here spends real money.

2. **A new subsection, "Decided, not yet built"**, under the table, clearly marked as
   forward-looking and not a decision to implement now:
   - a credits wall stops the product and tells the person; it never silently downgrades to a free
     model, and any downgrade to free is explicit, visible and opt-in (user directive, 2026-11-03);
   - cost- and quality-aware routing for paid-tier models, driven by a per-model price ledger,
     benchmark data and other metadata-derived ratings, is wanted but not designed or ticketed;
   - provenance: this machine already runs a separate MCP-only solution, VRAMWeave-MCP, registered
     as the MCP server `vramweave-mcp`. It has done related pricing and metric work and should be
     reviewed when that capability is ticketed. The pointer was identified by the MCP server name
     only; the source was not surveyed and nothing was designed from it.

## Why D19

The highest existing table row on this branch was D18 (provider identity). `git grep -n D19` across
the branch returned nothing, so D19 was genuinely unused before this change.

Numbering context:

- **D16 has no table row**, only a "D16 detail" section. That is a pre-existing gap, not introduced
  or fixed here.
- **D17 is deliberately skipped** by the existing numbering note (claimed independently by HDW-512
  and HDW-513 on unmerged branches).
- The table already runs D1-D15, then D18. D19 is the first free number after the highest existing
  row, so it does not collide with either branch's claimed D17.

## Ambiguous existing uses of "paid" (listed, not fixed)

New row reads at `docs/DECISIONS.md:21`; new subsection at `docs/DECISIONS.md:25-31`. Pre-existing
bare uses that D19 now makes ambiguous:

| Location | Text | Sense | Why ambiguous |
| --- | --- | --- | --- |
| `docs/DECISIONS.md:17` | D13 | `` `tier=free_cloud`/`paid` `` and `` `free_cloud` or `paid` `` | paid tier | The token is a bare tier enum value. The surrounding tier list makes it a paid tier, but the word itself is unqualified. |
| `docs/DECISIONS.md:88` | D10 detail | "leaves the paid differentiators" | paid product | Means paid/★ features sold as part of the app, not a provider tier. Bare. |
| `docs/DECISIONS.md:89` | D10 detail | "budget-aware paid escalation" | paid product | Same: a premium capability, not a provider tier. Bare. |

None of these were edited, per the brief. Only the new text uses the qualified forms.

Scope note: this scan covers `docs/DECISIONS.md` only, as instructed. Other documents and code were
not searched for bare "paid" uses; that wider sweep is out of scope here.

## Deliberately left alone

- Existing decision rows were not renumbered, reworded or reordered.
- D16's missing table row was not added.
- The D17 numbering note was not changed.
- No ticket packet (HDW-503 / 504 / 514 / 515 or any other) was touched.
- VRAMWeave-MCP was recorded by name only; it was not surveyed, opened or used for design.
- The release gate and `docs/PLAN.md` were not touched; D19 adds no stage or gate work.

## Verification

1. `git diff --stat` against the base shows only `.md` files (`docs/DECISIONS.md` for the decision
   commit; this report separately).
2. Exactly one new decision row was added (D19) and each D id appears once in the table
   (`D1`-`D15`, `D18`, `D19`).
3. No ticket packet file changed.
