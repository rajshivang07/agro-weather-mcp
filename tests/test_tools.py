from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from agro_weather_mcp import server


@respx.mock
async def test_geocode_tool():
    route = respx.get(server.GEOCODING_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "name": "Davis",
                        "admin1": "California",
                        "country": "United States",
                        "latitude": 38.5,
                        "longitude": -121.7,
                        "elevation": 16,
                        "timezone": "America/Los_Angeles",
                    }
                ]
            },
        )
    )
    result = await server.geocode_location("Davis", "US")
    assert route.called and result["candidates"][0]["name"] == "Davis"


@respx.mock
async def test_gdd_tool_archive_fixture():
    respx.get(server.ARCHIVE_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "UTC",
                "daily": {
                    "time": ["2020-01-01"],
                    "temperature_2m_max": [20],
                    "temperature_2m_min": [10],
                },
            },
        )
    )
    result = await server.get_growing_degree_days(0, 0, "2020-01-01", "2020-01-01")
    assert result["total_gdd"] == 5.0


@respx.mock
async def test_frost_tool():
    respx.get(server.FORECAST_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "UTC",
                "hourly": {
                    "time": ["2025-01-01T20:00", "2025-01-01T22:00"],
                    "temperature_2m": [1, -2],
                },
            },
        )
    )
    result = await server.get_frost_risk(0, 0, 1)
    assert result["nights"][0]["hours_below_threshold"] == 1


@respx.mock
async def test_water_balance_note_scales_with_range():
    respx.get(server.ARCHIVE_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "UTC",
                "daily": {
                    "time": ["2020-01-01", "2020-01-02"],
                    "precipitation_sum": [6, 6],
                    "et0_fao_evapotranspiration": [2, 2],
                },
            },
        )
    )
    result = await server.get_water_balance(0, 0, "2020-01-01", "2020-01-02")
    assert result["total_balance_mm"] == 8.0
    assert result["dryness_threshold_mm"] == 5.0
    assert result["dryness_note"] == "Likely water surplus"


def _hourly_fixture(day_count: int, start: str = "2025-06-01", **series) -> dict:
    """Build an Open-Meteo style hourly block. Series values are callables of the hour index."""
    from datetime import date, timedelta

    first = date.fromisoformat(start)
    times = [f"{first + timedelta(days=i // 24)}T{i % 24:02d}:00" for i in range(day_count * 24)]
    return {"time": times, **{k: [f(i) for i in range(len(times))] for k, f in series.items()}}


def _pin_clock(monkeypatch, iso: str) -> None:
    monkeypatch.setattr(server, "_utcnow", lambda: datetime.fromisoformat(iso).replace(tzinfo=UTC))


@respx.mock
async def test_spray_window_tool(monkeypatch):
    _pin_clock(monkeypatch, "2025-06-01T06:30:00")
    route = respx.get(server.FORECAST_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "Europe/Berlin",
                "utc_offset_seconds": 0,
                "hourly": _hourly_fixture(
                    2,
                    temperature_2m=lambda i: 20,
                    relative_humidity_2m=lambda i: 50,
                    wind_speed_10m=lambda i: 25 if i == 12 else 10,
                    precipitation=lambda i: 0.4 if i == 15 else 0.0,
                ),
            },
        )
    )
    result = await server.get_spray_window(52.52, 13.41, 1, rain_free_hours=3)
    params = dict(route.calls[0].request.url.params)
    assert params["forecast_days"] == "2" and params["wind_speed_unit"] == "kmh"
    assert result["evaluated_hours"] == 24 and result["suitable_hours"] == 21
    assert [w["hours"] for w in result["windows"]] == [6, 15]
    assert result["windows"][0] == {
        "start": "2025-06-01T06:00",
        "end": "2025-06-01T12:00",
        "hours": 6,
    }
    assert result["longest_window"]["hours"] == 15
    assert result["unsuitable_hours_by_limit"] == {"rain_expected": 3, "wind_too_high": 1}
    assert result["data_source"] == server.SOURCE and result["timezone"] == "Europe/Berlin"
    assert "hourly" not in result


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"days": 0}, "days must be between 1 and 7"),
        ({"days": 8}, "days must be between 1 and 7"),
        ({"min_wind_kmh": 15.0}, "min_wind_kmh"),
        ({"min_delta_t_c": 8.0}, "min_delta_t_c"),
        ({"rain_free_hours": 25}, "rain_free_hours"),
        ({"lat": 91}, "lat must be"),
    ],
)
async def test_spray_window_validation(kwargs, message):
    args = {"lat": 0, "lon": 0, **kwargs}
    with pytest.raises(ValueError, match=message):
        await server.get_spray_window(**args)


