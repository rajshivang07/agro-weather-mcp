"""FastMCP stdio server exposing agriculture weather tools."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from .calculations import (
    anomaly_stats,
    baseline_windows,
    chill_summary,
    daily_gdd,
    daily_means,
    dryness_note,
    dryness_threshold_mm,
    fieldwork_ratings,
    frost_nights,
    heat_days,
    heat_summary,
    longest_good_run,
    longest_run,
    merge_hourly,
    parse_iso_date,
    spray_hours,
    spray_summary,
    summarize_gdd,
    water_balance,
    weekly_balance,
    wet_flags,
    wetness_days,
    window_values,
)
from .client import OpenMeteoClient

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
SOURCE = "Open-Meteo (CC BY 4.0)"
MAX_FORECAST_DAYS = 16
# ERA5 archive data is finalized after about five days, so newer dates come
# from the forecast endpoint. past_days is documented as 0 to 92.
ARCHIVE_LAG_DAYS = 5
ARCHIVE_FIRST_YEAR = 1940
BASELINE_TTL_SECONDS = 24 * 60 * 60
MIN_BASELINE_YEARS = 10
MAX_PAST_DAYS = 92

mcp = FastMCP("agro-weather-mcp")
_client: OpenMeteoClient | None = None
_client_loop: object | None = None


def client() -> OpenMeteoClient:
    global _client, _client_loop

    current_loop = asyncio.get_running_loop()
    # Tests and some MCP hosts can create more than one event loop. An
    # httpx.AsyncClient must not be reused across closed event loops.
    if _client is None or _client_loop is not current_loop:
        _client = OpenMeteoClient()
        _client_loop = current_loop
    return _client


def _location(lat: float, lon: float) -> None:
    # Fail before making a network request so the model can correct the input.
    if not -90 <= lat <= 90:
        raise ValueError("lat must be between -90 and 90")
    if not -180 <= lon <= 180:
        raise ValueError("lon must be between -180 and 180")


def _date_range(start_date: str, end_date: str) -> tuple[date, date]:
    start, end = parse_iso_date(start_date, "start_date"), parse_iso_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if (end - start).days + 1 > 366:
        raise ValueError("date range cannot exceed 366 days")
    today = date.today()
    # Archive data handles the past. Keep future requests within the forecast
    # horizon supported by this server.
    if end >= today and (end - today).days >= MAX_FORECAST_DAYS:
        raise ValueError("forecast requests can extend at most 16 days from today")
    return start, end


def _days(days: int, maximum: int) -> None:
    if not 1 <= days <= maximum:
        raise ValueError(f"days must be between 1 and {maximum}")


def _utcnow() -> datetime:
    # Separate function so tests can pin the clock.
    return datetime.now(UTC)


def _local_hour_label(data: dict[str, Any]) -> str:
    """Current local hour as an Open-Meteo style label, from the response UTC offset."""
    offset = timedelta(seconds=int(data.get("utc_offset_seconds") or 0))
    return (_utcnow() + offset).strftime("%Y-%m-%dT%H:00")


def _meta(lat: float, lon: float, start: str, end: str, timezone: str = "auto") -> dict[str, Any]:
    return {
        "latitude": lat,
        "longitude": lon,
        "resolved_date_range": {"start": start, "end": end},
        "timezone": timezone,
        "data_source": SOURCE,
    }


async def _daily_parts(
    lat: float, lon: float, start: date, end: date, variables: str
) -> tuple[list[dict[str, Any]], str]:
    today = date.today()
    parts: list[dict[str, Any]] = []
    timezone = "auto"
    if start < today:
        # Historical dates stop yesterday so today is not requested twice.
        past_end = min(end, today - timedelta(days=1))
        parts.append(
            await client().get(
                ARCHIVE_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": str(start),
                    "end_date": str(past_end),
                    "daily": variables,
                    "timezone": "auto",
                },
            )
        )
    if end >= today:
        # Forecast dates begin today, including a range that crosses the split.
        future_start = max(start, today)
        parts.append(
            await client().get(
                FORECAST_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": str(future_start),
                    "end_date": str(end),
                    "daily": variables,
                    "timezone": "auto",
                },
            )
        )
    for part in parts:
        timezone = part.get("timezone", timezone)
    return parts, timezone


def _merge_daily(parts: list[dict[str, Any]], variables: list[str]) -> dict[str, list[Any]]:
    # Both API responses use aligned daily arrays. Extending in request order
    # preserves chronological order across the archive and forecast split.
    merged = {"time": []}
    for variable in variables:
        merged[variable] = []
    for part in parts:
        daily = part.get("daily", {})
        for key in merged:
            merged[key].extend(daily.get(key, []))
    return merged


async def _hourly_series(
    lat: float,
    lon: float,
    start: date,
    end: date,
    variables: list[str],
    today: date | None = None,
) -> tuple[dict[str, list[Any]], str]:
    """Fetch one ordered hourly series for a range that may span past and future.

    Dates up to ``ARCHIVE_LAG_DAYS`` before today come from the archive endpoint.
    Newer dates come from the forecast endpoint using ``past_days`` and
    ``forecast_days``. Overlapping hours are de-duplicated, the result is trimmed
    to the requested dates, and missing hours or values are ``None``.
    """
    today = today or date.today()
    archive_last = today - timedelta(days=ARCHIVE_LAG_DAYS)
    hourly = ",".join(variables)
    parts: list[dict[str, Any]] = []
    timezone = "auto"
    if start <= archive_last:
        parts.append(
            await client().get(
                ARCHIVE_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": str(start),
                    "end_date": str(min(end, archive_last)),
                    "hourly": hourly,
                    "timezone": "auto",
                },
            )
        )
    if end > archive_last:
        recent_start = max(start, archive_last + timedelta(days=1))
        past_days = max(0, (today - recent_start).days)
        if past_days > MAX_PAST_DAYS:
            raise ValueError(f"recent data can reach back at most {MAX_PAST_DAYS} days")
        parts.append(
            await client().get(
                FORECAST_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": hourly,
                    "past_days": past_days,
                    "forecast_days": max(1, (end - today).days + 1),
                    "timezone": "auto",
                },
            )
        )
    for part in parts:
        timezone = part.get("timezone", timezone)
        break
    series = merge_hourly([part.get("hourly", {}) for part in parts], variables, start, end)
    return series, timezone


@mcp.tool()
async def geocode_location(
    name: str, country_code: str | None = None, count: int = 3
) -> dict[str, Any]:
    """Find candidate places by name.

    Returns up to ``count`` candidates, where count is 1 through 10. Latitude and
    longitude are degrees, elevation is metres, and timezone is an IANA name.
    This tool has no date input and resolves the range to the current date. Example:
    ``geocode_location("Davis", "US", 3)``.
    """
    if not name.strip():
        raise ValueError("name must not be empty")
    if not 1 <= count <= 10:
        raise ValueError("count must be between 1 and 10")
    params: dict[str, Any] = {"name": name, "count": count, "language": "en", "format": "json"}
    if country_code:
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("country_code must be a two-letter ISO 3166-1 alpha-2 code")
        params["countryCode"] = country_code.upper()
    data = await client().get(GEOCODING_URL, params)
    places = [
        {
            "name": item.get("name"),
            "admin_area": item.get("admin1"),
            "country": item.get("country"),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "elevation": item.get("elevation"),
            "timezone": item.get("timezone"),
        }
        for item in data.get("results", [])
    ]
    today = str(date.today())
    return {
        **_meta(0, 0, today, today),
        "units": {"latitude_longitude": "degrees", "elevation": "m"},
        "candidates": places,
    }


@mcp.tool()
async def get_growing_degree_days(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    base_temp_c: float = 10.0,
    upper_cap_c: float = 30.0,
    method: str = "modified_avg",
) -> dict[str, Any]:
    """Calculate accumulated growing degree days from daily temperatures.

    Use when: the question is how much warm-season heat has accumulated.
    Not for: winter chilling (use ``get_chill_hours``) or hot-hour exposure
    (use ``get_heat_stress``).

    Dates use ISO ``YYYY-MM-DD`` and the inclusive range is at most 366 days.
    Temperatures and base/cap are degrees Celsius. The modified-average method
    clamps Tmin and Tmax to the base and upper cap before averaging. Daily values
    are returned for ranges up to 31 days, otherwise weekly summaries. Past dates
    use ERA5 archive data and future dates use the forecast, merged when needed.
    Example: ``get_growing_degree_days(38.54, -121.74, "2025-04-01", "2025-04-30")``.
    """
    _location(lat, lon)
    start, end = _date_range(start_date, end_date)
    if base_temp_c >= upper_cap_c:
        raise ValueError("base_temp_c must be lower than upper_cap_c")
    if method != "modified_avg":
        raise ValueError("method must be 'modified_avg'")
    parts, timezone = await _daily_parts(
        lat, lon, start, end, "temperature_2m_max,temperature_2m_min"
    )
    data = _merge_daily(parts, ["temperature_2m_max", "temperature_2m_min"])
    days = daily_gdd(
        data["time"],
        data["temperature_2m_max"],
        data["temperature_2m_min"],
        base_temp_c,
        upper_cap_c,
    )
    return {
        **_meta(lat, lon, str(start), str(end), timezone),
        "units": {"gdd": "°C days", "temperature": "°C"},
        "method": method,
        **summarize_gdd(days),
    }


@mcp.tool()
async def get_frost_risk(
    lat: float, lon: float, days: int = 7, threshold_c: float = 0.0
) -> dict[str, Any]:
    """Assess overnight frost risk for the next 1 to 16 forecast days.

    Use when: the question is about cold nights or frost exposure.
    Not for: hot-day exposure (use ``get_heat_stress``) or accumulated winter
    chill (use ``get_chill_hours``).

    ``threshold_c`` is degrees Celsius and days is a forecast horizon. A night is
    low risk when its minimum is above threshold minus 2 C, moderate when it is
    above threshold minus 5 C, and high at or below threshold minus 5 C. Any
    hours below threshold make risk at least low. The result includes per-night
    minimum temperature and hours below threshold, in the forecast timezone.
    Temperatures are 2 m air temperature from model grid data. Ground and leaf
    frost can occur while 2 m air temperature is still above 0 C, so a threshold
    of 2 C is common for cautious planning. The default of 0 C is unchanged and
    is the user's choice to adjust. Example: ``get_frost_risk(45.52, -122.68, 7, 0.0)``.
    """
    _location(lat, lon)
    if not 1 <= days <= MAX_FORECAST_DAYS:
        raise ValueError("days must be between 1 and 16")
    start, end = date.today(), date.today() + timedelta(days=days - 1)
    data = await client().get(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "forecast_days": days,
            "timezone": "auto",
        },
    )
    hourly = data.get("hourly", {})
    nights = frost_nights(hourly.get("time", []), hourly.get("temperature_2m", []), threshold_c)
    return {
        **_meta(lat, lon, str(start), str(end), data.get("timezone", "auto")),
        "units": {"temperature": "°C", "hours": "hours"},
        "threshold_c": threshold_c,
        "nights": nights,
    }


@mcp.tool()
async def get_water_balance(
    lat: float, lon: float, start_date: str, end_date: str
) -> dict[str, Any]:
    """Calculate precipitation minus reference evapotranspiration.

    Use when: the question is whether a period was wetter or drier than
    evaporative demand.
    Not for: comparison with normal years (use ``get_climate_anomaly``), humid
    hours (use ``get_wetness_hours``), or field access (use ``get_fieldwork_window``).

    Dates use ISO ``YYYY-MM-DD``, are inclusive, and span at most 366 days.
    Precipitation, ET0, and the resulting balance are millimetres. ET0 is
    ``et0_fao_evapotranspiration`` for a well-watered reference grass field.
    Results include total precipitation, total ET0, total balance, weekly
    breakdowns, and a plain-language dryness note. The note compares the total
    balance with a threshold that scales with range length: 0.35 mm per day
    (about 10.5 mm over 30 days) with a 5 mm floor. A balance at or above the
    threshold is a likely surplus, at or below minus the threshold is likely
    drying, and anything between is roughly balanced. Past and future ranges are
    merged from archive and forecast data. Example: ``get_water_balance(52.52,
    13.41, "2025-06-01", "2025-06-30")``.
    """
    _location(lat, lon)
    start, end = _date_range(start_date, end_date)
    parts, timezone = await _daily_parts(
        lat, lon, start, end, "precipitation_sum,et0_fao_evapotranspiration"
    )
    data = _merge_daily(parts, ["precipitation_sum", "et0_fao_evapotranspiration"])
    balances = water_balance(
        data["time"], data["precipitation_sum"], data["et0_fao_evapotranspiration"]
    )
    precipitation = sum(float(x) for x in data["precipitation_sum"] if isinstance(x, (int, float)))
    et0 = sum(float(x) for x in data["et0_fao_evapotranspiration"] if isinstance(x, (int, float)))
    total = round(precipitation - et0, 2)
    n_days = (end - start).days + 1
    return {
        **_meta(lat, lon, str(start), str(end), timezone),
        "units": {"precipitation": "mm", "et0": "mm", "balance": "mm"},
        "total_precipitation_mm": round(precipitation, 2),
        "total_et0_mm": round(et0, 2),
        "total_balance_mm": total,
        "weekly": weekly_balance(balances),
        "dryness_threshold_mm": dryness_threshold_mm(n_days),
        "dryness_note": dryness_note(total, n_days),
    }


@mcp.tool()
async def get_soil_conditions(lat: float, lon: float) -> dict[str, Any]:
    """Summarize current and next-48-hour soil conditions.

    Use when: the question is about soil temperature or moisture by depth right now.
    Not for: deciding which days are workable (use ``get_fieldwork_window``).

    Soil temperature is degrees Celsius and soil moisture is volumetric water
    content in m³/m³ at the available 0-7, 7-28, 28-100, and 100-255 cm depths.
    The forecast covers the current time through approximately 48 hours in the
    resolved timezone. The sowing note is a rough guide only: many crops prefer
    about 8-10 C at seed depth, but crop, soil, and local practice matter.
    Example: ``get_soil_conditions(42.36, -71.06)``.
    """
    _location(lat, lon)
    data = await client().get(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": ",".join(
                [
                    f"soil_temperature_{depth}"
                    for depth in ("0_to_7cm", "7_to_28cm", "28_to_100cm", "100_to_255cm")
                ]
                + [
                    f"soil_moisture_{depth}"
                    for depth in ("0_to_7cm", "7_to_28cm", "28_to_100cm", "100_to_255cm")
                ]
            ),
            "forecast_days": 3,
            "timezone": "auto",
        },
    )
    hourly = data.get("hourly", {})
    # The API request asks for three forecast days, then we retain only the
    # current point and the next 48 hourly points for a compact response.
    times = hourly.get("time", [])[:49]
    depths = ("0_to_7cm", "7_to_28cm", "28_to_100cm", "100_to_255cm")

    def stats(values: list[Any]) -> dict[str, float | None]:
        # Ignore null hourly values while preserving an all-missing result.
        numbers = [float(value) for value in values if isinstance(value, (int, float))]
        if not numbers:
            return {"min": None, "max": None, "average": None}
        return {
            "min": round(min(numbers), 2),
            "max": round(max(numbers), 2),
            "average": round(sum(numbers) / len(numbers), 2),
        }

    conditions = {}
    for depth in depths:
        temperatures = hourly.get(f"soil_temperature_{depth}", [])[:49]
        moisture = hourly.get(f"soil_moisture_{depth}", [])[:49]
        conditions[depth] = {
            "current": {
                "temperature_c": temperatures[0] if temperatures else None,
                "moisture_m3_m3": moisture[0] if moisture else None,
            },
            "next_48h": {
                "temperature_c": stats(temperatures),
                "moisture_m3_m3": stats(moisture),
            },
        }
    return {
        **_meta(
            lat,
            lon,
            times[0][:10] if times else str(date.today()),
            times[-1][:10] if times else str(date.today()),
            data.get("timezone", "auto"),
        ),
        "units": {"soil_temperature": "°C", "soil_moisture": "m³/m³"},
        "depths": conditions,
        "sowing_note": (
            "Rough guide only: many crops prefer about 8-10 °C at seed depth. "
            "Check crop-specific local guidance."
        ),
    }


@mcp.tool()
async def get_spray_window(
    lat: float,
    lon: float,
    days: int = 3,
    min_wind_kmh: float = 3.0,
    max_wind_kmh: float = 15.0,
    min_delta_t_c: float = 2.0,
    max_delta_t_c: float = 8.0,
    rain_free_hours: int = 3,
    max_temp_c: float = 30.0,
) -> dict[str, Any]:
    """Find forecast hours that fit user-set spray conditions and group them into windows.

    Use when: the question is when wind, Delta T, temperature and rain-free time
    line up for a spray or similar application in the next 1 to 7 days.
    Not for: frost (use ``get_frost_risk``), heat (use ``get_heat_stress``), or
    leaf wetness and disease conditions (use ``get_wetness_hours``).

    Delta T is air temperature minus wet-bulb temperature, with wet-bulb from the
    Stull (2011) approximation. It is only defined for -20 to 50 C and 5 to 99 %
    relative humidity, and hours outside that range count as unsuitable under
    ``delta_t_unavailable``. An hour is suitable when wind (km/h, 10 m model grid
    data), Delta T (C) and temperature (C, upper limit only) are within the limits
    and no precipitation above 0.1 mm is forecast in the next ``rain_free_hours``
    hours. Open-Meteo reports precipitation for the preceding hour, so the check
    reads the hourly labels after each hour. Only hours from the current local hour
    onward are evaluated. Returns contiguous windows (``end`` is exclusive), the
    longest window, and unsuitable hours counted per limiting factor (an hour that
    fails several limits is counted under each). The full hourly list is not returned.

    Every threshold is a generic rule of thumb supplied by the caller, and no
    product, crop or species knowledge is built in. The product label and local
    rules always take precedence. Wind is 10 m grid data, not wind at the nozzle
    or canopy. Example: ``get_spray_window(52.52, 13.41, 3)``.
    """
    _location(lat, lon)
    _days(days, 7)
    if min_wind_kmh < 0 or min_wind_kmh >= max_wind_kmh:
        raise ValueError("min_wind_kmh must be at least 0 and lower than max_wind_kmh")
    if min_delta_t_c >= max_delta_t_c:
        raise ValueError("min_delta_t_c must be lower than max_delta_t_c")
    if not 0 <= rain_free_hours <= 24:
        raise ValueError("rain_free_hours must be between 0 and 24")
    data = await client().get(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
            "wind_speed_unit": "kmh",
            # One extra day provides the rain look-ahead for the last hours.
            "forecast_days": min(days + 1, MAX_FORECAST_DAYS),
            "timezone": "auto",
        },
    )
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    first = next((i for i, t in enumerate(times) if t >= _local_hour_label(data)), len(times))
    hours = spray_hours(
        times,
        hourly.get("temperature_2m", []),
        hourly.get("relative_humidity_2m", []),
        hourly.get("wind_speed_10m", []),
        hourly.get("precipitation", []),
        min_wind_kmh=min_wind_kmh,
        max_wind_kmh=max_wind_kmh,
        min_delta_t_c=min_delta_t_c,
        max_delta_t_c=max_delta_t_c,
        rain_free_hours=rain_free_hours,
        max_temp_c=max_temp_c,
        first_index=first,
        count=days * 24,
    )
    start = hours[0]["time"][:10] if hours else str(date.today())
    end = hours[-1]["time"][:10] if hours else start
    return {
        **_meta(lat, lon, start, end, data.get("timezone", "auto")),
        "units": {"wind": "km/h", "delta_t": "°C", "temperature": "°C", "precipitation": "mm"},
        "limits": {
            "wind_kmh": [min_wind_kmh, max_wind_kmh],
            "delta_t_c": [min_delta_t_c, max_delta_t_c],
            "max_temp_c": max_temp_c,
            "rain_free_hours": rain_free_hours,
            "rain_threshold_mm": 0.1,
        },
        **spray_summary(hours),
    }


@mcp.tool()
async def get_heat_stress(
    lat: float, lon: float, days: int = 7, threshold_c: float = 35.0
) -> dict[str, Any]:
    """Count forecast hours above a heat threshold for the next 1 to 16 days.

    Use when: the question is about hot-day exposure, such as hours above a
    temperature limit or consecutive hot days.
    Not for: cold or frost exposure (use ``get_frost_risk``), or spray timing
    (use ``get_spray_window``).

    Uses hourly 2 m air temperature (C) in the forecast timezone. Per day it
    returns the maximum temperature and hours strictly above ``threshold_c``. It
    also returns total hours above the threshold and the longest streak of
    consecutive days with at least one such hour (a day with no data ends a
    streak). Hourly temperatures are included only when ``days`` is 3 or fewer.

    The right threshold depends on the crop, growth stage or species and must be
    chosen by the user. The default of 35 C is a generic placeholder, not
    agronomic guidance. Example: ``get_heat_stress(30.0, 75.0, 7, 35.0)``.
    """
    _location(lat, lon)
    _days(days, MAX_FORECAST_DAYS)
    start, end = date.today(), date.today() + timedelta(days=days - 1)
    data = await client().get(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "forecast_days": days,
            "timezone": "auto",
        },
    )
    hourly = data.get("hourly", {})
    times, temperatures = hourly.get("time", []), hourly.get("temperature_2m", [])
    per_day = heat_days(times, temperatures, threshold_c)
    result = {
        **_meta(lat, lon, str(start), str(end), data.get("timezone", "auto")),
        "units": {"temperature": "°C", "hours": "hours"},
        "threshold_c": threshold_c,
        **heat_summary(per_day),
        "days": per_day,
    }
    if days <= 3:
        result["hourly"] = [
            {"time": t, "temperature_c": v} for t, v in zip(times, temperatures, strict=False)
        ]
    return result


@mcp.tool()
async def get_chill_hours(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    method: str = "below_threshold",
    threshold_c: float = 7.2,
    lower_bound_c: float | None = 0.0,
    target_hours: float | None = None,
) -> dict[str, Any]:
    """Accumulate winter chill from hourly temperature over a date range.

    Use when: the question is how much chilling (cold hours or chill units) has
    accumulated, for example to compare with a requirement the user already knows.
    Not for: warm-season heat accumulation (use ``get_growing_degree_days``),
    frost nights (use ``get_frost_risk``), or hot hours (use ``get_heat_stress``).

    Dates use ISO ``YYYY-MM-DD``, are inclusive, and span at most 366 days. Hourly
    2 m air temperature (C) comes from the archive and the forecast and is merged
    when the range reaches recent days or the future. Two methods are available.
    ``below_threshold`` counts hours with temperature from ``lower_bound_c`` up to
    ``threshold_c``, both inclusive; set ``lower_bound_c`` to null to count every
    hour at or below the threshold. ``utah`` returns Utah chill units using the
    weights of Richardson, Seeley and Walker (1974, HortScience 9(4):331-332),
    per hour: at or below 1.4 C: 0; 1.5-2.4 C: 0.5; 2.5-9.1 C: 1.0; 9.2-12.4 C:
    0.5; 12.5-15.9 C: 0; 16.0-18.0 C: -0.5; above 18.0 C: -1.0 (``threshold_c``
    and ``lower_bound_c`` are ignored). Totals can be negative. Returns the
    total, a monthly breakdown, hours evaluated and missing, and the percent of
    ``target_hours`` reached when it is provided.

    Chilling requirements vary by species and cultivar and this tool carries
    none: ``target_hours`` and the thresholds are the caller's choice. The Utah
    model performs poorly in warm winters. Example: ``get_chill_hours(44.0, -121.3,
    "2025-11-01", "2026-02-28", "below_threshold", 7.2, 0.0, 1000)``.
    """
    _location(lat, lon)
    start, end = _date_range(start_date, end_date)
    if method not in ("below_threshold", "utah"):
        raise ValueError("method must be 'below_threshold' or 'utah'")
    if method == "below_threshold" and lower_bound_c is not None and lower_bound_c > threshold_c:
        raise ValueError("lower_bound_c must not be above threshold_c")
    if target_hours is not None and target_hours <= 0:
        raise ValueError("target_hours must be greater than 0")
    series, timezone = await _hourly_series(lat, lon, start, end, ["temperature_2m"])
    summary = chill_summary(
        series["time"],
        series["temperature_2m"],
        method,
        threshold_c,
        lower_bound_c,
        target_hours,
    )
    result = {
        **_meta(lat, lon, str(start), str(end), timezone),
        "units": {
            "temperature": "°C",
            "chill": "hours" if method == "below_threshold" else "Utah chill units",
        },
        "method": method,
    }
    if method == "below_threshold":
        result["threshold_c"] = threshold_c
        result["lower_bound_c"] = lower_bound_c
    return {**result, **summary}


def _archive_end_limit(today: date) -> date:
    return today - timedelta(days=ARCHIVE_LAG_DAYS)


@mcp.tool()
async def get_climate_anomaly(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    baseline_start_year: int = 1991,
    baseline_end_year: int = 2020,
) -> dict[str, Any]:
    """Compare a period's mean temperature and rainfall with the same calendar window in past years.

    Use when: the question is whether a past period was warmer, cooler, wetter or
    drier than usual for that time of year.
    Not for: a rain-minus-ET0 budget (use ``get_water_balance``), forecasts, or
    drought monitoring.

    Dates use ISO ``YYYY-MM-DD``, are inclusive, span at most 366 days, and must
    end at least 5 days before today (archive data lags); otherwise the error
    names the latest usable end date. For every baseline year the same window is
    taken with the same start month and day and the same number of days (Feb 29
    starts map to Feb 28 in non-leap years; windows spanning New Year are labelled
    by the year they start in and baseline windows running past the final baseline
    year are dropped). The baseline must cover at least 10 years. For temperature
    and precipitation it returns the target value (mean of daily mean temperature
    in C, precipitation total in mm), baseline mean, sample standard deviation,
    the anomaly, the mid-rank percentile of the target among the baseline years
    (0 to 100), and for rain also percent of normal. Years with any missing day
    are skipped.

    Both the baseline and the target are requested at the coordinates rounded to
    0.1 degrees, so they come from the same ERA5 grid area, and the baseline is
    cached in memory for 24 hours per rounded location and year range. Because
    both sides come from one dataset, ERA5 bias largely cancels. This is a
    comparison with a chosen baseline, not a drought index. Example:
    ``get_climate_anomaly(48.85, 2.35, "2025-06-01", "2025-08-31")``.
    """
    _location(lat, lon)
    start, end = parse_iso_date(start_date, "start_date"), parse_iso_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if (end - start).days + 1 > 366:
        raise ValueError("date range cannot exceed 366 days")
    today = date.today()
    limit = _archive_end_limit(today)
    if end > limit:
        raise ValueError(
            f"end_date {end} is beyond available archive data, which lags about "
            f"{ARCHIVE_LAG_DAYS} days; use end_date {limit} or earlier"
        )
    if start.year < ARCHIVE_FIRST_YEAR:
        raise ValueError(f"start_date must be in {ARCHIVE_FIRST_YEAR} or later")
    if baseline_start_year < ARCHIVE_FIRST_YEAR:
        raise ValueError(f"baseline_start_year must be {ARCHIVE_FIRST_YEAR} or later")
    if baseline_end_year > today.year - 1:
        raise ValueError(f"baseline_end_year must be {today.year - 1} or earlier")
    if baseline_end_year - baseline_start_year + 1 < MIN_BASELINE_YEARS:
        raise ValueError(f"baseline must span at least {MIN_BASELINE_YEARS} years")
    # "+ 0.0" turns -0.0 into 0.0 so nearby locations share one cache key.
    rlat, rlon = round(lat, 1) + 0.0, round(lon, 1) + 0.0
    variables = "temperature_2m_mean,precipitation_sum"
    base_params = {
        "latitude": rlat,
        "longitude": rlon,
        "daily": variables,
        "timezone": "auto",
    }
    baseline = await client().get(
        ARCHIVE_URL,
        {
            **base_params,
            "start_date": f"{baseline_start_year}-01-01",
            "end_date": f"{baseline_end_year}-12-31",
        },
        ttl_seconds=BASELINE_TTL_SECONDS,
    )
    target = await client().get(
        ARCHIVE_URL, {**base_params, "start_date": str(start), "end_date": str(end)}
    )
    target_daily, base_daily = target.get("daily", {}), baseline.get("daily", {})
    target_values = window_values(
        target_daily.get("time", []),
        target_daily.get("temperature_2m_mean", []),
        target_daily.get("precipitation_sum", []),
        start,
        end,
    )
    if target_values["mean_temp_c"] is None or target_values["precip_mm"] is None:
        raise ValueError(
            "archive data is incomplete for the requested range; try an earlier end_date "
            f"(on or before {limit})"
        )
    temps: list[float] = []
    rains: list[float] = []
    for _year, window_start, window_end in baseline_windows(
        start, end, baseline_start_year, baseline_end_year
    ):
        values = window_values(
            base_daily.get("time", []),
            base_daily.get("temperature_2m_mean", []),
            base_daily.get("precipitation_sum", []),
            window_start,
            window_end,
        )
        if values["mean_temp_c"] is not None:
            temps.append(values["mean_temp_c"])
        if values["precip_mm"] is not None:
            rains.append(values["precip_mm"])
    if len(temps) < MIN_BASELINE_YEARS or len(rains) < MIN_BASELINE_YEARS:
        raise ValueError(
            f"fewer than {MIN_BASELINE_YEARS} complete baseline years are available for this "
            "window; widen the baseline years or shorten the range"
        )
    return {
        **_meta(lat, lon, str(start), str(end), target.get("timezone", "auto")),
        "units": {"temperature": "°C", "precipitation": "mm", "percentile_rank": "%"},
        "baseline": {
            "start_year": baseline_start_year,
            "end_year": baseline_end_year,
            "years_used": {"temperature": len(temps), "precipitation": len(rains)},
            "coordinates_used": {"latitude": rlat, "longitude": rlon},
            "cache_hours": BASELINE_TTL_SECONDS // 3600,
        },
        "temperature": {
            "metric": "mean of daily mean temperature (°C)",
            **anomaly_stats(target_values["mean_temp_c"], temps),
        },
        "precipitation": {
            "metric": "total precipitation (mm)",
            **anomaly_stats(target_values["precip_mm"], rains, precipitation=True),
        },
    }


@mcp.tool()
async def get_fieldwork_window(
    lat: float,
    lon: float,
    days: int = 7,
    good_day_mm: float = 1.0,
    good_lookback_mm: float = 5.0,
    marginal_day_mm: float = 5.0,
    marginal_lookback_mm: float = 15.0,
    lookback_days: int = 3,
    soil_moisture_max: float | None = None,
) -> dict[str, Any]:
    """Rate each of the next 1 to 14 days for field access from recent and forecast rain.

    Use when: the question is which days are workable for tillage, traffic or
    harvest given recent and forecast rain.
    Not for: reading current soil temperature or moisture by depth (use
    ``get_soil_conditions``) or a seasonal rain-minus-ET0 budget (use
    ``get_water_balance``).

    Each day gets ``good``, ``marginal`` or ``poor`` from daily precipitation (mm)
    on that day and the total over the ``lookback_days`` days before it, which
    includes past days. Good needs the day at or below ``good_day_mm`` and the
    lookback at or below ``good_lookback_mm``. Marginal needs both within the
    marginal limits. Anything else is poor. A day with missing rain data is
    ``unknown``. If ``soil_moisture_max`` (m3/m3) is set, a day whose mean forecast
    topsoil (0-7 cm) moisture is above it drops one level. Returns each day's
    rating with reasons and the longest run of consecutive good days.

    This is a rain-based proxy and is not soil-texture aware, because Open-Meteo
    has no soil texture data. A sandy field and a clay field need very different
    limits. Users who know their soil should set the limits and
    ``soil_moisture_max``. No limit here is crop or machinery knowledge.
    Example: ``get_fieldwork_window(51.5, -0.1, 7, soil_moisture_max=0.35)``.
    """
    _location(lat, lon)
    _days(days, 14)
    if not 1 <= lookback_days <= 14:
        raise ValueError("lookback_days must be between 1 and 14")
    if min(good_day_mm, good_lookback_mm, marginal_day_mm, marginal_lookback_mm) < 0:
        raise ValueError("rain limits must not be negative")
    if good_day_mm > marginal_day_mm or good_lookback_mm > marginal_lookback_mm:
        raise ValueError("good limits must not exceed the marginal limits")
    if soil_moisture_max is not None and not 0 < soil_moisture_max <= 1:
        raise ValueError("soil_moisture_max must be between 0 and 1 (m3/m3)")
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "past_days": lookback_days,
        "forecast_days": days,
        "timezone": "auto",
    }
    if soil_moisture_max is not None:
        params["hourly"] = "soil_moisture_0_to_7cm"
    data = await client().get(FORECAST_URL, params)
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    moisture = None
    if soil_moisture_max is not None:
        hourly = data.get("hourly", {})
        moisture = daily_means(hourly.get("time", []), hourly.get("soil_moisture_0_to_7cm", []))
    rated = fieldwork_ratings(
        dates,
        daily.get("precipitation_sum", []),
        lookback_days,
        first_index=max(0, len(dates) - days),
        good_day_mm=good_day_mm,
        good_lookback_mm=good_lookback_mm,
        marginal_day_mm=marginal_day_mm,
        marginal_lookback_mm=marginal_lookback_mm,
        soil_moisture=moisture,
        soil_moisture_max=soil_moisture_max,
    )
    start = rated[0]["date"] if rated else str(date.today())
    end = rated[-1]["date"] if rated else start
    return {
        **_meta(lat, lon, start, end, data.get("timezone", "auto")),
        "units": {"precipitation": "mm", "soil_moisture": "m³/m³"},
        "limits": {
            "good": {"day_mm": good_day_mm, "lookback_mm": good_lookback_mm},
            "marginal": {"day_mm": marginal_day_mm, "lookback_mm": marginal_lookback_mm},
            "lookback_days": lookback_days,
            "soil_moisture_max": soil_moisture_max,
        },
        "good_days": sum(1 for day in rated if day["rating"] == "good"),
        "longest_good_run": longest_good_run(rated),
        "days": rated,
    }


@mcp.tool()
async def get_wetness_hours(
    lat: float,
    lon: float,
    days: int = 3,
    min_rh_pct: float = 90.0,
    min_temp_c: float = 10.0,
    max_temp_c: float = 25.0,
    count_rain_hours: bool = True,
) -> dict[str, Any]:
    """Count forecast hours that look wet and warm enough for leaf wetness over 1 to 7 days.

    Use when: the question is how many hours of humid or rainy weather within a
    temperature range are forecast, as a leaf wetness proxy.
    Not for: rain minus evapotranspiration over weeks (use ``get_water_balance``)
    or timing a spray (use ``get_spray_window``).

    An hour is wet when temperature is within ``min_temp_c`` to ``max_temp_c``
    (inclusive) and either relative humidity is at or above ``min_rh_pct`` or, if
    ``count_rain_hours`` is true, precipitation in that hour is above 0 mm. An hour
    with missing data that cannot be decided is not counted and ends a run. Per
    day it returns wet hours and the longest consecutive wet run within that day;
    it also returns the total wet hours and the longest run over the whole
    period, which can cross midnight. The times of wet hours are listed only when
    ``days`` is 3 or fewer.

    This is a leaf wetness proxy only. Open-Meteo has no leaf wetness data, this
    is not a disease risk model, and no pathogen or crop presets are included.
    All thresholds are the caller's choice. Example: ``get_wetness_hours(45.0, 9.2, 3)``.
    """
    _location(lat, lon)
    _days(days, 7)
    if not 0 <= min_rh_pct <= 100:
        raise ValueError("min_rh_pct must be between 0 and 100")
    if min_temp_c > max_temp_c:
        raise ValueError("min_temp_c must not be above max_temp_c")
    start, end = date.today(), date.today() + timedelta(days=days - 1)
    data = await client().get(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "relative_humidity_2m,temperature_2m,precipitation",
            "forecast_days": days,
            "timezone": "auto",
        },
    )
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    flags = wet_flags(
        hourly.get("temperature_2m", []),
        hourly.get("relative_humidity_2m", []),
        hourly.get("precipitation", []),
        min_rh_pct=min_rh_pct,
        min_temp_c=min_temp_c,
        max_temp_c=max_temp_c,
        count_rain_hours=count_rain_hours,
    )
    result = {
        **_meta(lat, lon, str(start), str(end), data.get("timezone", "auto")),
        "units": {"hours": "hours", "temperature": "°C", "relative_humidity": "%"},
        "criteria": {
            "min_rh_pct": min_rh_pct,
            "temp_range_c": [min_temp_c, max_temp_c],
            "count_rain_hours": count_rain_hours,
        },
        "total_wet_hours": sum(flag is True for flag in flags),
        "longest_wet_run_hours": longest_run(flags),
        "hours_undetermined": sum(flag is None for flag in flags),
        "days": wetness_days(times, flags),
    }
    if days <= 3:
        result["wet_hour_times"] = [t for t, flag in zip(times, flags, strict=False) if flag]
    return result


def main() -> None:
    mcp.run(transport="stdio")
