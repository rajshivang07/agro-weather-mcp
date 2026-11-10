import os

import pytest

from agro_weather_mcp import server

pytestmark = pytest.mark.live


def _skip():
    if os.getenv("RUN_LIVE") != "1":
        pytest.skip("set RUN_LIVE=1 to run live Open-Meteo smoke tests")


@pytest.mark.asyncio
async def test_live_geocode():
    _skip()
    assert (await server.geocode_location("Tokyo"))["candidates"]


@pytest.mark.asyncio
async def test_live_gdd():
    _skip()
    result = await server.get_growing_degree_days(35.68, 139.65, "2020-01-01", "2020-01-02")
    assert "total_gdd" in result


@pytest.mark.asyncio
async def test_live_frost():
    _skip()
    assert "nights" in await server.get_frost_risk(35.68, 139.65, 1)


@pytest.mark.asyncio
async def test_live_water():
    _skip()
    assert "total_balance_mm" in await server.get_water_balance(
        35.68, 139.65, "2020-01-01", "2020-01-02"
    )


@pytest.mark.asyncio
async def test_live_soil():
    _skip()
    assert "depths" in await server.get_soil_conditions(35.68, 139.65)


@pytest.mark.asyncio
async def test_live_spray_window():
    _skip()
    result = await server.get_spray_window(35.68, 139.65, 2)
    assert "windows" in result and "unsuitable_hours_by_limit" in result


@pytest.mark.asyncio
async def test_live_climate_anomaly():
    _skip()
    result = await server.get_climate_anomaly(35.68, 139.65, "2024-06-01", "2024-06-30")
    assert result["baseline"]["years_used"]["temperature"] >= 10
    assert "anomaly_c" in result["temperature"] and "anomaly_mm" in result["precipitation"]


@pytest.mark.asyncio
async def test_live_chill_hours():
    _skip()
    result = await server.get_chill_hours(35.68, 139.65, "2024-12-01", "2024-12-31")
    assert result["total"] >= 0 and result["monthly"]


@pytest.mark.asyncio
async def test_live_fieldwork_window():
    _skip()
    result = await server.get_fieldwork_window(35.68, 139.65, 3, soil_moisture_max=0.4)
    assert len(result["days"]) == 3


@pytest.mark.asyncio
async def test_live_heat_stress():
    _skip()
    assert "days" in await server.get_heat_stress(35.68, 139.65, 2)


@pytest.mark.asyncio
async def test_live_wetness_hours():
    _skip()
    assert "total_wet_hours" in await server.get_wetness_hours(35.68, 139.65, 2)
