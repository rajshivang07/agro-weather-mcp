import math
from datetime import date

from agro_weather_mcp.calculations import (
    anomaly_stats,
    baseline_windows,
    chill_summary,
    daily_gdd,
    daily_means,
    delta_t_c,
    fieldwork_ratings,
    frost_nights,
    heat_days,
    heat_summary,
    longest_good_run,
    spray_hours,
    spray_summary,
    spray_windows,
    summarize_gdd,
    utah_units,
    water_balance,
    weekly_balance,
    wet_bulb_c,
    wet_flags,
    wetness_days,
    window_values,
)


def test_gdd_clamps_both_ends_and_skips_missing():
    values = daily_gdd(
        ["2025-01-01", "2025-01-02", "2025-01-03"], [5, 35, None], [-2, 25, 10], 10, 30
    )
    assert values == [
        {"date": "2025-01-01", "gdd": 0.0},
        {"date": "2025-01-02", "gdd": 17.5},
        {"date": "2025-01-03", "gdd": None},
    ]


def test_gdd_weekly_summary():
    days = [{"date": f"2025-01-{i:02d}", "gdd": 1.0} for i in range(1, 33)]
    result = summarize_gdd(days)
    assert result["total_gdd"] == 32.0
    assert result["weekly"][0]["gdd"] == 7.0


def test_frost_risk_rules_and_missing_values():
    times = ["2025-01-01T18:00", "2025-01-01T20:00", "2025-01-01T02:00", "2025-01-01T10:00"]
    assert frost_nights(times, [1, -1, -6, None], 0)[0]["risk"] == "high"
    assert frost_nights(times, [1, -1, -6, None], 0)[0]["hours_below_threshold"] == 2


def test_water_balance_and_weekly_sum():
    days = water_balance(["2025-01-01", "2025-01-02"], [10, None], [4, 3])
    assert days == [
        {"date": "2025-01-01", "balance_mm": 6.0},
        {"date": "2025-01-02", "balance_mm": None},
    ]
    assert weekly_balance(days)[0]["balance_mm"] == 6.0


def test_dryness_threshold_scales_with_range_and_has_floor():
    from agro_weather_mcp.calculations import dryness_note, dryness_threshold_mm

    assert dryness_threshold_mm(7) == 5.0
    assert dryness_threshold_mm(30) == 10.5
    assert dryness_threshold_mm(366) == 128.1
    # 8 mm is a surplus over a week but roughly balanced over a season.
    assert dryness_note(8, 7) == "Likely water surplus"
    assert dryness_note(8, 90) == "Roughly balanced"
    assert dryness_note(-5.0, 7).startswith("Likely drying")
    assert dryness_note(-4.9, 7) == "Roughly balanced"


# ---------------------------------------------------------------------------
# Wet-bulb, Delta T and spray hours
# ---------------------------------------------------------------------------


def _psychrometric_wet_bulb(temp_c, rh_pct, pressure_hpa=1013.25):
    """Independent reference: solve the psychrometer equation by bisection (Magnus es)."""

    def es(t):
        return 6.112 * math.exp(17.62 * t / (243.12 + t))

    vapour = rh_pct / 100 * es(temp_c)
    low, high = temp_c - 40, temp_c
    for _ in range(80):
        mid = (low + high) / 2
        implied = es(mid) - 0.000662 * pressure_hpa * (temp_c - mid)
        low, high = (mid, high) if implied < vapour else (low, mid)
    return (low + high) / 2


def test_wet_bulb_matches_stulls_published_example():
    # Stull (2011) gives about 13.7 C for 20 C at 50 % relative humidity.
    assert round(wet_bulb_c(20, 50), 1) == 13.7
    assert round(delta_t_c(20, 50), 1) == 6.3


def test_wet_bulb_agrees_with_independent_psychrometric_solution():
    # Stull states errors between -1 and +0.65 C over the valid range.
    for temp, rh in [(30, 60), (10, 80), (25, 20), (0, 70), (40, 30), (-10, 60)]:
        assert abs(wet_bulb_c(temp, rh) - _psychrometric_wet_bulb(temp, rh)) < 1.0


def test_wet_bulb_is_none_outside_validity_range_and_for_missing_input():
    assert wet_bulb_c(20, 4.9) is None
    assert wet_bulb_c(20, 99.1) is None
    assert wet_bulb_c(20, 100) is None
    assert wet_bulb_c(-20.1, 50) is None
    assert wet_bulb_c(50.1, 50) is None
    assert wet_bulb_c(None, 50) is None
    assert wet_bulb_c(20, None) is None
    assert delta_t_c(20, 100) is None
    # Boundaries are inclusive.
    assert wet_bulb_c(-20, 5) is not None and wet_bulb_c(50, 99) is not None


