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