@respx.mock
async def test_heat_stress_tool_with_hourly_detail():
    respx.get(server.FORECAST_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "Asia/Kolkata",
                "hourly": _hourly_fixture(
                    2,
                    start="2025-07-01",
                    temperature_2m=lambda i: (
                        38.0 if i == 13 else 36.0 if 12 <= i <= 15 else 35.0 if i == 30 else 34.0
                    ),
                ),
            },
        )
    )
    result = await server.get_heat_stress(30, 75, 2, 35)
    assert result["days"] == [
        {"date": "2025-07-01", "max_temp_c": 38.0, "hours_above_threshold": 4},
        {"date": "2025-07-02", "max_temp_c": 35.0, "hours_above_threshold": 0},
    ]
    assert result["total_hours_above_threshold"] == 4
    assert result["longest_streak_days"] == 1
    assert len(result["hourly"]) == 48 and result["threshold_c"] == 35


@respx.mock
async def test_heat_stress_tool_is_daily_only_beyond_three_days():
    respx.get(server.FORECAST_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "UTC",
                "hourly": _hourly_fixture(4, start="2025-07-01", temperature_2m=lambda i: 36.0),
            },
        )
    )
    result = await server.get_heat_stress(0, 0, 4)
    assert "hourly" not in result
    assert result["longest_streak_days"] == 4 and result["total_hours_above_threshold"] == 96


@pytest.mark.parametrize("days", [0, 17])
async def test_heat_stress_validation(days):
    with pytest.raises(ValueError, match="days must be between 1 and 16"):
        await server.get_heat_stress(0, 0, days)


@respx.mock
async def test_chill_hours_tool_both_methods_and_target():
    day1 = [5.0] * 12 + [20.0] * 12
    day2 = [2.0] * 24
    respx.get(server.ARCHIVE_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "America/Los_Angeles",
                "hourly": _hourly_fixture(
                    2,
                    start="2024-01-31",
                    temperature_2m=lambda i: (day1 + day2)[i],
                ),
            },
        )
    )
    below = await server.get_chill_hours(38.5, -121.7, "2024-01-31", "2024-02-01", target_hours=72)
    assert below["total"] == 36.0 and below["percent_of_target"] == 50.0
    assert below["monthly"] == [
        {"month": "2024-01", "value": 12.0},
        {"month": "2024-02", "value": 24.0},
    ]
    assert below["lower_bound_c"] == 0.0 and below["units"]["chill"] == "hours"
    utah = await server.get_chill_hours(38.5, -121.7, "2024-01-31", "2024-02-01", method="utah")
    # Day 1: 12 * 1.0 - 12 * 1.0 = 0. Day 2: 24 * 0.5 = 12.
    assert utah["total"] == 12.0
    assert utah["monthly"][0]["value"] == 0.0 and utah["monthly"][1]["value"] == 12.0
    assert "threshold_c" not in utah


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"method": "dynamic"}, "method must be"),
        ({"target_hours": 0}, "target_hours"),
        ({"threshold_c": 5.0, "lower_bound_c": 6.0}, "lower_bound_c"),
        ({"start_date": "2024-01-01", "end_date": "2025-06-01"}, "366 days"),
        ({"start_date": "bad"}, "start_date must be an ISO date"),
    ],
)
async def test_chill_hours_validation(kwargs, message):
    args = {"lat": 0, "lon": 0, "start_date": "2024-01-01", "end_date": "2024-01-02", **kwargs}
    with pytest.raises(ValueError, match=message):
        await server.get_chill_hours(**args)