def _spray_inputs(n=8):
    times = [f"2025-06-01T{h:02d}:00" for h in range(n)]
    return {
        "times": times,
        "temperature": [20] * n,
        "humidity": [50] * n,
        "wind_kmh": [10] * n,
        "precipitation": [0] * n,
    }


_SPRAY_LIMITS = {
    "min_wind_kmh": 3.0,
    "max_wind_kmh": 15.0,
    "min_delta_t_c": 2.0,
    "max_delta_t_c": 8.0,
    "rain_free_hours": 2,
    "max_temp_c": 30.0,
}


def test_spray_hours_windows_and_limiting_factors():
    data = _spray_inputs()
    data["wind_kmh"][3] = 20
    data["precipitation"][5] = 0.5
    hours = spray_hours(**data, **_SPRAY_LIMITS, count=6)
    assert [h["suitable"] for h in hours] == [True, True, True, False, False, True]
    # Hour 3 looks ahead at labels 4 and 5, so rain at 05:00 also limits hours 3 and 4.
    assert hours[3]["limits"] == ["wind_too_high", "rain_expected"]
    assert hours[4]["limits"] == ["rain_expected"]
    summary = spray_summary(hours)
    assert summary["windows"] == [
        {"start": "2025-06-01T00:00", "end": "2025-06-01T03:00", "hours": 3},
        {"start": "2025-06-01T05:00", "end": "2025-06-01T06:00", "hours": 1},
    ]
    assert summary["longest_window"]["hours"] == 3
    assert summary["suitable_hours"] == 4
    assert summary["unsuitable_hours_by_limit"] == {"rain_expected": 2, "wind_too_high": 1}


def test_spray_rain_threshold_is_strictly_above_point_one_mm():
    data = _spray_inputs(5)
    data["precipitation"][2] = 0.1
    assert all(h["suitable"] for h in spray_hours(**data, **_SPRAY_LIMITS, count=2))
    data["precipitation"][2] = 0.11
    assert not spray_hours(**data, **_SPRAY_LIMITS, count=2)[1]["suitable"]


def test_spray_delta_t_limits_and_unavailable_cases():
    data = _spray_inputs(4)
    data["humidity"] = [95, 10, 100, 50]
    hours = spray_hours(**data, **_SPRAY_LIMITS, count=4)
    assert "delta_t_too_low" in hours[0]["limits"]
    assert "delta_t_too_high" in hours[1]["limits"]
    assert "delta_t_unavailable" in hours[2]["limits"]
    # Hour 3 has no full rain look-ahead in the series, so it cannot be confirmed.
    assert hours[3]["limits"] == ["missing_data"]


def test_spray_temperature_wind_low_and_missing_values():
    data = _spray_inputs(6)
    data["temperature"][0] = 31
    data["wind_kmh"][1] = 2.9
    data["temperature"][2] = None
    data["precipitation"][4] = None
    hours = spray_hours(**data, **_SPRAY_LIMITS, count=4)
    assert hours[0]["limits"] == ["temperature_too_high"]
    assert hours[1]["limits"] == ["wind_too_low"]
    assert hours[2]["limits"] == ["missing_data"]
    # Limit values themselves are suitable (inclusive), but missing rain ahead is not.
    assert hours[3]["limits"] == ["missing_data"]


def test_spray_windows_empty_when_nothing_suitable():
    assert spray_windows([{"time": "2025-06-01T00:00", "suitable": False, "limits": ["x"]}]) == []
    assert spray_summary([])["longest_window"] is None


# ---------------------------------------------------------------------------
# Heat stress
# ---------------------------------------------------------------------------


def test_heat_days_counts_strictly_above_threshold_and_ignores_missing():
    times = [f"2025-07-01T{h:02d}:00" for h in range(4)] + [
        f"2025-07-02T{h:02d}:00" for h in range(2)
    ]
    temps = [30, 36, 37.46, None, 34, 35]
    days = heat_days(times, temps, 35)
    assert days == [
        {"date": "2025-07-01", "max_temp_c": 37.5, "hours_above_threshold": 2},
        {"date": "2025-07-02", "max_temp_c": 35.0, "hours_above_threshold": 0},
    ]


