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

SUPPORTED: nvidia | free_cloud | key required | verified 2026-11-03 | https://build.nvidia.com/api/runtime/content/help-center-faq.yaml | all models free to prototype, about 40 requests per minute, trial-only and logged; the model list is anonymous, so `keys test` is unsupported
SUPPORTED: huggingface | paid | key required | verified 2026-11-03 | https://huggingface.co/docs/inference-providers/index | free accounts have no credits; a model is treated as free only if the listing says `is_free`; `keys test` uses the Hub whoami endpoint
SUPPORTED: mistral | paid | key required | verified 2026-11-03 | https://docs.mistral.ai/api/ | a free mode exists in the docs but nothing in the API tells a free key from a paid one, so it stays paid; `keys test` lists models
SUPPORTED: pollinations | paid | key required | verified 2026-11-03 | https://gen.pollinations.ai/llms.txt | no keyless generation is documented, so a key is required; `keys test` is unsupported
SUPPORTED: kilo | paid | key optional | verified 2026-11-03 | https://kilo.ai/docs/gateway/api-reference | models marked free work without a key at 200 requests per hour per IP; every other model needs a key; `keys test` is unsupported
SUPPORTED: opencode | paid | key required | verified 2026-11-03 | https://opencode.ai/docs/zen/ | OpenCode Console: pay as you go. The free-tier models (`... Free`, cost 0 in the listing) work only inside the OpenCode app — other clients get HTTP 403 even with a key — so those ids are never offered and every callable model is paid; `keys test` lists the models
SUPPORTED: opencode-go | paid | key required | verified 2026-11-03 | https://opencode.ai/docs/go/ | the Go plan is a subscription (Go, Go Plus, Go Teams); Beam Free and Iceman Contributor Free are included in it at no per-call price, but the plan itself is paid, so no model here counts as free; `keys test` lists the models

`opencode` and `opencode-go` are two products with two endpoints (Console and Go), so each has its
own chat base URL and its own listing URL. One key variable covers both: a key you save is kept per
provider name and never answers for the other provider, while a key in the environment reaches
both. Environment-variable names are internal (see above) and are never printed.

`anthropic`, `openai`, `openrouter` and `ollama` are built in and are not catalog rows.

## Add a provider as data

1. Verify the base URL, auth header, model-list endpoint and any free tier against the provider's
   own public docs. Never copy a slug, limit or price from memory or a competitor.
2. Add a row to `providers` in the catalog (`name`, `title`, `env_var`, `base_url`, `tier`,
   `source_url`, `checked`; optionally `metadata_url`, `models_endpoint`, `honor_free_markers`,
   `key_required`, `skip_id_pattern`, `retains_data`). The tier is `free_cloud` only where the
   provider says so, otherwise `paid`. A listing that is not reachable at `base_url + models_path`
   gets an explicit `models_endpoint`; a provider whose listing names ids it serves only to its own
   app gets a `skip_id_pattern` (discovery drops those ids and `TargetBuilder` refuses them). Two
   products of one company may share one `env_var`.
3. Add one row per verified fact to `docs/SDK_FACTS.md` and point the `docs/ASSUMPTIONS.md` row at
   it.
4. Regenerate the schema: `uv run python -m headwaters.core.catalog.loader --write
   catalog/catalog.schema.json`.

No `rules.yaml` edit is needed: `headwaters keys set <name>` and the dashboard pick the provider
up from `KeyStore.providers()`.
