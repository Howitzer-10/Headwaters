# Plan: Stage 1 (dogfooding) and Stage 2 (distribution)

This page is the single source of truth for where the project is and what happens next. It records
the owner's decision (decision record D16 in [DECISIONS.md](DECISIONS.md)) to split the remaining
work into two stages, and it is the page to read before starting any release-shaped task.

Short version:

- **Stage 1 is active now.** The owner runs the build from source on their own machine, uses it, and
  functionally tests it. Ticket work then continues through Phases 5, 6 and 7. Nothing is packaged
  or published.
- **Stage 2 is deferred.** Distribution and sale work starts only after Phase 7 is complete. Until
  then it is out of scope, not backlog pressure.

## Stage 1: owner dogfooding and functional testing (ACTIVE NOW)

What happens in Stage 1, in order:

1. The owner installs the current source checkout on their own Windows machine and runs it as their
   normal tool. See [DOGFOOD.md](DOGFOOD.md).
2. The owner walks the hand-run checks in [FUNCTIONAL_TESTS.md](FUNCTIONAL_TESTS.md) and records what
   breaks.
3. Bugs and rough edges found this way become tickets or follow-ups. They may reorder the remaining
   backlog; they do not create release work.
4. Ticket work continues: Phase 5 (tickets 501-509), then Phase 6 (601-608), then Phase 7
   (701-706).

Explicitly **not** in Stage 1:

- no packaging and no installer distribution;
- no code signing;
- no clean Windows VM run;
- no Linux payload and no Linux release;
- no itch.io pages or uploads, and no store copy;
- no closed alpha and no public beta;
- no marketing;
- no attempt to turn the release gate green.

The two documents for this stage are [DOGFOOD.md](DOGFOOD.md) (how to run the current build from
source) and [FUNCTIONAL_TESTS.md](FUNCTIONAL_TESTS.md) (what to check by hand).

## Stage 2: distribution and sale (DEFERRED until Phase 7 is complete)

Stage 2 is everything on the path from a build the owner trusts to something other people can install
and, eventually, buy. It starts only when Phase 7 is complete, that is when tickets 501-509, 601-608
and 701-706 are all done. That is the start condition and it is unchanged; once Stage 2 has started,
Phase 8 (tickets 801-803) runs as part of it.

Stage 2 contains:

- the Windows installer build, and signing it;
- the clean Windows VM run;
- the Linux payload and the clean Linux run;
- the itch.io `windows-beta` and `linux-beta` uploads;
- the store copy;
- the 11 owner evidence steps recorded in `docs/release/evidence.json`;
- the release gate going green because those evidence steps are recorded and true;
- RC hardening and the open-source-readiness review (Phase 8, ticket 801);
- the decision evidence report (Phase 8, ticket 802);
- applying the 1.0 decision, conditional on the owner's decision (Phase 8, ticket 803).

This page deliberately does not repeat the Stage 2 detail. That detail lives in:

- [BETA.md](BETA.md): what beta means, the closed-alpha exit criteria, and the tester list;
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md): the Windows build, signing, clean-VM and Windows
  upload steps;
- [RELEASE_CHECKLIST_LINUX.md](RELEASE_CHECKLIST_LINUX.md): the Linux payload, clean Linux run and
  Linux upload steps;
- [RELEASE_EVIDENCE.md](RELEASE_EVIDENCE.md): what the evidence file is and how a step is recorded.

## The release gate is expected to be red

`uv run headwaters release-gate` currently exits 1. It reports every machine check passing and every
owner step unrecorded, and it lists the blocked owner steps by name.

**That is the correct state during Stage 1.** The owner steps are Stage 2 work by definition: they
are assertions that a person did something on a real machine (a clean VM run, a signed installer, an
upload). Nobody should treat the red gate as a bug to fix, and nobody should work around it, disable
it, or edit a document so that a line turns green. The gate and its owner steps belong to Stage 2 and
stay exactly as they are until Stage 2 runs them for real.

The gate's own wording still talks about beta 0.1 because the tool was written before this split.
That wording is not edited either; it becomes accurate when Stage 2 is reached.

## This build is not a beta

The current build is **not** a public beta, not a closed alpha, and has no published version number.
It reports version `0.0.0` because nothing has been released. Phase 4 (tickets 401-407) produced the
first complete build, and that build exists for owner evaluation only, which is exactly what Stage 1
is for.

If the owner's evaluation concludes the project is not worth taking to market, the plan simply stops
there. Nothing has been published, so nothing has to be withdrawn.

## Phase map

The phase names below are the backlog's internal labels. A "Beta 0.x" label names a phase of ticket
work; it does not mean a build with that version is published to anyone.

| Phase | Tickets | Theme | Stage |
|-------|---------|-------|-------|
| Phase 4 | 401-407 | first complete build, for owner evaluation only | Stage 1, done |
| Phase 5 | 501-509 | Beta 0.2 | Stage 1, ticket work after the functional test pass |
| Phase 6 | 601-608 | Beta 0.3 | Stage 1, after Phase 5 |
| Phase 7 | 701-706 | Beta 0.4 | Stage 1, after Phase 6, and the gate into Stage 2 |
| Phase 8 | 801-803 | 1.0 | Stage 2, after Phase 7 |
| Stage 2 | the 11 owner steps | distribution and sale | starts when Phase 7 is complete |

The ticket rows themselves live in
[headwaters_spec/headwaters_ticket_backlog.md](../headwaters_spec/headwaters_ticket_backlog.md);
this table only maps phases to stages.

## Where the decisions live

- D16 in [DECISIONS.md](DECISIONS.md) records the two-stage split as binding: no public beta, no
  closed alpha, no packaging, signing, itch.io or Linux release work during Stage 1, and the release
  gate left red and unmodified until Stage 2.
- D1 and D9 in the same file cover the open-source and privacy positions that Stage 2 assumes.
