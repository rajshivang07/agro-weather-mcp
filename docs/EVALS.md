# Manual evaluation prompts

1. “What are the likely sowing conditions in Tokyo?” Expected tool: `geocode_location`, then `get_soil_conditions`.
2. “Calculate GDD from 2025-04-01 through 2025-04-30 in Davis with a 10 C base and 30 C cap.” Expected tool: `get_growing_degree_days`.
3. “How many GDD accumulated over the last year at this coordinate?” Expected tool: `get_growing_degree_days`.
4. “Will frost threaten this field in the next week below 0 C?” Expected tool: `get_frost_risk`.
5. “Was this field water stressed over the previous 30 days?” Expected tool: `get_water_balance`.
6. “Compare rainfall and crop reference ET0 for a season that crosses today.” Expected tool: `get_water_balance`.
7. “Show current and 48-hour soil temperature and moisture by depth.” Expected tool: `get_soil_conditions`.
8. “Find Springfield in the US and report the candidate coordinates.” Expected tool: `geocode_location`.