def _anomaly_archive(calls: list[dict]):
    """Archive fixture: baseline year Y has temp (Y - 2001 + 10) and rain (Y - 2000) mm/day."""
    from datetime import date, timedelta

    def respond(request):
        params = dict(request.url.params)
        calls.append(params)
        first, last = (
            date.fromisoformat(params["start_date"]),
            date.fromisoformat(params["end_date"]),
        )
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        if first.year == 2001 and last.year == 2010:
            temp = [float(d.year - 2001 + 10) for d in days]
            rain = [float(d.year - 2000) for d in days]
        else:
            temp, rain = [17.0] * len(days), [5.0] * len(days)
        return Response(
            200,
            json={
                "timezone": "Europe/Paris",
                "daily": {
                    "time": [str(d) for d in days],
                    "temperature_2m_mean": temp,
                    "precipitation_sum": rain,
                },
            },
        )

    return respond


@respx.mock
async def test_climate_anomaly_tool_and_baseline_cache():
    calls: list[dict] = []
    respx.get(server.ARCHIVE_URL).mock(side_effect=_anomaly_archive(calls))
    result = await server.get_climate_anomaly(
        48.86, 2.35, "2024-06-01", "2024-06-03", baseline_start_year=2001, baseline_end_year=2010
    )
    # Baseline is 10..19 (mean 14.5); target 17 is above 7 years and ties one: (7 + 0.5) / 10.
    temperature = result["temperature"]
    assert temperature["target"] == 17.0 and temperature["baseline_mean"] == 14.5
    assert temperature["baseline_std"] == 3.03 and temperature["anomaly_c"] == 2.5
    assert temperature["percentile_rank"] == 75.0
    # Rain baseline totals are 3, 6, ..., 30 (mean 16.5); target is 15 and ties one year.
    rain = result["precipitation"]
    assert rain["target"] == 15.0 and rain["anomaly_mm"] == -1.5
    assert rain["percent_of_normal"] == 90.9 and rain["percentile_rank"] == 45.0
    assert result["baseline"]["years_used"] == {"temperature": 10, "precipitation": 10}
    assert result["baseline"]["coordinates_used"] == {"latitude": 48.9, "longitude": 2.4}
    # Both the baseline and the target use the rounded coordinates.
    assert {c["latitude"] for c in calls} == {"48.9"} and {c["longitude"] for c in calls} == {"2.4"}
    baseline_calls = [c for c in calls if c["start_date"] == "2001-01-01"]
    assert len(baseline_calls) == 1
    # A different window at a nearby point reuses the cached baseline.
    again = await server.get_climate_anomaly(
        48.88, 2.36, "2024-07-01", "2024-07-03", baseline_start_year=2001, baseline_end_year=2010
    )
    assert again["baseline"]["years_used"]["temperature"] == 10
    assert len([c for c in calls if c["start_date"] == "2001-01-01"]) == 1
    assert len(calls) == 3
    # A point that rounds to a different 0.1 degree cell fetches its own baseline.
    await server.get_climate_anomaly(
        48.84, 2.34, "2024-07-01", "2024-07-03", baseline_start_year=2001, baseline_end_year=2010
    )
    assert len([c for c in calls if c["start_date"] == "2001-01-01"]) == 2


