from agro_weather_mcp.calculations import (
    daily_gdd,
    frost_nights,
    summarize_gdd,
    water_balance,
    weekly_balance,
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
