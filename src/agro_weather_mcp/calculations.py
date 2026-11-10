"""Pure calculations used by the agriculture weather tools."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any


def _number(value: Any) -> float | None:
    # Open-Meteo can return null for unavailable observations. Keep those
    # values explicit so callers can distinguish missing data from zero.
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
            # The modified-average method caps both daily extremes before
            # averaging, which prevents unusually hot days from dominating.
            high_clamped = min(max(high_num, base_temp_c), upper_cap_c)
            low_clamped = min(max(low_num, base_temp_c), upper_cap_c)
            value = round(((high_clamped + low_clamped) / 2) - base_temp_c, 2)
        result.append({"date": day, "gdd": value})
    return result


def summarize_gdd(days: list[dict[str, str | float | None]]) -> dict[str, Any]:
    # Keep short requests detailed, but aggregate long requests to protect the
    # model context from hundreds of daily records.
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
        # Use evening through early morning as the practical overnight window.
        if parsed.hour >= 18 or parsed.hour < 9:
            day = str(parsed.date())
            grouped.setdefault(day, []).append(numeric)
    result = []
    for day, values in sorted(grouped.items()):
        minimum = min(values)
        hours = sum(value < threshold_c for value in values)
        # Risk is intentionally based on both threshold exposure and the
        # nightly minimum, so a brief threshold crossing is still visible.
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
        # A missing input means the daily balance is unknown, not zero.
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
    # Weekly groups are consecutive chunks, with a shorter final group when
    # the requested range does not end on a week boundary.
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


# ---------------------------------------------------------------------------
# Water balance interpretation
# ---------------------------------------------------------------------------

DRYNESS_RATE_MM_PER_DAY = 0.35
DRYNESS_FLOOR_MM = 5.0


def dryness_threshold_mm(n_days: int) -> float:
    """Return the balance magnitude that separates "balanced" from surplus or drying.

    The threshold scales with range length at 0.35 mm/day (about 10.5 mm over
    30 days) and never drops below 5 mm.
    """
    return round(max(DRYNESS_FLOOR_MM, DRYNESS_RATE_MM_PER_DAY * n_days), 2)


def dryness_note(total_balance_mm: float, n_days: int) -> str:
    threshold = dryness_threshold_mm(n_days)
    if total_balance_mm >= threshold:
        return "Likely water surplus"
    if total_balance_mm > -threshold:
        return "Roughly balanced"
    return "Likely drying conditions; monitor soil moisture and irrigation needs"


# ---------------------------------------------------------------------------
# Hourly series helpers
# ---------------------------------------------------------------------------

_HOUR_FORMAT = "%Y-%m-%dT%H:%M"


def _parse_hour(timestamp: str) -> datetime:
    # Open-Meteo returns local wall-clock labels such as "2025-04-01T06:00".
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(tzinfo=None)


def _label(moment: datetime) -> str:
    return moment.strftime(_HOUR_FORMAT)


def hour_after(timestamp: str) -> str:
    """Return the label of the hour following ``timestamp``."""
    return _label(_parse_hour(timestamp) + timedelta(hours=1))


def merge_hourly(
    parts: Iterable[dict[str, Any]],
    variables: list[str],
    start: date | None = None,
    end: date | None = None,
) -> dict[str, list[Any]]:
    """Merge hourly API payloads into one ordered, de-duplicated series.

    Each part holds a ``time`` list plus one list per variable. Overlapping hours
    are merged per variable: the earliest part with a non-null value wins, and
    later parts only fill gaps. When ``start`` and ``end`` are given, the result
    is trimmed to ``start 00:00`` through ``end 23:00`` and every hour in that
    range is present, with ``None`` for hours or values no part supplied.
    Without bounds, the grid spans the first through last hour seen.
    """
    cells: dict[datetime, dict[str, float | None]] = {}
    for part in parts:
        times = part.get("time", [])
        for variable in variables:
            values = part.get(variable, [])
            for index, timestamp in enumerate(times):
                moment = _parse_hour(timestamp)
                row = cells.setdefault(moment, {})
                incoming = _number(values[index]) if index < len(values) else None
                if row.get(variable) is None:
                    row[variable] = incoming
    if start is not None and end is not None:
        first = datetime.combine(start, datetime.min.time())
        last = datetime.combine(end, datetime.min.time()) + timedelta(hours=23)
    elif cells:
        first, last = min(cells), max(cells)
    else:
        return {"time": [], **{variable: [] for variable in variables}}
    merged: dict[str, list[Any]] = {"time": [], **{variable: [] for variable in variables}}
    moment = first
    while moment <= last:
        merged["time"].append(_label(moment))
        row = cells.get(moment, {})
        for variable in variables:
            merged[variable].append(row.get(variable))
        moment += timedelta(hours=1)
    return merged


def group_by_date(times: Iterable[str], values: Iterable[Any]) -> dict[str, list[float | None]]:
    """Group hourly values by the local date part of each timestamp, in order."""
    grouped: dict[str, list[float | None]] = {}
    for timestamp, value in zip(times, values, strict=False):
        grouped.setdefault(timestamp[:10], []).append(_number(value))
    return grouped


def longest_run(flags: Iterable[bool | None]) -> int:
    """Longest run of consecutive ``True`` values. ``None`` and ``False`` end a run."""
    best = current = 0
    for flag in flags:
        current = current + 1 if flag is True else 0
        best = max(best, current)
    return best


# ---------------------------------------------------------------------------
# Wet-bulb temperature and spray window
# ---------------------------------------------------------------------------

STULL_MIN_TEMP_C = -20.0
STULL_MAX_TEMP_C = 50.0
STULL_MIN_RH_PCT = 5.0
STULL_MAX_RH_PCT = 99.0


def wet_bulb_c(temp_c: Any, rh_pct: Any) -> float | None:
    """Wet-bulb temperature from air temperature and relative humidity.

    Uses the Stull (2011) empirical approximation for standard sea-level
    pressure (J. Appl. Meteor. Climatol., 50, 2267-2269). It is stated to be valid for
    -20 to 50 C and 5 to 99 % relative humidity, with errors between about -1
    and +0.65 C. ``None`` is returned for missing input or values outside that
    range. Pressure is not an input, so results at high elevation are less accurate.
    """
    t, rh = _number(temp_c), _number(rh_pct)
    if t is None or rh is None:
        return None
    if not (STULL_MIN_TEMP_C <= t <= STULL_MAX_TEMP_C):
        return None
    if not (STULL_MIN_RH_PCT <= rh <= STULL_MAX_RH_PCT):
        return None
    return (
        t * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def delta_t_c(temp_c: Any, rh_pct: Any) -> float | None:
    """Delta T: air temperature minus wet-bulb temperature, or ``None``."""
    wet_bulb = wet_bulb_c(temp_c, rh_pct)
    t = _number(temp_c)
    return None if wet_bulb is None or t is None else t - wet_bulb


def spray_hours(
    times: list[str],
    temperature: list[Any],
    humidity: list[Any],
    wind_kmh: list[Any],
    precipitation: list[Any],
    *,
    min_wind_kmh: float,
    max_wind_kmh: float,
    min_delta_t_c: float,
    max_delta_t_c: float,
    rain_free_hours: int,
    max_temp_c: float,
    rain_threshold_mm: float = 0.1,
    first_index: int = 0,
    count: int | None = None,
) -> list[dict[str, Any]]:
    """Evaluate each hour against the spray limits and name every limit it fails.

    Open-Meteo precipitation at a given hour is the sum of the preceding hour,
    so rain that falls during a spray hour is reported at the next label. The
    rain check therefore looks at the ``rain_free_hours`` labels after the hour.
    Rain data missing or beyond the series end makes the hour unsuitable with
    ``missing_data``. Only ``count`` hours from ``first_index`` are evaluated,
    but later hours are still read for the rain look-ahead.
    """
    stop = len(times) if count is None else min(len(times), first_index + count)
    hours = []
    for index in range(first_index, stop):
        t, rh = _number(temperature[index]), _number(humidity[index])
        wind = _number(wind_kmh[index])
        limits: list[str] = []
        if t is None or rh is None or wind is None:
            limits.append("missing_data")
        if wind is not None:
            if wind < min_wind_kmh:
                limits.append("wind_too_low")
            elif wind > max_wind_kmh:
                limits.append("wind_too_high")
        if t is not None and t > max_temp_c:
            limits.append("temperature_too_high")
        if t is not None and rh is not None:
            delta = delta_t_c(t, rh)
            if delta is None:
                limits.append("delta_t_unavailable")
            elif delta < min_delta_t_c:
                limits.append("delta_t_too_low")
            elif delta > max_delta_t_c:
                limits.append("delta_t_too_high")
        ahead = [_number(x) for x in precipitation[index + 1 : index + 1 + rain_free_hours]]
        if any(x is not None and x > rain_threshold_mm for x in ahead):
            limits.append("rain_expected")
        elif len(ahead) < rain_free_hours or any(x is None for x in ahead):
            if "missing_data" not in limits:
                limits.append("missing_data")
        hours.append({"time": times[index], "suitable": not limits, "limits": limits})
    return hours


def spray_windows(hours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive suitable hours. ``end`` is exclusive (start of the next hour)."""
    windows: list[dict[str, Any]] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            windows.append({"start": run[0], "end": hour_after(run[-1]), "hours": len(run)})
            run.clear()

    for item in hours:
        if item["suitable"]:
            run.append(item["time"])
        else:
            flush()
    flush()
    return windows


