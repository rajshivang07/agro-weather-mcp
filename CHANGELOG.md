# Changelog

## Unreleased

Guardrails:

- Non-finite (NaN or infinite) numeric arguments are rejected in every tool with numeric parameters. Before, a NaN threshold made `get_frost_risk` report no risk for a freezing night.
- An empty upstream response is now an error in the forecast tools and `get_chill_hours`, instead of a result with zero counts.
- `get_heat_stress` reports `hours_missing`.
- `get_spray_window`, `get_fieldwork_window` and `get_wetness_hours` return a `note` with their main caveat.
- `geocode_location` shortens and flattens place text returned by the API.
- New `tests/test_guardrails.py`, including a check that no tool names a specific crop.
- `docs/EVALS.md` and `docs/EVAL_INTERACTIONS.md` gain out-of-scope and guardrail prompts where the correct behaviour is to call no tool, and a README "Scope and guardrails" section.

## 0.2.0

New tools (all thresholds are user-supplied parameters with neutral defaults, and no crop knowledge is bundled):

- `get_spray_window`: suitable spray windows from wind, Delta T (Stull 2011 wet-bulb), temperature and rain-free hours.
- `get_heat_stress`: forecast hours and consecutive days above a temperature threshold.
- `get_chill_hours`: hours within a temperature band, or Utah chill units, with monthly totals and an optional target.
- `get_climate_anomaly`: temperature and rainfall anomaly, percent of normal and percentile rank against a baseline period, with a 24 hour in-memory baseline cache.
- `get_fieldwork_window`: rain-based daily field access rating with an optional topsoil moisture downgrade.
- `get_wetness_hours`: leaf wetness proxy from humidity, rain and a temperature range.

Changes:

- `get_water_balance`: the `dryness_note` thresholds now scale with range length (0.35 mm/day with a 5 mm floor) and the response reports `dryness_threshold_mm`.
- `get_frost_risk`: documented that it uses 2 m air temperature and that a 2 C threshold is common for cautious planning. The default is unchanged.
- Tool descriptions of the original tools now include "Use when" and "Not for" routing lines.
- Added a shared hourly helper that merges archive and forecast data (with `past_days`), de-duplicates overlapping hours and marks gaps as null.
- The HTTP client accepts a per-request cache TTL and drops expired cache entries.
- README: new tool documentation, a "No crop knowledge is bundled" section and a tool selection table. `docs/EVALS.md` has new prompts and confusable-pair checks.

## 0.1.0

- Initial stdio MCP server with five agriculture-focused Open-Meteo tools.
- Added retries, ten-minute in-memory caching, pure calculations, tests, and documentation.
