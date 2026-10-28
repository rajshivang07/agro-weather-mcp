# MCP evaluation interaction flows

This document expands the prompts in [EVALS.md](EVALS.md). An MCP host first
discovers the tools with `tools/list`, then the model selects a tool and sends a
`tools/call` request. The server validates the arguments, calls Open-Meteo,
performs calculations, and returns compact JSON in an MCP `CallToolResult`.

Every result includes `timezone`, `resolved_date_range`, `units`, and
`data_source: "Open-Meteo (CC BY 4.0)"`.

## 1. Sowing conditions in Tokyo

Prompt: “What are the likely sowing conditions in Tokyo?”

The model makes two dependent calls. First:

```json
{"name": "Tokyo", "country_code": "JP", "count": 3}
```

The `geocode_location` result contains candidates with `name`, `admin_area`,
`country`, `latitude`, `longitude`, `elevation`, and `timezone`. The model uses
the selected coordinates in the second call:

```json
{"lat": 35.6895, "lon": 139.6917}
```

`get_soil_conditions` returns each available depth with current values and
next-48-hour statistics:

```json
{
  "depths": {
    "0_to_7cm": {
      "current": {"temperature_c": 18.2, "moisture_m3_m3": 0.24},
      "next_48h": {
        "temperature_c": {"min": 16.8, "max": 22.1, "average": 19.4},
        "moisture_m3_m3": {"min": 0.22, "max": 0.25, "average": 0.24}
      }
    }
  },
  "sowing_note": "Rough guide only: many crops prefer about 8-10 °C at seed depth. Check crop-specific local guidance."
}
```

The model should explain that the sowing note is not crop-specific advice.

## 2. GDD for Davis in April

Prompt: “Calculate GDD from 2025-04-01 through 2025-04-30 in Davis with a 10 C base and 30 C cap.”

After geocoding if necessary, the model calls:

```json
{
  "lat": 38.54,
  "lon": -121.74,
  "start_date": "2025-04-01",
  "end_date": "2025-04-30",
  "base_temp_c": 10.0,
  "upper_cap_c": 30.0,
  "method": "modified_avg"
}
```

The historical archive supplies daily Tmin and Tmax. The server clamps both to
the base and cap, then calculates modified-average GDD. Because this range is
30 days, the result has `total_gdd` and `daily` values:

```json
{
  "total_gdd": 245.7,
  "daily": [
    {"date": "2025-04-01", "gdd": 6.4},
    {"date": "2025-04-02", "gdd": 8.1}
  ],
  "units": {"gdd": "°C days", "temperature": "°C"}
}
```

The `daily` array above is abbreviated for readability. The real response
contains one entry for every date from 2025-04-01 through 2025-04-30, so it
contains 30 daily entries unless the API returns missing values. Missing days
remain in the array with `gdd: null` and are excluded from `total_gdd`.

Ranges longer than 31 days return weekly summaries instead of daily values.

## 3. GDD over the last year

Prompt: “How many GDD accumulated over the last year at this coordinate?”

The prompt assumes coordinates are already known. Otherwise, the model asks for
a location or geocodes one. If today is `2026-10-28`, it can send:

```json
{
  "lat": 35.68,
  "lon": 139.65,
  "start_date": "2025-10-28",
  "end_date": "2026-10-28",
  "base_temp_c": 10.0,
  "upper_cap_c": 30.0,
  "method": "modified_avg"
}
```

The result uses weekly summaries because the range exceeds 31 days:

```json
{
  "total_gdd": 1280.4,
  "weekly": [
    {"start_date": "2025-10-28", "end_date": "2025-11-03", "gdd": 22.8}
  ]
}
```

If the range reaches today, archive and forecast portions are merged.

## 4. Frost risk for the next week

Prompt: “Will frost threaten this field in the next week below 0 C?”

The model needs field coordinates. It calls:

```json
{"lat": 35.68, "lon": 139.65, "days": 7, "threshold_c": 0.0}
```

The server uses hourly forecast temperature and returns nightly entries:

```json
{
  "threshold_c": 0.0,
  "nights": [
    {
      "date": "YYYY-MM-DD",
      "min_temp_c": 1.2,
      "risk": "none",
      "hours_below_threshold": 0
    }
  ]
}
```

Risk is `none` with zero hours below threshold, `low` when the minimum is above
threshold minus 2 °C, `moderate` when above threshold minus 5 °C, and `high` at
or below threshold minus 5 °C.

## 5. Water stress over the previous 30 days

Prompt: “Was this field water stressed over the previous 30 days?”

The model converts relative dates into ISO dates and sends coordinates:

```json
{
  "lat": 35.68,
  "lon": 139.65,
  "start_date": "2026-09-29",
  "end_date": "2026-10-28"
}
```

The server calculates precipitation minus reference ET0 in millimetres:

```json
{
  "total_precipitation_mm": 24.6,
  "total_et0_mm": 61.2,
  "total_balance_mm": -36.6,
  "weekly": [
    {"start_date": "2026-09-29", "end_date": "2026-10-05", "balance_mm": -8.1}
  ],
  "dryness_note": "Likely drying conditions; monitor soil moisture and irrigation needs"
}
```

This is a weather balance, not measured crop stress. It does not model crop
type, soil storage, irrigation, runoff, or field observations.

## 6. Season crossing today

Prompt: “Compare rainfall and crop reference ET0 for a season that crosses today.”

The model needs coordinates and explicit dates. If either is missing, it should
ask a follow-up question. Example arguments:

```json
{
  "lat": 35.68,
  "lon": 139.65,
  "start_date": "2026-10-01",
  "end_date": "2026-11-15"
}
```

The server sends dates before today to the archive endpoint and dates from today
forward to the forecast endpoint. It merges the data and returns total
precipitation, total ET0, total balance, weekly balances, and `dryness_note`.

## 7. Soil conditions by depth

Prompt: “Show current and 48-hour soil temperature and moisture by depth.”

The model calls:

```json
{"lat": 35.68, "lon": 139.65}
```

The response includes the available depths `0_to_7cm`, `7_to_28cm`,
`28_to_100cm`, and `100_to_255cm`. Each depth has:

```json
{
  "current": {"temperature_c": 18.2, "moisture_m3_m3": 0.24},
  "next_48h": {
    "temperature_c": {"min": 16.8, "max": 22.1, "average": 19.4},
    "moisture_m3_m3": {"min": 0.22, "max": 0.25, "average": 0.24}
  }
}
```

The server returns statistics rather than all hourly samples to keep the MCP
response compact.

## 8. Springfield candidates

Prompt: “Find Springfield in the US and report the candidate coordinates.”

The model calls:

```json
{"name": "Springfield", "country_code": "US", "count": 3}
```

The result contains up to three candidates:

```json
{
  "units": {"latitude_longitude": "degrees", "elevation": "m"},
  "candidates": [
    {
      "name": "Springfield",
      "admin_area": "Illinois",
      "country": "United States",
      "latitude": 39.7989,
      "longitude": -89.644,
      "elevation": 171,
      "timezone": "America/Chicago"
    }
  ]
}
```

The model should report all candidates instead of assuming the first match is
the intended place.