@respx.mock
async def test_climate_anomaly_rejects_recent_end_date_with_suggestion():
    from datetime import date, timedelta

    route = respx.get(server.ARCHIVE_URL)
    today = date.today()
    with pytest.raises(ValueError, match="use end_date") as error:
        await server.get_climate_anomaly(0, 0, str(today - timedelta(days=10)), str(today))
    assert str(today - timedelta(days=server.ARCHIVE_LAG_DAYS)) in str(error.value)
    assert not route.called


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"baseline_start_year": 2015, "baseline_end_year": 2020}, "at least 10 years"),
        ({"baseline_start_year": 1900}, "baseline_start_year"),
        ({"baseline_end_year": 2999}, "baseline_end_year"),
        ({"start_date": "2024-06-03", "end_date": "2024-06-01"}, "on or before"),
        ({"start_date": "2023-01-01", "end_date": "2024-06-01"}, "366 days"),
    ],
)
async def test_climate_anomaly_validation(kwargs, message):
    args = {"lat": 0, "lon": 0, "start_date": "2024-06-01", "end_date": "2024-06-03", **kwargs}
    with pytest.raises(ValueError, match=message):
        await server.get_climate_anomaly(**args)


@respx.mock
async def test_climate_anomaly_errors_when_target_data_is_incomplete():
    def respond(request):
        params = dict(request.url.params)
        n = 3 if params["start_date"] == "2024-06-01" else 3650
        return Response(
            200,
            json={
                "daily": {
                    "time": ["2024-06-01", "2024-06-02", "2024-06-03"][:n],
                    "temperature_2m_mean": [10.0, None, 10.0][:n],
                    "precipitation_sum": [1.0, 1.0, 1.0][:n],
                }
            },
        )

    respx.get(server.ARCHIVE_URL).mock(side_effect=respond)
    with pytest.raises(ValueError, match="incomplete"):
        await server.get_climate_anomaly(0, 0, "2024-06-01", "2024-06-03")


def _fieldwork_fixture(with_soil: bool) -> dict:
    # Two past days plus six forecast days, 2025-06-01 to 2025-06-08.
    precipitation = [0, 0, 0.5, 6, 0, 0, 0, 0]
    body: dict = {
        "timezone": "Europe/London",
        "daily": {
            "time": [f"2025-06-{i + 1:02d}" for i in range(8)],
            "precipitation_sum": precipitation,
        },
    }
    if with_soil:
        # Day index 6 (2025-06-07) is wet at 0.45 m3/m3, every other day is 0.30.
        body["hourly"] = _hourly_fixture(
            8, soil_moisture_0_to_7cm=lambda i: 0.45 if i // 24 == 6 else 0.30
        )
    return body


@respx.mock
async def test_fieldwork_window_tool_rain_only():
    route = respx.get(server.FORECAST_URL).mock(
        return_value=Response(200, json=_fieldwork_fixture(False))
    )
    result = await server.get_fieldwork_window(51.5, -0.1, 6, lookback_days=2)
    params = dict(route.calls[0].request.url.params)
    assert params["past_days"] == "2" and params["forecast_days"] == "6"
    assert "hourly" not in params
    assert [d["rating"] for d in result["days"]] == [
        "good",
        "poor",
        "marginal",
        "marginal",
        "good",
        "good",
    ]
    assert result["longest_good_run"] == {"start": "2025-06-07", "end": "2025-06-08", "days": 2}
    assert result["resolved_date_range"] == {"start": "2025-06-03", "end": "2025-06-08"}
    assert result["good_days"] == 3 and result["days"][1]["reasons"]


