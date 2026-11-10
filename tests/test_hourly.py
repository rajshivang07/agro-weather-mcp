from datetime import date

import respx
from httpx import Response

from agro_weather_mcp import server
from agro_weather_mcp.calculations import merge_hourly


def _hours(day: str, values: list, start_hour: int = 0) -> dict:
    return {
        "time": [f"{day}T{start_hour + i:02d}:00" for i in range(len(values))],
        "temperature_2m": values,
    }


def test_merge_orders_and_trims_to_requested_days():
    late = _hours("2025-01-02", [3, 4])
    early = _hours("2025-01-01", [1, 2])
    merged = merge_hourly([late, early], ["temperature_2m"], date(2025, 1, 1), date(2025, 1, 1))
    assert len(merged["time"]) == 24
    assert merged["time"][0] == "2025-01-01T00:00"
    assert merged["temperature_2m"][:3] == [1, 2, None]


def test_merge_overlap_prefers_earlier_part_and_fills_its_gaps():
    archive = _hours("2025-01-01", [1.0, None, 3.0])
    forecast = _hours("2025-01-01", [9.0, 2.0, 9.0, 4.0])
    merged = merge_hourly([archive, forecast], ["temperature_2m"])
    assert merged["time"] == [f"2025-01-01T0{i}:00" for i in range(4)]
    assert merged["temperature_2m"] == [1.0, 2.0, 3.0, 4.0]


def test_merge_fills_gap_between_parts_with_none():
    first = _hours("2025-01-01", [1, 2])
    second = _hours("2025-01-01", [5], start_hour=4)
    merged = merge_hourly([first, second], ["temperature_2m"])
    assert merged["time"][-1] == "2025-01-01T04:00"
    assert merged["temperature_2m"] == [1, 2, None, None, 5]


def test_merge_handles_empty_parts_and_missing_variables():
    assert merge_hourly([], ["temperature_2m"]) == {"time": [], "temperature_2m": []}
    bounded = merge_hourly([{}], ["temperature_2m"], date(2025, 1, 1), date(2025, 1, 1))
    assert bounded["temperature_2m"] == [None] * 24
    # A variable absent from a part becomes None for that part's hours.
    merged = merge_hourly([{"time": ["2025-01-01T00:00"], "a": [1]}], ["a", "b"])
    assert merged["b"] == [None]


@respx.mock
async def test_hourly_series_splits_archive_and_forecast():
    today = date(2025, 6, 20)
    archive = respx.get(server.ARCHIVE_URL).mock(
        return_value=Response(
            200,
            json={"timezone": "Europe/Berlin", "hourly": _hours("2025-06-14", [10.0, 11.0])},
        )
    )
    forecast = respx.get(server.FORECAST_URL).mock(
        return_value=Response(
            200,
            json={
                "timezone": "Europe/Berlin",
                # past_days can also return hours outside the requested range.
                "hourly": {
                    "time": ["2025-06-13T23:00", "2025-06-15T00:00", "2025-06-21T00:00"],
                    "temperature_2m": [99.0, 12.0, 20.0],
                },
            },
        )
    )
    series, timezone = await server._hourly_series(
        52.5, 13.4, date(2025, 6, 14), date(2025, 6, 21), ["temperature_2m"], today=today
    )
    assert timezone == "Europe/Berlin"
    # Archive covers through today minus 5 days (the 15th); forecast starts the 16th.
    assert dict(archive.calls[0].request.url.params)["end_date"] == "2025-06-15"
    forecast_params = dict(forecast.calls[0].request.url.params)
    assert forecast_params["past_days"] == "4"
    assert forecast_params["forecast_days"] == "2"
    assert len(series["time"]) == 8 * 24
    assert series["time"][0] == "2025-06-14T00:00"
    assert series["temperature_2m"][:2] == [10.0, 11.0]
    assert series["temperature_2m"][24] == 12.0
    assert 99.0 not in series["temperature_2m"]
    assert series["temperature_2m"][7 * 24] == 20.0


@respx.mock
async def test_hourly_series_archive_only_makes_one_request():
    route = respx.get(server.ARCHIVE_URL).mock(
        return_value=Response(200, json={"timezone": "UTC", "hourly": _hours("2024-01-01", [1])})
    )
    forecast = respx.get(server.FORECAST_URL)
    series, _ = await server._hourly_series(
        0, 0, date(2024, 1, 1), date(2024, 1, 1), ["temperature_2m"], today=date(2025, 6, 20)
    )
    assert route.call_count == 1 and not forecast.called
    assert series["temperature_2m"][:2] == [1, None]