def spray_summary(hours: list[dict[str, Any]]) -> dict[str, Any]:
    """Windows, longest window and unsuitable-hour counts by limiting factor.

    An unsuitable hour that fails several limits is counted under each of them.
    """
    windows = spray_windows(hours)
    factors: dict[str, int] = {}
    for item in hours:
        for limit in item["limits"]:
            factors[limit] = factors.get(limit, 0) + 1
    longest = max(windows, key=lambda w: w["hours"], default=None)
    return {
        "evaluated_hours": len(hours),
        "suitable_hours": sum(1 for item in hours if item["suitable"]),
        "windows": windows,
        "longest_window": longest,
        "unsuitable_hours_by_limit": dict(sorted(factors.items())),
    }


# ---------------------------------------------------------------------------
# Heat stress
# ---------------------------------------------------------------------------


def heat_days(
    times: list[str], temperatures: list[Any], threshold_c: float
) -> list[dict[str, Any]]:
    """Per local day: maximum temperature and hours strictly above the threshold."""
    result = []
    for day, values in group_by_date(times, temperatures).items():
        known = [v for v in values if v is not None]
        result.append(
            {
                "date": day,
                "max_temp_c": round(max(known), 1) if known else None,
                "hours_above_threshold": sum(v > threshold_c for v in known),
            }
        )
    return result