def test_heat_summary_streak_total_and_missing_day():
    def day(n, hours, maximum="x"):
        return {"date": f"2025-07-{n:02d}", "max_temp_c": maximum, "hours_above_threshold": hours}

    days = [day(1, 3), day(2, 1), day(3, 5), day(4, 0), day(5, 2)]
    assert heat_summary(days) == {"total_hours_above_threshold": 11, "longest_streak_days": 3}
    # A day with no data (max None, zero hours) breaks the streak.
    gap = [day(1, 2), day(2, 0, None), day(3, 2)]
    assert heat_summary(gap)["longest_streak_days"] == 1
    assert heat_summary([]) == {"total_hours_above_threshold": 0, "longest_streak_days": 0}


# ---------------------------------------------------------------------------
# Chill hours and Utah units
# ---------------------------------------------------------------------------


def test_utah_units_band_edges_and_negative_bands():
    expected = {
        1.4: 0.0,
        1.45: 0.0,
        1.5: 0.5,
        2.4: 0.5,
        2.5: 1.0,
        9.1: 1.0,
        9.2: 0.5,
        12.4: 0.5,
        12.5: 0.0,
        15.9: 0.0,
        16.0: -0.5,
        18.0: -0.5,
        18.1: -1.0,
        35.0: -1.0,
        -5.0: 0.0,
    }
    for temperature, units in expected.items():
        assert utah_units(temperature) == units, temperature
    assert utah_units(None) is None


def test_chill_below_threshold_with_and_without_lower_bound():
    times = [f"2025-01-31T{h:02d}:00" for h in range(4)] + ["2025-02-01T00:00", "2025-02-01T01:00"]
    temps = [-1, 0, 3, 7.2, 7.3, None]
    bounded = chill_summary(times, temps, "below_threshold", 7.2, 0.0)
    # 0, 3 and 7.2 are inside [0, 7.2]; -1 is below the bound; 7.3 is above.
    assert bounded["total"] == 3.0
    assert bounded["monthly"] == [
        {"month": "2025-01", "value": 3.0},
        {"month": "2025-02", "value": 0.0},
    ]
    assert bounded["hours_evaluated"] == 5 and bounded["hours_missing"] == 1
    assert "percent_of_target" not in bounded
    unbounded = chill_summary(times, temps, "below_threshold", 7.2, None, target_hours=8)
    assert unbounded["total"] == 4.0
    assert unbounded["percent_of_target"] == 50.0


def test_chill_utah_total_can_be_negative_and_splits_by_month():
    times = ["2025-01-31T00:00", "2025-01-31T01:00", "2025-02-01T00:00", "2025-02-01T01:00"]
    # 5 C gives +1.0, 20 C gives -1.0, 2.0 C gives +0.5, 17 C gives -0.5.
    result = chill_summary(times, [5, 20, 2.0, 17], "utah", 7.2, 0.0, target_hours=4)
    assert result["monthly"] == [
        {"month": "2025-01", "value": 0.0},
        {"month": "2025-02", "value": 0.0},
    ]
    warm = chill_summary(times, [20, 20, 17, None], "utah", 7.2, 0.0)
    assert warm["total"] == -2.5 and warm["hours_missing"] == 1
    assert result["percent_of_target"] == 0.0


# ---------------------------------------------------------------------------
# Climate anomaly
# ---------------------------------------------------------------------------


def test_anomaly_temperature_mean_std_and_percentile():
    stats = anomaly_stats(15, [10, 12, 14, 16, 18])
    # Mean 14; squared deviations 16+4+0+4+16 = 40; sample variance 40/4 = 10.
    assert stats["baseline_mean"] == 14.0
    assert stats["baseline_std"] == 3.16
    assert stats["anomaly_c"] == 1.0
    assert stats["percentile_rank"] == 60.0  # 3 of 5 below, none equal


def test_anomaly_precipitation_percent_of_normal_and_ties():
    stats = anomaly_stats(150, [100, 200, 300], precipitation=True)
    assert stats["anomaly_mm"] == -50.0
    assert stats["percent_of_normal"] == 75.0
    assert stats["percentile_rank"] == 33.3
    # A tie counts half: (1 below + 0.5 * 1 equal) / 3.
    assert anomaly_stats(200, [100, 200, 300], precipitation=True)["percentile_rank"] == 50.0
    # A zero normal has no percent of normal.
    assert anomaly_stats(5, [0, 0, 0], precipitation=True)["percent_of_normal"] is None


