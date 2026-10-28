from agro_weather_mcp.server import mcp


async def test_tool_names_and_schemas():
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "geocode_location",
        "get_growing_degree_days",
        "get_frost_risk",
        "get_water_balance",
        "get_soil_conditions",
    }
    schemas = {tool.name: tool.inputSchema for tool in tools}
    assert "lat" in schemas["get_frost_risk"]["properties"]
    assert "start_date" in schemas["get_water_balance"]["properties"]