def heat_summary(days: list[dict[str, Any]]) -> dict[str, int]:
    """Total hours above threshold and the longest streak of consecutive exceeding days.

    A day exceeds when at least one hour is above the threshold. A day with no
    temperature data does not exceed and ends a streak.
    """
    return {
        "total_hours_above_threshold": sum(d["hours_above_threshold"] for d in days),
        "longest_streak_days": longest_run(d["hours_above_threshold"] > 0 for d in days),
    }


# ---------------------------------------------------------------------------
# Chill hours and chill units
# ---------------------------------------------------------------------------

# Utah model (Richardson et al., 1974, HortScience 9(4):331-332). Weights are
# chill units per hour for the hour's temperature. Bands as commonly tabulated
# in degrees Celsius: <=1.4: 0; 1.5-2.4: 0.5; 2.5-9.1: 1.0; 9.2-12.4: 0.5;
# 12.5-15.9: 0; 16.0-18.0: -0.5; >18.0: -1.0. Continuous temperatures that
# fall between the tabulated tenths are assigned using the upper-bound cut
# points below (a value is in the first band whose limit it is below).
UTAH_BANDS: tuple[tuple[float, float], ...] = (
    (1.5, 0.0),
    (2.5, 0.5),
    (9.2, 1.0),
    (12.5, 0.5),
    (16.0, 0.0),
)


