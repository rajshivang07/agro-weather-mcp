from agro_weather_mcp.server import mcp

EXPECTED_TOOLS = {
    "geocode_location",
    "get_growing_degree_days",
    "get_frost_risk",
    "get_water_balance",
    "get_soil_conditions",
    "get_spray_window",
    "get_heat_stress",
    "get_chill_hours",
    "get_climate_anomaly",
    "get_fieldwork_window",
    "get_wetness_hours",
}
NEW_TOOLS = {
    "get_spray_window",
    "get_heat_stress",
    "get_chill_hours",
    "get_climate_anomaly",
    "get_fieldwork_window",
    "get_wetness_hours",
}


async def test_tool_names_and_schemas():
    tools = await mcp.list_tools()
    assert len(tools) == 11
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    schemas = {tool.name: tool.inputSchema for tool in tools}
    assert "lat" in schemas["get_frost_risk"]["properties"]
    assert "start_date" in schemas["get_water_balance"]["properties"]
    for name, schema in schemas.items():
        assert schema["type"] == "object", name
        assert schema["properties"], name
        # Every declared required argument must be a declared property.
        assert set(schema.get("required", [])) <= set(schema["properties"]), name
        if name != "geocode_location":
            assert {"lat", "lon"} <= set(schema["properties"]), name
            assert {"lat", "lon"} <= set(schema.get("required", [])), name


async def test_new_tool_schemas_expose_parameters_with_defaults():
    schemas = {tool.name: tool.inputSchema for tool in await mcp.list_tools()}
    spray = schemas["get_spray_window"]["properties"]
    assert spray["max_wind_kmh"]["default"] == 15.0 and spray["days"]["default"] == 3
    assert schemas["get_chill_hours"]["properties"]["method"]["default"] == "below_threshold"
    anomaly = schemas["get_climate_anomaly"]["properties"]
    assert anomaly["baseline_start_year"]["default"] == 1991
    assert anomaly["baseline_end_year"]["default"] == 2020
    assert schemas["get_heat_stress"]["properties"]["threshold_c"]["default"] == 35.0
    assert schemas["get_fieldwork_window"]["properties"]["lookback_days"]["default"] == 3
    assert schemas["get_wetness_hours"]["properties"]["count_rain_hours"]["default"] is True
    # Optional numeric limits accept null.
    assert "null" in str(schemas["get_chill_hours"]["properties"]["target_hours"])


async def test_new_tool_descriptions_follow_the_routing_format():
    descriptions = {tool.name: tool.description for tool in await mcp.list_tools()}
    for name in NEW_TOOLS:
        text = descriptions[name]
        assert "Use when:" in text and "Not for:" in text, name
        # The first line says what the tool answers, before any routing lines.
        assert text.index("Use when:") > 20, name
        assert "``get_" in text.split("Not for:")[1], name
