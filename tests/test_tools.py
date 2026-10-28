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