def utah_units(temp_c: Any) -> float | None:
    """Utah chill units for one hour, or ``None`` for a missing temperature."""
    t = _number(temp_c)
    if t is None:
        return None
    for upper, weight in UTAH_BANDS:
        if t < upper:
            return weight
    return -0.5 if t <= 18.0 else -1.0


def chill_hour_value(
    temp_c: Any, method: str, threshold_c: float, lower_bound_c: float | None
) -> float | None:
    t = _number(temp_c)
    if t is None:
        return None
    if method == "utah":
        return utah_units(t)
    within = t <= threshold_c and (lower_bound_c is None or t >= lower_bound_c)
    return 1.0 if within else 0.0


def chill_summary(
    times: list[str],
    temperatures: list[Any],
    method: str,
    threshold_c: float,
    lower_bound_c: float | None,
    target_hours: float | None = None,
) -> dict[str, Any]:
    """Total chill, monthly breakdown and optional percent of a user target.

    Hours with a missing temperature add nothing and are counted separately.
    Utah totals can be lower than zero because warm hours subtract units.
    """
    monthly: dict[str, float] = {}
    total = 0.0
    missing = 0
    for timestamp, temperature in zip(times, temperatures, strict=False):
        value = chill_hour_value(temperature, method, threshold_c, lower_bound_c)
        if value is None:
            missing += 1
            continue
        total += value
        month = timestamp[:7]
        monthly[month] = monthly.get(month, 0.0) + value
    result: dict[str, Any] = {
        "total": round(total, 1),
        "monthly": [{"month": m, "value": round(v, 1)} for m, v in sorted(monthly.items())],
        "hours_evaluated": len(times) - missing,
        "hours_missing": missing,
    }
    if target_hours is not None:
        result["target"] = target_hours
        result["percent_of_target"] = round(total / target_hours * 100, 1)
    return result


# ---------------------------------------------------------------------------
# Climate anomaly
# ---------------------------------------------------------------------------


def _shift_to_year(day: date, year: int) -> date:
    try:
        return day.replace(year=year)
    except ValueError:
        # Feb 29 has no counterpart in non-leap years; use Feb 28.
        return date(year, 2, 28)


def baseline_windows(
    start: date, end: date, first_year: int, last_year: int
) -> list[tuple[int, date, date]]:
    """Calendar windows matching ``start``..``end`` in each baseline year.

    Each window begins on the same month and day as ``start`` (Feb 29 maps to
    Feb 28 in non-leap years) and has the same number of days as the target, so
    totals are comparable. A window that spans New Year is labelled by the year
    it starts in. Windows extending beyond December 31 of ``last_year`` are
    dropped. Returns ``(year, window_start, window_end)`` tuples.
    """
    length = (end - start).days
    windows = []
    for year in range(first_year, last_year + 1):
        window_start = _shift_to_year(start, year)
        window_end = window_start + timedelta(days=length)
        if window_end > date(last_year, 12, 31):
            continue
        windows.append((year, window_start, window_end))
    return windows


def window_values(
    times: list[str],
    temperatures: list[Any],
    precipitation: list[Any],
    window_start: date,
    window_end: date,
) -> dict[str, float | None]:
    """Mean temperature and precipitation total for one window.

    A variable is ``None`` when any day in the window is missing, so incomplete
    windows never bias the comparison.
    """
    temp_by_day = {t: _number(v) for t, v in zip(times, temperatures, strict=False)}
    rain_by_day = {t: _number(v) for t, v in zip(times, precipitation, strict=False)}
    days = [
        str(window_start + timedelta(days=i)) for i in range((window_end - window_start).days + 1)
    ]
    temps = [temp_by_day.get(d) for d in days]
    rains = [rain_by_day.get(d) for d in days]
    return {
        "mean_temp_c": sum(temps) / len(temps) if all(v is not None for v in temps) else None,
        "precip_mm": sum(rains) if all(v is not None for v in rains) else None,
    }


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def sample_std(values: list[float]) -> float | None:
    """Sample standard deviation (n - 1), or ``None`` with fewer than two values."""
    if len(values) < 2:
        return None
    centre = sum(values) / len(values)
    return math.sqrt(sum((v - centre) ** 2 for v in values) / (len(values) - 1))


