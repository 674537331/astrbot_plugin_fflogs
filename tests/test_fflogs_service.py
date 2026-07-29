from services.fflogs import (
    SAVAGE_DIFFICULTY_ID,
    FFLogsService,
    RankingResult,
)


def _ranking(
    encounter_id: int,
    percent: float,
    spec: str = "Paladin",
    difficulty: int | None = None,
):
    result = {
        "encounter": {"id": encounter_id},
        "rankPercent": percent,
        "spec": spec,
    }
    if difficulty is not None:
        result["difficulty"] = difficulty
    return result


def test_query_contains_current_fflogs_zones():
    query = FFLogsService.build_zone_rankings_query()

    assert "zoneID: 76" in query
    assert "zoneID: 65" in query
    assert "zoneID: 59" in query
    assert "zoneID: 73, difficulty: 101" in query


def test_extract_results_supports_7x_ultimates_and_savage():
    character_data = {
        "s73": {
            "difficulty": SAVAGE_DIFFICULTY_ID,
            "rankings": [_ranking(105, 96.4, "Pictomancer")],
        },
        "u_fru": {
            "rankings": [_ranking(1079, 88.2, "Paladin")],
        },
        "u_dmu": {
            "rankings": [_ranking(1085, 77.1, "WhiteMage")],
        },
    }

    results = FFLogsService.extract_results(character_data)

    assert results["M12S"] == RankingResult(96.4, "画家")
    assert results["绝伊甸"] == RankingResult(88.2, "骑士")
    assert results["绝妖星乱舞"] == RankingResult(77.1, "白魔")


def test_extract_results_rejects_normal_difficulty():
    character_data = {
        "s73": {
            "difficulty": 100,
            "rankings": [_ranking(105, 99.9)],
        },
    }

    assert FFLogsService.extract_results(character_data) == {}


def test_extract_results_keeps_best_percentile():
    character_data = {
        "u_7x_legacy": {
            "rankings": [
                _ranking(1077, 50.0, "Paladin"),
                _ranking(1077, 80.0, "Warrior"),
            ],
        },
    }

    assert FFLogsService.extract_results(character_data)["绝欧"] == RankingResult(
        80.0,
        "战士",
    )