def test_anomaly_with_empty_or_single_value_baseline():
    empty = anomaly_stats(5, [])
    assert empty["baseline_mean"] is None and empty["anomaly_c"] is None
    single = anomaly_stats(5, [4])
    assert single["baseline_std"] is None and single["anomaly_c"] == 1.0


def test_baseline_windows_handle_leap_day_and_keep_length():
    # 2024-02-28 to 2024-03-02 includes Feb 29, so it has 4 days.
    windows = baseline_windows(date(2024, 2, 28), date(2024, 3, 2), 2019, 2021)
    assert windows == [
        (2019, date(2019, 2, 28), date(2019, 3, 3)),
        (2020, date(2020, 2, 28), date(2020, 3, 2)),
        (2021, date(2021, 2, 28), date(2021, 3, 3)),
    ]
    for _year, first, last in windows:
        assert (last - first).days == 3
    # A window starting on Feb 29 starts on Feb 28 in non-leap years.
    starts = baseline_windows(date(2024, 2, 29), date(2024, 3, 2), 2019, 2020)
    assert [(w[1], w[2]) for w in starts] == [
        (date(2019, 2, 28), date(2019, 3, 2)),
        (date(2020, 2, 29), date(2020, 3, 2)),
    ]


def test_baseline_windows_spanning_new_year_drop_windows_past_last_year():
    windows = baseline_windows(date(2023, 12, 25), date(2024, 1, 5), 2019, 2021)
    assert windows == [
        (2019, date(2019, 12, 25), date(2020, 1, 5)),
        (2020, date(2020, 12, 25), date(2021, 1, 5)),
    ]


def test_window_values_mean_total_and_missing_days():
    times = ["2024-02-28", "2024-02-29", "2024-03-01"]
    full = window_values(times, [10, 12, 14], [1, 0, 2.5], date(2024, 2, 28), date(2024, 3, 1))
    assert full == {"mean_temp_c": 12.0, "precip_mm": 3.5}
    # One null temperature invalidates that variable only.
    partial = window_values(times, [10, None, 14], [1, 0, 2.5], date(2024, 2, 28), date(2024, 3, 1))
    assert partial == {"mean_temp_c": None, "precip_mm": 3.5}
    # A date absent from the series is treated as missing too.
    short = window_values(times, [10, 12, 14], [1, 0, 2.5], date(2024, 2, 28), date(2024, 3, 2))
    assert short == {"mean_temp_c": None, "precip_mm": None}


# ---------------------------------------------------------------------------
# Fieldwork window
# ---------------------------------------------------------------------------

_FIELD_LIMITS = {
    "good_day_mm": 1.0,
    "good_lookback_mm": 5.0,
    "marginal_day_mm": 5.0,
    "marginal_lookback_mm": 15.0,
}


def _field_dates(n):
    return [f"2025-06-{i + 1:02d}" for i in range(n)]


def test_fieldwork_ratings_with_lookback_and_reasons():
    dates = _field_dates(8)
    precipitation = [0, 0, 0.5, 6, 0, 0, 0, 0]
    days = fieldwork_ratings(dates, precipitation, 2, first_index=2, **_FIELD_LIMITS)
    assert [d["rating"] for d in days] == ["good", "poor", "marginal", "marginal", "good", "good"]
    # Day 4: no rain, but the 2-day lookback is 0.5 + 6 = 6.5 mm, above the good limit of 5.
    assert days[2]["lookback_rain_mm"] == 6.5
    assert "exceeds good limit 5 mm" in days[2]["reasons"][0]
    assert "exceeds marginal limit 5 mm" in days[1]["reasons"][0]
    assert longest_good_run(days) == {"start": "2025-06-07", "end": "2025-06-08", "days": 2}


def test_fieldwork_limits_are_inclusive():
    days = fieldwork_ratings(_field_dates(3), [2.5, 2.5, 1.0], 2, first_index=2, **_FIELD_LIMITS)
    assert days[0]["rating"] == "good"  # rain 1.0 <= 1.0 and lookback 5.0 <= 5.0