def percentile_rank(target: float, baseline: list[float]) -> float | None:
    """Mid-rank percentile of ``target`` among ``baseline``: (below + 0.5 * equal) / n * 100."""
    if not baseline:
        return None
    below = sum(v < target for v in baseline)
    equal = sum(v == target for v in baseline)
    return (below + 0.5 * equal) / len(baseline) * 100


def anomaly_stats(
    target: float, baseline: list[float], precipitation: bool = False
) -> dict[str, Any]:
    """Target versus baseline: mean, std, anomaly, percentile and (for rain) percent of normal."""
    centre, spread = mean(baseline), sample_std(baseline)
    key = "mm" if precipitation else "c"
    result: dict[str, Any] = {
        "target": round(target, 2),
        "baseline_mean": None if centre is None else round(centre, 2),
        "baseline_std": None if spread is None else round(spread, 2),
        f"anomaly_{key}": None if centre is None else round(target - centre, 2),
        "percentile_rank": None
        if centre is None
        else round(percentile_rank(target, baseline) or 0.0, 1),
    }
    if precipitation:
        result["percent_of_normal"] = (
            round(target / centre * 100, 1) if centre not in (None, 0) else None
        )
    return result


# ---------------------------------------------------------------------------
# Fieldwork window
# ---------------------------------------------------------------------------

_RATINGS = ("good", "marginal", "poor")


def daily_means(times: list[str], values: list[Any]) -> dict[str, float | None]:
    """Mean of the non-null hourly values for each local date."""
    result: dict[str, float | None] = {}
    for day, group in group_by_date(times, values).items():
        known = [v for v in group if v is not None]
        result[day] = sum(known) / len(known) if known else None
    return result


def fieldwork_ratings(
    dates: list[str],
    precipitation: list[Any],
    lookback_days: int,
    *,
    first_index: int,
    good_day_mm: float,
    good_lookback_mm: float,
    marginal_day_mm: float,
    marginal_lookback_mm: float,
    soil_moisture: dict[str, float | None] | None = None,
    soil_moisture_max: float | None = None,
) -> list[dict[str, Any]]:
    """Rate each day from ``first_index`` onward using rain on the day and before it.

    The lookback total is the rain on the ``lookback_days`` days before the day
    (the day itself is excluded). A day is ``good`` when both values are at or
    under the good limits, ``marginal`` within the marginal limits, otherwise
    ``poor``. If ``soil_moisture_max`` is set and the day's mean topsoil moisture
    exceeds it, the rating drops one level. Missing rain makes the rating
    ``unknown``.
    """
    rain = [_number(x) for x in precipitation]
    result = []
    for index in range(first_index, len(dates)):
        day_rain = rain[index]
        window = rain[max(0, index - lookback_days) : index]
        reasons: list[str] = []
        if day_rain is None or len(window) < lookback_days or any(x is None for x in window):
            result.append(
                {
                    "date": dates[index],
                    "rating": "unknown",
                    "rain_mm": day_rain,
                    "lookback_rain_mm": None,
                    "reasons": ["precipitation data missing for the day or the lookback period"],
                }
            )
            continue
        lookback = sum(window)
        if day_rain <= good_day_mm and lookback <= good_lookback_mm:
            rating = "good"
            reasons.append(
                f"rain {day_rain:.1f} mm and {lookback_days}-day lookback "
                f"{lookback:.1f} mm are within the good limits"
            )
        elif day_rain <= marginal_day_mm and lookback <= marginal_lookback_mm:
            rating = "marginal"
            if day_rain > good_day_mm:
                reasons.append(f"rain {day_rain:.1f} mm exceeds good limit {good_day_mm:g} mm")
            if lookback > good_lookback_mm:
                reasons.append(
                    f"{lookback_days}-day lookback {lookback:.1f} mm exceeds good limit "
                    f"{good_lookback_mm:g} mm"
                )
        else:
            rating = "poor"
            if day_rain > marginal_day_mm:
                reasons.append(
                    f"rain {day_rain:.1f} mm exceeds marginal limit {marginal_day_mm:g} mm"
                )
            if lookback > marginal_lookback_mm:
                reasons.append(
                    f"{lookback_days}-day lookback {lookback:.1f} mm exceeds marginal limit "
                    f"{marginal_lookback_mm:g} mm"
                )
        moisture = None if soil_moisture is None else soil_moisture.get(dates[index])
        if soil_moisture_max is not None and moisture is not None and moisture > soil_moisture_max:
            lowered = _RATINGS[min(_RATINGS.index(rating) + 1, len(_RATINGS) - 1)]
            if lowered != rating:
                reasons.append(
                    f"topsoil moisture {moisture:.3f} m3/m3 exceeds {soil_moisture_max:g}, "
                    f"downgraded from {rating}"
                )
                rating = lowered
            else:
                reasons.append(
                    f"topsoil moisture {moisture:.3f} m3/m3 exceeds {soil_moisture_max:g}"
                )
        entry: dict[str, Any] = {
            "date": dates[index],
            "rating": rating,
            "rain_mm": round(day_rain, 1),
            "lookback_rain_mm": round(lookback, 1),
            "reasons": reasons,
        }
        if soil_moisture_max is not None:
            entry["topsoil_moisture_m3_m3"] = None if moisture is None else round(moisture, 3)
        result.append(entry)
    return result


