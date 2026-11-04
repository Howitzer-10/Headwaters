# Providers

One canonical set of plain provider names is shared by the CLI (`headwaters keys set|list|test`)
and the dashboard and tray: `KeyStore.providers()` in `headwaters/core/keys.py`. A person types
`headwaters keys set nvidia`; no environment-variable name is shown or needed. A short form (`hf`)
or an environment-variable spelling typed by habit is normalised to the same provider, never stored
as a second alias. A name that is not a provider and not an existing alias is refused unless
`--provider` says which provider your own alias is for, so a key the router can never spend is
never saved. `--json` output keeps an `env_var` field as a machine contract.

The cloud providers below are `catalog.providers` rows in `catalog/seed_catalog.json` (schema:
`ProviderEntry` in `headwaters/core/catalog/schema.py`, loaded by `headwaters/core/providers.py`).
Every fact was read from the provider's own public docs or an anonymous request on 2026-11-03 and
is recorded in `docs/SDK_FACTS.md` (section "Cloud provider APIs (HDW-514)").

Two senses of "free" are kept apart (HDW-519, decision D1): **whether money may change hands**
(`charges_money`) and **how a free path exists** (`free_access` = `none`, `marked`, `all` or
`plan`). One formatter, `format_access` in the schema, renders the pair, and every surface prints
its string: `free to use`, `sells usage`, `sells usage · free models marked`, `sells usage · free
on the free plan`. The second column below is the row's routing tier; the third is what the row
prints to a person. `tier` is still read as the derivation when a row states neither field (D2),
so an unmigrated catalog is legal: `free_cloud` derives `charges_money: false, free_access: all`,
and `paid` derives `charges_money: true` with `free_access: marked` when `honor_free_markers` is
on, else `none`.

Rate limits in `free_limits` are **advisory, not enforced** (D5): a counted request ranks a model
down, it never refuses a call, and the counter is never shown as a promise.

SUPPORTED: nvidia | free_cloud | free to use | key required | verified 2026-11-03 | https://build.nvidia.com/api/runtime/content/help-center-faq.yaml | all models free to prototype, about 40 requests per minute (counted per key), trial-only and logged; the model list is anonymous, so `keys test` is unsupported
SUPPORTED: huggingface | paid | sells usage · free models marked | key required | verified 2026-11-03 | https://huggingface.co/docs/inference-providers/index | free accounts have no credits; a model is treated as free only if the listing says `is_free`; `keys test` uses the Hub whoami endpoint
SUPPORTED: mistral | paid | sells usage · free on the free plan | key required | verified 2026-11-03 | https://docs.mistral.ai/api/ | the free Experiment plan is a plan on the account, not a marking on a model: nothing in the API tells a free key from a paid one, so no model counts as free on every account, and a key on the free plan gets free requests; `keys test` lists models
SUPPORTED: pollinations | paid | sells usage | key required | verified 2026-11-03 | https://gen.pollinations.ai/llms.txt | no keyless generation is documented, so a key is required; `keys test` is unsupported
SUPPORTED: kilo | paid | sells usage · free models marked | key optional | verify: false (2026-11-03) | https://kilo.ai/docs/gateway/api-reference | models marked free work without a key at 200 requests per hour per IP; every other model needs a key; not yet verified with a key: keyless discovery answers 200 and lists `kilo-auto/free`, and a keyless call answers 200 routed to `stealth/glyph-cluster` at cost 0, but the owner's stored key returns HTTP 401 `INVALID_TOKEN`, so a keyed call is pending and the key needs clearing or replacing; `keys test` is unsupported
SUPPORTED: opencode | paid | sells usage | key required | verified 2026-11-03 | https://opencode.ai/docs/zen/ | OpenCode Console: pay as you go. The Console free-tier models (`... Free`, cost 0 in the listing) are app-only: calling one returns HTTP 429 `FreeUsageLimitError` with or without a key, so those ids are never offered and every callable model is paid. That 429 covers only the Console/Zen `-free` ids and was not re-tested; it does not cover the Go plan's `$0` ids (see the opencode-go row). (An HTTP 403 here means Cloudflare rejected the user agent, not that the key is bad.); `keys test` lists the models
SUPPORTED: opencode-go | paid | sells usage · free models are plan access | key required | verified 2026-11-03 | https://opencode.ai/docs/go/ | the Go plan is a subscription (Go, Go Plus, Go Teams), so the tier is paid although Headwaters has no entitlement check and the key is the only gate; its two `$0` ids are `free_access: "plan"` (D23), not `"all"`, because using them consumes the subscription allowance: on the Go base `beam-free` answers 200 at cost 0 with `billing_source: go`, and `iceman-contributor-free` answers 400 on the workspace Privacy setting (it trains on request data) - neither is 429 or app-only; every call must carry a stable per-conversation id in the `x-opencode-session` header or it is refused with HTTP 400 `MissingSessionID` (the catalog row's `session_header` field supplies it from the router session key); `keys test` lists the models
SUPPORTED: gemini | free_cloud | sells usage · free on the free plan | key required | UNVERIFIED 2026-11-03 (P3) | https://ai.google.dev/gemini-api/docs/openai | an AI Studio key has free requests per model per day and a billing-enabled key is charged; the OpenAI-compatibility base answers, but `GET /v1beta/openai/models` is 404, so discovery cannot list this provider yet (follow-up: a native `/v1beta/models` adapter)
SUPPORTED: groq | free_cloud | sells usage · free on the free plan | key required | UNVERIFIED 2026-11-03 (P2, P5) | https://console.groq.com/docs/rate-limits | every model is callable on the free plan at the organisation's lower rate limits; a paid plan raises the limits rather than unlocking models
SUPPORTED: cerebras | free_cloud | sells usage · free on the free plan | key required | UNVERIFIED 2026-11-03 (P2) | https://inference-docs.cerebras.ai/support/pricing | the free plan is a rate-limited daily token allowance with no card, and a paid tier raises it
SUPPORTED: cohere | free_cloud | sells usage · free on the free plan | key required | UNVERIFIED 2026-11-03 (P2) | https://docs.cohere.com/docs/rate-limits | a trial key is a free plan with per-key limits under non-commercial terms, and a production key is charged; the compatibility base lists models

