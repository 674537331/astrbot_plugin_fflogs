import asyncio

import httpx

from services.fashion import FashionService
from services.pvp import PvpService
from services.quest import QuestGraphService
from services.wiki import WIKI_API_URL, WikiResult, WikiService


def test_wiki_classifies_complex_entries_and_keeps_short_structured_data():
    result = WikiService._make_result(
        {
            "title": "传说之鱼",
            "extract": (
                "天气鱼。钓场：拉诺西亚外地。鱼饵链：虾肉→小鱼。"
                "前置天气：阴云。直感条件：需要直感。时间：02:00-04:00。"
            ),
            "fullurl": "https://ff14.huijiwiki.com/wiki/%E4%BC%A0%E8%AF%B4%E4%B9%8B%E9%B1%BC",
            "categories": [{"title": "Category:天气鱼"}],
        },
    )

    assert result.page_type == "天气鱼"
    assert result.details["钓场"] == "拉诺西亚外地"
    assert result.details["鱼饵链"] == "虾肉→小鱼"
    assert len(result.time_windows) >= 4
    assert result.to_dict()["type"] == "天气鱼"
    assert result.to_dict()["url"].startswith("https://")


def test_wiki_marks_missing_complex_conditions_without_guessing():
    result = WikiService._make_result(
        {
            "title": "未知限时采集物",
            "extract": "限时采集物。时间：02:00-04:00。",
            "categories": [{"title": "Category:限时采集"}],
        },
    )

    assert "资料不完整" in result.details["资料状态"]


def test_wiki_cache_is_used_when_remote_search_fails(tmp_path):
    async def scenario():
        service = WikiService({}, tmp_path)
        live = [
            WikiResult(
                title="水晶塔",
                page_type="普通",
                summary="测试摘要",
                source_url="https://example.com/wiki",
            ),
        ]

        async def successful_fetch(query, limit):
            return live

        service._fetch_remote = successful_fetch
        assert await service.search_ff14_wiki("水晶塔") == live

        async def failed_fetch(query, limit):
            raise TimeoutError

        service._fetch_remote = failed_fetch
        cached = await service.search_ff14_wiki("水晶塔")
        assert cached[0].title == "水晶塔"
        assert cached[0].cached_at

    asyncio.run(scenario())


def test_wiki_redirects_resolve_to_target_page_and_detail_failure_keeps_candidates(tmp_path):
    async def scenario():
        service = WikiService({}, tmp_path)
        calls = []

        async def fake_request(client, endpoint, params):
            calls.append(params)
            if params.get("list") == "search":
                return {"query": {"search": [{"title": "旧称"}]}}
            return {
                "query": {
                    "redirects": [{"from": "旧称", "to": "正式名称"}],
                    "pages": [
                        {
                            "title": "正式名称",
                            "extract": "这是正式页面摘要。",
                            "fullurl": "https://example.com/wiki/正式名称",
                        },
                    ],
                },
            }

        service._request_json = fake_request
        results = await service._fetch_remote("旧称", 5)
        assert results[0].title == "正式名称"
        assert calls[0]["list"] == "search"

        async def failed_request(client, endpoint, params):
            if params.get("list") == "search":
                return {"query": {"search": [{"title": "搜索候选"}]}}
            raise TimeoutError

        service._request_json = failed_request
        results = await service._fetch_remote("候选", 5)
        assert results[0].title == "搜索候选"
        assert results[0].page_type == "候选"

    asyncio.run(scenario())


def test_wiki_retries_429_and_uses_fallback_after_403(tmp_path):
    async def scenario():
        service = WikiService({}, tmp_path)

        class RateLimitedClient:
            def __init__(self):
                self.calls = 0

            async def get(self, endpoint, params=None):
                self.calls += 1
                request = httpx.Request("GET", endpoint)
                if self.calls < 3:
                    return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
                return httpx.Response(200, json={"ok": True}, request=request)

        client = RateLimitedClient()
        assert await service._request_json(client, "https://example.com", {}) == {"ok": True}
        assert client.calls == 3

        async def fallback_request(client, endpoint, params):
            if endpoint == WIKI_API_URL:
                response = httpx.Response(403, request=httpx.Request("GET", endpoint))
                response.raise_for_status()
            return {"query": {"search": []}}

        service._request_json = fallback_request
        assert await service._fetch_remote("空结果", 5) == []

    asyncio.run(scenario())


def test_wiki_format_marks_ambiguous_candidates():
    text = WikiService.format_results(
        "龙",
        [
            WikiResult("龙骑", "职业", "职业摘要", source_url="https://example.com/1"),
            WikiResult("龙诗", "副本", "副本摘要", source_url="https://example.com/2"),
        ],
    )

    assert "#1" in text
    assert "#2" in text
    assert "候选" in text


def test_wiki_format_displays_one_result_directly_even_when_it_is_a_candidate():
    text = WikiService.format_results(
        "舞台上最悲惨的演员",
        [
            WikiResult(
                "舞台上最悲惨的演员",
                "候选",
                "Wiki搜索接口暂时不可用；当前没有可用缓存。",
                source_url="https://example.com/search",
            ),
        ],
    )

    assert "有多个候选" not in text
    assert "舞台上最悲惨的演员" in text
    assert "当前没有可用缓存" in text


def test_pvp_map_image_is_cached_and_embedded(tmp_path, monkeypatch):
    async def scenario():
        service = PvpService({})
        calls = 0

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def get(self, url):
                nonlocal calls
                calls += 1
                return httpx.Response(
                    200,
                    content=b"RIFF1234WEBP",
                    headers={"content-type": "image/webp"},
                    request=httpx.Request("GET", url),
                )

        monkeypatch.setattr(
            "services.pvp.create_http_client",
            lambda config, timeout: FakeClient(),
        )
        first = await service.local_image_data("secure", tmp_path)
        second = await service.local_image_data("secure", tmp_path)

        assert first.startswith("data:image/webp;base64,")
        assert second == first
        assert calls == 1
        assert (tmp_path / "pvp_maps" / "secure.webp").read_bytes() == b"RIFF1234WEBP"

    asyncio.run(scenario())


def test_quest_graph_can_provide_progress_from_cached_csv(tmp_path):
    (tmp_path / "Quest.csv").write_text(
        "#,Name,PreviousQuest[0],Type\n"
        "1,前置任务,0,0\n"
        "2,舞台上最悲惨的演员,1,0\n",
        encoding="utf-8",
    )

    async def scenario():
        service = QuestGraphService({}, tmp_path)
        progress = await service.progress_for("舞台上最悲惨的演员")
        assert "主线约第 2/2 条" in progress

    asyncio.run(scenario())


def test_fashion_parser_requires_confirmed_theme_and_80_point_data():
    report = FashionService.parse_html(
        """
        <h1>Eastern Hunter | Week 500</h1>
        <div>Easy 80 | Head: item; Legs: item</div>
        <meta property="og:image" content="https://example.com/fashion.png">
        """,
    )

    assert report.week == "500"
    assert report.theme == "Eastern Hunter"
    assert report.confirmed is True
    assert report.image_url.endswith("fashion.png")