def test_fieldwork_soil_moisture_downgrades_one_level():
    dates = _field_dates(8)
    precipitation = [0, 0, 0.5, 6, 0, 0, 0, 0]
    moisture = {"2025-06-03": 0.50, "2025-06-04": 0.50, "2025-06-06": 0.50, "2025-06-07": 0.45}
    days = fieldwork_ratings(
        dates,
        precipitation,
        2,
        first_index=2,
        soil_moisture=moisture,
        soil_moisture_max=0.40,
        **_FIELD_LIMITS,
    )
    ratings = {d["date"]: d["rating"] for d in days}
    assert ratings["2025-06-03"] == "marginal"  # good -> marginal
    assert ratings["2025-06-04"] == "poor"  # already poor stays poor
    assert ratings["2025-06-06"] == "poor"  # marginal -> poor
    assert ratings["2025-06-07"] == "marginal"  # good -> marginal
    assert ratings["2025-06-08"] == "good"  # no moisture value for that day
    assert any("downgraded from good" in reason for reason in days[0]["reasons"])
    assert not any("downgraded" in reason for reason in days[1]["reasons"])
    assert days[0]["topsoil_moisture_m3_m3"] == 0.5
    # Moisture exactly at the limit is not above it.
    at_limit = fieldwork_ratings(
        dates,
        precipitation,
        2,
        first_index=2,
        soil_moisture={"2025-06-03": 0.40},
        soil_moisture_max=0.40,
        **_FIELD_LIMITS,
    )
    assert at_limit[0]["rating"] == "good"
    assert longest_good_run(days) == {"start": "2025-06-08", "end": "2025-06-08", "days": 1}


def test_fieldwork_missing_data_is_unknown_and_breaks_runs():
    dates = _field_dates(6)
    days = fieldwork_ratings(dates, [0, 0, 0, None, 0, 0], 2, first_index=2, **_FIELD_LIMITS)
    # Day 4 has no rain value, and days 5 and 6 have it in their lookback window.
    assert [d["rating"] for d in days] == ["good", "unknown", "unknown", "unknown"]
    assert longest_good_run(days) == {"start": "2025-06-03", "end": "2025-06-03", "days": 1}
    short = fieldwork_ratings(dates[:3], [0, 0, 0], 5, first_index=2, **_FIELD_LIMITS)
    assert short[0]["rating"] == "unknown"  # lookback window is incomplete
    assert longest_good_run(short) is None
    assert longest_good_run([]) is None


def test_daily_means_ignore_nulls():
    times = ["2025-06-01T00:00", "2025-06-01T01:00", "2025-06-02T00:00"]
    assert daily_means(times, [0.2, None, None]) == {"2025-06-01": 0.2, "2025-06-02": None}


# ---------------------------------------------------------------------------
# Wetness hours
# ---------------------------------------------------------------------------

_WET = {"min_rh_pct": 90.0, "min_temp_c": 10.0, "max_temp_c": 25.0}


def test_wet_flags_rules_ranges_rain_and_missing_values():
    temps = [12, 12, 12, 30, 12, None, 12]
    rh = [95, 80, 90, 95, 91, 95, None]
    rain = [0, 0.2, 0, 0, 0, 0, 0]
    flags = wet_flags(temps, rh, rain, count_rain_hours=True, **_WET)
    # h1 is wet only through rain, h2 sits exactly on the humidity limit, h3 is too hot.
    assert flags == [True, True, True, False, True, None, None]
    no_rain = wet_flags(temps, rh, rain, count_rain_hours=False, **_WET)
    assert no_rain == [True, False, True, False, True, None, None]


def test_wet_flags_temperature_bounds_inclusive_and_rain_with_missing_humidity():
    flags = wet_flags([10, 25, 9.9, 25.1], [95] * 4, [0] * 4, count_rain_hours=True, **_WET)
    assert flags == [True, True, False, False]
    assert wet_flags([12], [None], [1.0], count_rain_hours=True, **_WET) == [True]
    assert wet_flags([12], [None], [0.0], count_rain_hours=True, **_WET) == [None]
    # Without rain counting, a missing rain value does not matter.
    assert wet_flags([12], [50], [None], count_rain_hours=False, **_WET) == [False]
    assert wet_flags([12], [50], [None], count_rain_hours=True, **_WET) == [None]


def test_wetness_days_runs_stay_within_each_day():
    times = [f"2025-06-01T{h:02d}:00" for h in (22, 23)] + [
        f"2025-06-02T{h:02d}:00" for h in range(4)
    ]
    flags = [True, True, True, True, None, True]
    days = wetness_days(times, flags)
    assert days == [
        {"date": "2025-06-01", "wet_hours": 2, "longest_wet_run_hours": 2},
        {"date": "2025-06-02", "wet_hours": 3, "longest_wet_run_hours": 2},
    ]