@respx.mock
async def test_fieldwork_window_tool_soil_moisture_downgrade():
    route = respx.get(server.FORECAST_URL).mock(
        return_value=Response(200, json=_fieldwork_fixture(True))
    )
    result = await server.get_fieldwork_window(
        51.5, -0.1, 6, lookback_days=2, soil_moisture_max=0.4
    )
    assert dict(route.calls[0].request.url.params)["hourly"] == "soil_moisture_0_to_7cm"
    ratings = {d["date"]: d["rating"] for d in result["days"]}
    assert ratings["2025-06-07"] == "marginal" and ratings["2025-06-08"] == "good"
    assert ratings["2025-06-03"] == "good"
    wet_day = next(d for d in result["days"] if d["date"] == "2025-06-07")
    assert wet_day["topsoil_moisture_m3_m3"] == 0.45
    assert any("downgraded from good" in r for r in wet_day["reasons"])
    assert result["longest_good_run"]["days"] == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"days": 15}, "days must be between 1 and 14"),
        ({"days": 0}, "days must be between 1 and 14"),
        ({"lookback_days": 0}, "lookback_days"),
        ({"good_day_mm": 6.0}, "good limits must not exceed"),
        ({"good_lookback_mm": 20.0}, "good limits must not exceed"),
        ({"good_day_mm": -1.0}, "must not be negative"),
        ({"soil_moisture_max": 0}, "soil_moisture_max"),
        ({"soil_moisture_max": 40}, "soil_moisture_max"),
    ],
)
async def test_fieldwork_window_validation(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await server.get_fieldwork_window(0, 0, **kwargs)


def _wetness_fixture() -> dict:
    def humidity(i: int) -> float:
        # Day 1 hours 0-5 are humid, day 2 hours 22 and 23 are humid.
        return 95.0 if i < 6 or i >= 46 else 50.0

    return {
        "timezone": "Europe/Rome",
        "hourly": _hourly_fixture(
            2,
            relative_humidity_2m=humidity,
            temperature_2m=lambda i: 15.0,
            precipitation=lambda i: 0.2 if i in (7, 8) else 0.0,
        ),
    }


@respx.mock
async def test_wetness_hours_tool_with_rain_counted():
    respx.get(server.FORECAST_URL).mock(return_value=Response(200, json=_wetness_fixture()))
    result = await server.get_wetness_hours(45.0, 9.2, 2)
    # Day 1: 6 humid hours plus 2 rainy hours. Day 2: 2 humid hours.
    assert result["days"] == [
        {"date": "2025-06-01", "wet_hours": 8, "longest_wet_run_hours": 6},
        {"date": "2025-06-02", "wet_hours": 2, "longest_wet_run_hours": 2},
    ]
    assert result["total_wet_hours"] == 10 and result["longest_wet_run_hours"] == 6
    assert len(result["wet_hour_times"]) == 10 and result["hours_undetermined"] == 0
    assert result["wet_hour_times"][-1] == "2025-06-02T23:00"


@respx.mock
async def test_wetness_hours_tool_without_rain_and_daily_only_over_three_days():
    respx.get(server.FORECAST_URL).mock(return_value=Response(200, json=_wetness_fixture()))
    result = await server.get_wetness_hours(45.0, 9.2, 2, count_rain_hours=False)
    assert result["total_wet_hours"] == 8 and result["criteria"]["count_rain_hours"] is False

    respx.get(server.FORECAST_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "UTC",
                "hourly": _hourly_fixture(
                    4,
                    relative_humidity_2m=lambda i: 95.0,
                    temperature_2m=lambda i: None if i == 10 else 15.0,
                    precipitation=lambda i: 0.0,
                ),
            },
        )
    )
    long = await server.get_wetness_hours(0, 0, 4)
    assert "wet_hour_times" not in long
    # One missing temperature hour is undetermined and splits the first day's run.
    assert long["hours_undetermined"] == 1 and long["total_wet_hours"] == 95
    assert long["days"][0]["longest_wet_run_hours"] == 13
    assert long["longest_wet_run_hours"] == 85


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"days": 8}, "days must be between 1 and 7"),
        ({"min_rh_pct": 101}, "min_rh_pct"),
        ({"min_temp_c": 30.0}, "min_temp_c"),
    ],
)
async def test_wetness_hours_validation(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await server.get_wetness_hours(0, 0, **kwargs)
