# Manual evaluation prompts

1. “What are the likely sowing conditions in Tokyo?” Expected tool: `geocode_location`, then `get_soil_conditions`.
2. “Calculate GDD from 2025-04-01 through 2025-04-30 in Davis with a 10 C base and 30 C cap.” Expected tool: `get_growing_degree_days`.
3. “How many GDD accumulated over the last year at this coordinate?” Expected tool: `get_growing_degree_days`.
4. “Will frost threaten this field in the next week below 0 C?” Expected tool: `get_frost_risk`.
5. “Was this field water stressed over the previous 30 days?” Expected tool: `get_water_balance`.
6. “Compare rainfall and crop reference ET0 for a season that crosses today.” Expected tool: `get_water_balance`.
7. “Show current and 48-hour soil temperature and moisture by depth.” Expected tool: `get_soil_conditions`.
8. “Find Springfield in the US and report the candidate coordinates.” Expected tool: `geocode_location`.


## New tools (0.2.0)

Thresholds are never assumed. When a prompt names a value, the model should pass it as the matching parameter, and otherwise rely on the documented neutral default.

9. “In the next 3 days, when can I spray? Wind should be 3 to 15 km/h, Delta T 2 to 8 C, and no rain for 3 hours.” Expected tool: `get_spray_window`.
10. “What is the longest suitable spray window in the next 2 days at 38.5, -121.7 if it must stay under 25 C?” Expected tool: `get_spray_window` with `days=2` and `max_temp_c=25`.
11. “How many hours above 38 C are forecast this week at 30.0, 75.0, and how many hot days in a row?” Expected tool: `get_heat_stress` with `threshold_c=38`.
12. “Will tomorrow be hotter than 33 C for more than a few hours?” Expected tool: `get_heat_stress` with `days=2` and `threshold_c=33`.
13. “How many chill hours between 0 and 7.2 C have accumulated from 2025-11-01 to 2026-01-31 at 44.0, -121.3?” Expected tool: `get_chill_hours` with the default `below_threshold` method.
14. “Calculate Utah chill units for that same winter and tell me what percent of 1200 units it reached.” Expected tool: `get_chill_hours` with `method="utah"` and `target_hours=1200`. The model should note that the Utah model performs poorly in warm winters.
15. “Was June 2025 warmer or wetter than the 1991 to 2020 normal at 48.85, 2.35?” Expected tool: `get_climate_anomaly`.
16. “Compare this past spring (March to May) with the 2001 to 2020 baseline at this location.” Expected tool: `get_climate_anomaly` with `baseline_start_year=2001`. If the end date is too recent, the model should use the earlier end date suggested by the error.
17. “Which days this week are workable for fieldwork? More than 5 mm of rain in the last 3 days makes a day poor.” Expected tool: `get_fieldwork_window` with `marginal_lookback_mm=5`.
18. “Rate the next 5 days for field access, and treat topsoil wetter than 0.35 m3/m3 as one level worse.” Expected tool: `get_fieldwork_window` with `days=5` and `soil_moisture_max=0.35`.
19. “How many hours in the next 3 days have humidity of at least 90 percent between 10 and 25 C?” Expected tool: `get_wetness_hours`. The model should state that it is a leaf wetness proxy and not a disease forecast.
20. “Count wet hours for the next 2 days using humidity only, not rain, with a 85 percent humidity limit.” Expected tool: `get_wetness_hours` with `days=2`, `min_rh_pct=85` and `count_rain_hours=false`.

## Confusable pairs (tool selection)

Each prompt could be mistaken for a sibling tool. The expected tool is the correct one.

21. Frost versus heat: “Will it get hot enough to scorch the crop this week, above 36 C?” Expected tool: `get_heat_stress`, not `get_frost_risk`.
22. Soil versus fieldwork: “Is the field dry enough to get the tractor on it on Thursday?” Expected tool: `get_fieldwork_window`, not `get_soil_conditions`.
23. Water balance versus wetness hours: “How many hours will the canopy stay damp from humidity and rain over the next 3 days?” Expected tool: `get_wetness_hours`, not `get_water_balance`.
24. GDD versus chill hours: “How much winter chilling has my orchard location had since November?” Expected tool: `get_chill_hours`, not `get_growing_degree_days`.
25. Anomaly versus water balance: “Was last summer unusually dry compared with normal years?” Expected tool: `get_climate_anomaly`, not `get_water_balance`. A request for rain minus ET0 over the same period would instead use `get_water_balance`.
26. Spray versus wetness: “When is the best time in the next 2 days to apply a spray?” Expected tool: `get_spray_window`, not `get_wetness_hours`.
