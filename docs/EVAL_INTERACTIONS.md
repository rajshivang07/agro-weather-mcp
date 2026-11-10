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

The temperature is 2 m air temperature. Ground frost can occur while it is above
0 °C, so the model may mention that a 2 °C threshold is common for cautious
planning, and it should not change the threshold without being asked.

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
  "dryness_threshold_mm": 10.5,
  "dryness_note": "Likely drying conditions; monitor soil moisture and irrigation needs"
}
```

The note compares the balance with a threshold that scales with the range: 0.35
mm/day with a 5 mm floor, so 10.5 mm for 30 days. A balance at or below minus the
threshold is drying and at or above the threshold is a surplus.

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

## Tools added in 0.2.0

Sections 9 to 26 expand prompts 9 to 26 in [EVALS.md](EVALS.md). Numbers in the
example responses are illustrative and abbreviated. Thresholds are never
assumed: the model passes the values the user states and otherwise relies on the
neutral defaults, which are placeholders and not crop advice.

## 9. Spray window in the next 3 days

Prompt: “In the next 3 days, when can I spray? Wind should be 3 to 15 km/h, Delta T 2 to 8 C, and no rain for 3 hours.”

The limits match the defaults, so the model may send only coordinates and days:

```json
{"lat": 52.52, "lon": 13.41, "days": 3}
```

The server forecasts hourly temperature, humidity, 10 m wind (km/h) and
precipitation, computes Delta T from the Stull (2011) wet-bulb approximation, and
evaluates the next 72 hours from the current local hour. It returns windows, not
the hourly list:

```json
{
  "evaluated_hours": 72,
  "suitable_hours": 12,
  "windows": [
    {"start": "2026-06-02T05:00", "end": "2026-06-02T09:00", "hours": 4},
    {"start": "2026-06-03T20:00", "end": "2026-06-04T04:00", "hours": 8}
  ],
  "longest_window": {"start": "2026-06-03T20:00", "end": "2026-06-04T04:00", "hours": 8},
  "unsuitable_hours_by_limit": {"delta_t_too_high": 12, "rain_expected": 9, "wind_too_high": 14}
}
```

`end` is exclusive. An hour failing several limits is counted under each limit,
so the counts by limit can add up to more than the unsuitable hours. The model
should say the limits are generic rules of thumb, that the product label and
local rules take precedence, and that wind is 10 m model grid data.

## 10. Longest spray window under 25 C

Prompt: “What is the longest suitable spray window in the next 2 days at 38.5, -121.7 if it must stay under 25 C?”

```json
{"lat": 38.5, "lon": -121.7, "days": 2, "max_temp_c": 25.0}
```

The result has the same shape as flow 9. The model reads `longest_window` and
reports its start, exclusive end and hours. If `longest_window` is null, no hour
met all limits, and the model should use `unsuitable_hours_by_limit` to say which
limit was the main obstacle. Hours with Delta T unavailable (outside -20 to 50 C
or 5 to 99 % humidity) appear as `delta_t_unavailable`.

## 11. Hot hours and consecutive hot days

Prompt: “How many hours above 38 C are forecast this week at 30.0, 75.0, and how many hot days in a row?”

```json
{"lat": 30.0, "lon": 75.0, "days": 7, "threshold_c": 38.0}
```

The result mirrors the frost tool, with days instead of nights:

```json
{
  "threshold_c": 38.0,
  "total_hours_above_threshold": 17,
  "longest_streak_days": 3,
  "days": [
    {"date": "2026-05-20", "max_temp_c": 40.2, "hours_above_threshold": 6},
    {"date": "2026-05-21", "max_temp_c": 37.4, "hours_above_threshold": 0}
  ]
}
```

A day counts toward a streak when at least one hour is strictly above the
threshold. Because `days` is more than 3, no `hourly` list is returned.

## 12. Hotter than 33 C tomorrow

Prompt: “Will tomorrow be hotter than 33 C for more than a few hours?”

```json
{"lat": 30.0, "lon": 75.0, "days": 2, "threshold_c": 33.0}
```

With `days` of 3 or fewer the response also includes `hourly`, a list of
`{"time", "temperature_c"}` entries, so the model can say which hours are above
33 C. It should use the second entry of `days` for tomorrow and
`hours_above_threshold` for the count. If the user did not give coordinates, the
model asks or geocodes first.

## 13. Chill hours between 0 and 7.2 C

Prompt: “How many chill hours between 0 and 7.2 C have accumulated from 2025-11-01 to 2026-01-31 at 44.0, -121.3?”

```json
{
  "lat": 44.0,
  "lon": -121.3,
  "start_date": "2025-11-01",
  "end_date": "2026-01-31",
  "method": "below_threshold",
  "threshold_c": 7.2,
  "lower_bound_c": 0.0
}
```

Hourly temperature comes from the archive, and from the forecast if the range
reaches recent days. The result:

```json
{
  "method": "below_threshold",
  "threshold_c": 7.2,
  "lower_bound_c": 0.0,
  "total": 1184.0,
  "monthly": [
    {"month": "2025-11", "value": 301.0},
    {"month": "2025-12", "value": 466.0},
    {"month": "2026-01", "value": 417.0}
  ],
  "hours_evaluated": 2208,
  "hours_missing": 0
}
```

Hours below 0 C are not counted because `lower_bound_c` is 0. Setting it to null
counts every hour at or below the threshold. The tool carries no chilling
requirement, so the model should not say whether the total is enough for any crop.

## 14. Utah chill units and percent of a target

Prompt: “Calculate Utah chill units for that same winter and tell me what percent of 1200 units it reached.”

```json
{
  "lat": 44.0,
  "lon": -121.3,
  "start_date": "2025-11-01",
  "end_date": "2026-01-31",
  "method": "utah",
  "target_hours": 1200
}
```

`threshold_c` and `lower_bound_c` are ignored for the Utah method. The result has
`units.chill` of `Utah chill units`, a `total` that can be lower than a plain hour
count because warm hours subtract units, monthly values that can be negative, and
`percent_of_target`:

```json
{"method": "utah", "total": 936.5, "target": 1200.0, "percent_of_target": 78.0}
```

The model should note that the Utah model performs poorly in warm winters and
that the 1200 target is the user's own figure.

## 15. June versus the 1991 to 2020 normal

Prompt: “Was June 2025 warmer or wetter than the 1991 to 2020 normal at 48.85, 2.35?”

```json
{
  "lat": 48.85,
  "lon": 2.35,
  "start_date": "2025-06-01",
  "end_date": "2025-06-30"
}
```

The baseline years default to 1991 and 2020. Both the baseline and the target are
requested at coordinates rounded to 0.1 degrees (48.9, 2.4), and the baseline is
cached for 24 hours. The result:

```json
{
  "baseline": {
    "start_year": 1991,
    "end_year": 2020,
    "years_used": {"temperature": 30, "precipitation": 30},
    "coordinates_used": {"latitude": 48.9, "longitude": 2.4}
  },
  "temperature": {
    "target": 21.6, "baseline_mean": 18.2, "baseline_std": 1.4,
    "anomaly_c": 3.4, "percentile_rank": 98.3
  },
  "precipitation": {
    "target": 31.0, "baseline_mean": 56.2, "baseline_std": 24.1,
    "anomaly_mm": -25.2, "percent_of_normal": 55.2, "percentile_rank": 15.0
  }
}
```

The model answers both halves: warmer, and drier rather than wetter. It should
mention that the comparison is within one dataset and is not a drought index.

## 16. Spring against a 2001 to 2020 baseline

Prompt: “Compare this past spring (March to May) with the 2001 to 2020 baseline at this location.”

```json
{
  "lat": 48.85,
  "lon": 2.35,
  "start_date": "2026-03-01",
  "end_date": "2026-05-31",
  "baseline_start_year": 2001
}
```

If the user asks for a window that ends too recently, the tool returns an error
instead of partial data, for example `end_date 2026-11-10 is beyond available
archive data, which lags about 5 days; use end_date 2026-11-05 or earlier`. The
model should retry with the suggested date and tell the user the range was
shortened. Errors for a baseline shorter than 10 years or a range over 366 days
are also actionable.

## 17. Workable days with a 5 mm lookback limit

Prompt: “Which days this week are workable for fieldwork? More than 5 mm of rain in the last 3 days makes a day poor.”

```json
{
  "lat": 51.5,
  "lon": -0.1,
  "days": 7,
  "good_lookback_mm": 5.0,
  "marginal_lookback_mm": 5.0,
  "lookback_days": 3
}
```

The good lookback limit cannot exceed the marginal one, so the model sets both to
5 (the default good limit is already 5). Setting only `marginal_lookback_mm` below
`good_lookback_mm` would return `good limits must not exceed the marginal limits`.
The result:

```json
{
  "good_days": 3,
  "longest_good_run": {"start": "2026-06-05", "end": "2026-06-07", "days": 3},
  "days": [
    {
      "date": "2026-06-01",
      "rating": "poor",
      "rain_mm": 8.2,
      "lookback_rain_mm": 1.0,
      "reasons": ["rain 8.2 mm exceeds marginal limit 5 mm"]
    }
  ]
}
```

The rating is a rain-based proxy that is not soil-texture aware.

## 18. Field access with a topsoil moisture limit

Prompt: “Rate the next 5 days for field access, and treat topsoil wetter than 0.35 m3/m3 as one level worse.”

```json
{"lat": 51.5, "lon": -0.1, "days": 5, "soil_moisture_max": 0.35}
```

The server also requests hourly `soil_moisture_0_to_7cm` and averages it per day.
A day above the limit drops one level, and the reasons say so:

```json
{
  "date": "2026-06-03",
  "rating": "marginal",
  "rain_mm": 0.0,
  "lookback_rain_mm": 0.5,
  "topsoil_moisture_m3_m3": 0.38,
  "reasons": ["topsoil moisture 0.380 m3/m3 exceeds 0.35, downgraded from good"]
}
```

A `poor` day stays `poor`. Days with missing rain data are `unknown`.

## 19. Wet hours in the next 3 days

Prompt: “How many hours in the next 3 days have humidity of at least 90 percent between 10 and 25 C?”

The limits match the defaults:

```json
{"lat": 45.0, "lon": 9.2, "days": 3}
```

The result counts rain hours too, because `count_rain_hours` defaults to true:

```json
{
  "criteria": {"min_rh_pct": 90.0, "temp_range_c": [10.0, 25.0], "count_rain_hours": true},
  "total_wet_hours": 41,
  "longest_wet_run_hours": 11,
  "hours_undetermined": 0,
  "days": [
    {"date": "2026-06-01", "wet_hours": 14, "longest_wet_run_hours": 9}
  ],
  "wet_hour_times": ["2026-06-01T00:00", "2026-06-01T01:00"]
}
```

`wet_hour_times` is present because `days` is 3 or fewer. The user asked about
humidity only, so the model should mention that rainy hours are included by
default and offer to rerun with `count_rain_hours` set to false. It should also say
this is a leaf wetness proxy and not a disease forecast.

## 20. Humidity-only wet hours with an 85 percent limit

Prompt: “Count wet hours for the next 2 days using humidity only, not rain, with a 85 percent humidity limit.”

```json
{"lat": 45.0, "lon": 9.2, "days": 2, "min_rh_pct": 85.0, "count_rain_hours": false}
```

With rain counting off, an hour is wet only when the temperature is in range and
humidity is at or above 85 %. `criteria.count_rain_hours` in the result is
`false`, which the model can quote to confirm the request was followed.

## 21. Frost versus heat

Prompt: “Will it get hot enough to scorch the crop this week, above 36 C?”

The question is about hot hours, not cold nights. The model calls
`get_heat_stress`:

```json
{"lat": 30.0, "lon": 75.0, "days": 7, "threshold_c": 36.0}
```

Calling `get_frost_risk` here would return overnight minimums and cold `risk`
labels, which cannot answer the question. The model should also not assume 36 C is
right for the crop; it is the user's value.

## 22. Soil versus fieldwork

Prompt: “Is the field dry enough to get the tractor on it on Thursday?”

This asks which days are workable, so the model calls `get_fieldwork_window`:

```json
{"lat": 51.5, "lon": -0.1, "days": 7}
```

and reads the rating for Thursday. `get_soil_conditions` returns current and
48-hour soil moisture by depth with no judgement and no days beyond two, so it
cannot say whether Thursday is workable. If the user knows their soil, the model
can offer `soil_moisture_max`, since the default rain limits ignore soil texture.

## 23. Water balance versus wetness hours

Prompt: “How many hours will the canopy stay damp from humidity and rain over the next 3 days?”

The question is about hours of humid or rainy weather, so the model calls
`get_wetness_hours` with `days` set to 3. `get_water_balance` reports rain minus
ET0 in millimetres over a date range and would not give hours or runs. The answer
should call the result a leaf wetness proxy.

## 24. GDD versus chill hours

Prompt: “How much winter chilling has my orchard location had since November?”

The model calls `get_chill_hours` with the most recent November 1 as `start_date`,
today as `end_date`, and the default method. `get_growing_degree_days` measures
warm accumulation above a base temperature and would report close to zero in
winter. If the user names a requirement, it goes in `target_hours`; the model
should not invent one for any crop.

## 25. Anomaly versus water balance

Prompt: “Was last summer unusually dry compared with normal years?”

The question compares with normal years, so the model calls `get_climate_anomaly`
for June 1 through August 31 and reads `precipitation.percent_of_normal` and
`percentile_rank`. `get_water_balance` for the same dates would return only total
rain minus ET0 with no baseline. If the user instead asks for rain minus ET0, the
correct tool is `get_water_balance`. The model should state that the anomaly is
not a drought index.

## 26. Spray versus wetness

Prompt: “When is the best time in the next 2 days to apply a spray?”

The model calls `get_spray_window` with `days` set to 2 and reads `windows` and
`longest_window`. `get_wetness_hours` counts humid and rainy hours and returns no
windows or wind and Delta T checks, so it cannot say when to spray. The model
should say that it used the default limits, which are generic rules of thumb, and
ask whether the product label gives different ones.

