"""FastMCP stdio server exposing agriculture weather tools."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from .calculations import (
    daily_gdd,
    frost_nights,
    parse_iso_date,
    summarize_gdd,
    water_balance,
    weekly_balance,
)
from .client import OpenMeteoClient

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
SOURCE = "Open-Meteo (CC BY 4.0)"
MAX_FORECAST_DAYS = 16

mcp = FastMCP("agro-weather-mcp")
_client: OpenMeteoClient | None = None
_client_loop: object | None = None


def client() -> OpenMeteoClient:
    global _client, _client_loop
    import asyncio

    current_loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not current_loop:
        _client = OpenMeteoClient()
        _client_loop = current_loop
    return _client


def _location(lat: float, lon: float) -> None:
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
    if end >= today and (end - today).days >= MAX_FORECAST_DAYS:
        raise ValueError("forecast requests can extend at most 16 days from today")
    return start, end


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
    merged = {"time": []}
    for variable in variables:
        merged[variable] = []
    for part in parts:
        daily = part.get("daily", {})
        for key in merged:
            merged[key].extend(daily.get(key, []))
    return merged


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

    ``threshold_c`` is degrees Celsius and days is a forecast horizon. A night is
    low risk when its minimum is above threshold minus 2 C, moderate when it is
    above threshold minus 5 C, and high at or below threshold minus 5 C. Any
    hours below threshold make risk at least low. The result includes per-night
    minimum temperature and hours below threshold, in the forecast timezone.
    Example: ``get_frost_risk(45.52, -122.68, 7, 0.0)``.
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

    Dates use ISO ``YYYY-MM-DD``, are inclusive, and span at most 366 days.
    Precipitation, ET0, and the resulting balance are millimetres. ET0 is
    ``et0_fao_evapotranspiration`` for a well-watered reference grass field.
    Results include total precipitation, total ET0, total balance, weekly
    breakdowns, and a plain-language dryness note. Past and future ranges are
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
    note = (
        "Likely water surplus"
        if total >= 10
        else "Roughly balanced"
        if total > -10
        else "Likely drying conditions; monitor soil moisture and irrigation needs"
    )
    return {
        **_meta(lat, lon, str(start), str(end), timezone),
        "units": {"precipitation": "mm", "et0": "mm", "balance": "mm"},
        "total_precipitation_mm": round(precipitation, 2),
        "total_et0_mm": round(et0, 2),
        "total_balance_mm": total,
        "weekly": weekly_balance(balances),
        "dryness_note": note,
    }


@mcp.tool()
async def get_soil_conditions(lat: float, lon: float) -> dict[str, Any]:
    """Summarize current and next-48-hour soil conditions.

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
    times = hourly.get("time", [])[:49]
    depths = ("0_to_7cm", "7_to_28cm", "28_to_100cm", "100_to_255cm")

    def stats(values: list[Any]) -> dict[str, float | None]:
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


def main() -> None:
    mcp.run(transport="stdio")