An unverified row keeps `verify: false` and a `note` naming the probe that could not be run, so a
reader can tell "checked and wrong" from "not checked". `cloudflare` has a `free_limits` row and no
provider row: its Workers AI URLs carry the account id inside the path and provider rows have no
way to template it yet (follow-up: `base_url_vars`).

Not every provider named in `free_limits` is a `providers` row, and not every `providers` row has a
`free_limits` entry; the two lists answer different questions (where a call goes, what an allowance
is). `free_notes` is prose for a person and is never parsed by code — the structured pair is the
only thing that drives behaviour.

Residual risk: `free_access: plan` says a free path exists, not that *this* key is on it. Nothing in
a provider's API reveals the account's plan, so a model from a `plan` row is reached on the same
terms either way — free if your key is on the free plan, charged if it is not, and Headwaters
counts it as free either way.

`opencode` and `opencode-go` are two products with two endpoints (Console and Go), so each has its
own chat base URL and its own listing URL. One key variable covers both: a key you save is kept per
provider name and never answers for the other provider, while a key in the environment reaches
both. Environment-variable names are internal (see above) and are never printed.

`anthropic`, `openai`, `openrouter` and `ollama` are built in and are not catalog rows.

## Add a provider as data

1. Verify the base URL, auth header, model-list endpoint and any free tier against the provider's
   own public docs. Never copy a slug, limit or price from memory or a competitor.
2. Add a row to `providers` in the catalog (`name`, `title`, `env_var`, `base_url`, `tier`,
   `charges_money`, `free_access`, `source_url`, `checked`; optionally `metadata_url`,
   `models_endpoint`, `honor_free_markers`, `key_required`, `skip_id_pattern`, `retains_data`,
   `session_header`, `note`). The two labels are the fact a reviewer needs: does money ever change
   hands, and is a free path marked per model, true of every model, or a plan on the account?
   `tier` still decides what an unmarked model costs when routing. The tier is `free_cloud`
   only where the provider says so, otherwise `paid`. A listing that is not reachable at
   `base_url + models_path` gets an explicit `models_endpoint`; a provider whose listing names ids
   it serves only to its own app gets a `skip_id_pattern` (discovery drops those ids and
   `TargetBuilder` refuses them). A provider that refuses a call without a per-conversation id gets
   a `session_header` naming that header (`opencode-go` and `x-opencode-session`): the executor
   fills it with the router's session key for that conversation, and it raises rather than send a
   call the provider would reject. Two products of one company may share one `env_var`.
3. Add one row per verified fact to `docs/SDK_FACTS.md` and point the `docs/ASSUMPTIONS.md` row at
   it.
4. Regenerate the schema: `uv run python -m headwaters.core.catalog.loader --write
   catalog/catalog.schema.json`.

No `rules.yaml` edit is needed: `headwaters keys set <name>` and the dashboard pick the provider
up from `KeyStore.providers()`.