def longest_good_run(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Longest run of consecutive days rated ``good``, or ``None`` if there is none."""
    best: tuple[int, int] | None = None
    start = None
    for index, day in enumerate([*days, {"rating": None}]):
        if day["rating"] == "good":
            start = index if start is None else start
        elif start is not None:
            if best is None or index - start > best[1] - best[0]:
                best = (start, index)
            start = None
    if best is None:
        return None
    return {
        "start": days[best[0]]["date"],
        "end": days[best[1] - 1]["date"],
        "days": best[1] - best[0],
    }


# ---------------------------------------------------------------------------
# Leaf wetness proxy
# ---------------------------------------------------------------------------


def wet_flags(
    temperature: list[Any],
    humidity: list[Any],
    precipitation: list[Any],
    *,
    min_rh_pct: float,
    min_temp_c: float,
    max_temp_c: float,
    count_rain_hours: bool,
) -> list[bool | None]:
    """Per-hour wetness proxy: ``True``, ``False`` or ``None`` when it cannot be decided.

    Wet means temperature inside the inclusive range and either humidity at or
    above the minimum or, when enabled, precipitation above zero in that hour.
    A missing temperature gives ``None``. Missing humidity gives ``None`` unless
    rain already makes the hour wet.
    """
    flags: list[bool | None] = []
    for temp, rh, rain in zip(temperature, humidity, precipitation, strict=False):
        t, h, p = _number(temp), _number(rh), _number(rain)
        if t is None:
            flags.append(None)
            continue
        if not (min_temp_c <= t <= max_temp_c):
            flags.append(False)
            continue
        rainy = count_rain_hours and p is not None and p > 0
        if rainy or (h is not None and h >= min_rh_pct):
            flags.append(True)
        elif h is None or (count_rain_hours and p is None):
            flags.append(None)
        else:
            flags.append(False)
    return flags


def wetness_days(times: list[str], flags: list[bool | None]) -> list[dict[str, Any]]:
    """Per local day: wet hours and the longest consecutive wet run within that day."""
    grouped: dict[str, list[bool | None]] = {}
    for timestamp, flag in zip(times, flags, strict=False):
        grouped.setdefault(timestamp[:10], []).append(flag)
    return [
        {
            "date": day,
            "wet_hours": sum(f is True for f in values),
            "longest_wet_run_hours": longest_run(values),
        }
        for day, values in grouped.items()
    ]
