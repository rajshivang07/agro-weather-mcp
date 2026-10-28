"""Pure calculations used by the agriculture weather tools."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def daily_gdd(
    dates: Iterable[str],
    tmax: Iterable[Any],
    tmin: Iterable[Any],
    base_temp_c: float,
    upper_cap_c: float,
) -> list[dict[str, str | float | None]]:
    """Calculate modified-average GDD, skipping days with missing temperatures."""
    result = []
    for day, high, low in zip(dates, tmax, tmin, strict=False):
        high_num, low_num = _number(high), _number(low)
        value = None
        if high_num is not None and low_num is not None:
            high_clamped = min(max(high_num, base_temp_c), upper_cap_c)
            low_clamped = min(max(low_num, base_temp_c), upper_cap_c)
            value = round(((high_clamped + low_clamped) / 2) - base_temp_c, 2)
        result.append({"date": day, "gdd": value})
    return result


def summarize_gdd(days: list[dict[str, str | float | None]]) -> dict[str, Any]:
    total = round(sum(float(item["gdd"]) for item in days if item["gdd"] is not None), 2)
    if len(days) <= 31:
        return {"total_gdd": total, "daily": days}
    weekly: list[dict[str, Any]] = []
    for index in range(0, len(days), 7):
        group = days[index : index + 7]
        weekly.append(
            {
                "start_date": group[0]["date"],
                "end_date": group[-1]["date"],
                "gdd": round(sum(float(x["gdd"]) for x in group if x["gdd"] is not None), 2),
            }
        )
    return {"total_gdd": total, "weekly": weekly}


def frost_nights(
    times: Iterable[str], temperatures: Iterable[Any], threshold_c: float
) -> list[dict[str, Any]]:
    """Group hourly temperatures by local date and summarize night hours."""
    grouped: dict[str, list[float]] = {}
    for timestamp, temperature in zip(times, temperatures, strict=False):
        numeric = _number(temperature)
        if numeric is None:
            continue
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.hour >= 18 or parsed.hour < 9:
            day = str(parsed.date() if parsed.hour >= 18 else parsed.date())
            grouped.setdefault(day, []).append(numeric)
    result = []
    for day, values in sorted(grouped.items()):
        minimum = min(values)
        hours = sum(value < threshold_c for value in values)
        risk = (
            "none"
            if hours == 0
            else "low"
            if minimum > threshold_c - 2
            else "moderate"
            if minimum > threshold_c - 5
            else "high"
        )
        result.append(
            {
                "date": day,
                "min_temp_c": round(minimum, 1),
                "risk": risk,
                "hours_below_threshold": hours,
            }
        )
    return result


def water_balance(
    dates: Iterable[str], precipitation: Iterable[Any], et0: Iterable[Any]
) -> list[dict[str, str | float | None]]:
    """Calculate daily precipitation minus ET0, retaining missing values."""
    result = []
    for day, rain, evap in zip(dates, precipitation, et0, strict=False):
        rain_num, evap_num = _number(rain), _number(evap)
        result.append(
            {
                "date": day,
                "balance_mm": round(rain_num - evap_num, 2)
                if rain_num is not None and evap_num is not None
                else None,
            }
        )
    return result


def weekly_balance(days: list[dict[str, str | float | None]]) -> list[dict[str, Any]]:
    result = []
    for index in range(0, len(days), 7):
        group = days[index : index + 7]
        result.append(
            {
                "start_date": group[0]["date"],
                "end_date": group[-1]["date"],
                "balance_mm": round(
                    sum(float(x["balance_mm"]) for x in group if x["balance_mm"] is not None), 2
                ),
            }
        )
    return result


def parse_iso_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date in YYYY-MM-DD format") from exc
