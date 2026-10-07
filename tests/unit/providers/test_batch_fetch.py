from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Event

import httpx
import pytest

from xqatexp.artifacts.publisher import ArtifactPublishError
from xqatexp.cli import main
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.providers.tushare.batch import BatchPlan, BatchRunner, dry_run, report_periods
from xqatexp.providers.tushare.client import TushareClient, TushareSchemaError
from xqatexp.providers.tushare.collection import (
    collection_lock,
    collection_roots,
    index_collection,
    merge_financial_records,
    read_index,
    write_json,
)
from xqatexp.providers.tushare.raw import FetchRequest, RawFetchService
from xqatexp.providers.tushare.registry import get_dataset_by_api
from xqatexp.providers.tushare.runtime import ProviderConfig
from xqatexp.security import SecretValue


class MarketTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_api: str | None = None
        self.cap = 2
        self.profit = 10.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        api, params = body["api_name"], body["params"]
        self.calls.append((api, params))
        if api == self.fail_api:
            return httpx.Response(200, json={"code": 2002, "msg": "denied", "data": None})
        fields = body["fields"].split(",")
        spec = get_dataset_by_api(api)
        if api == "trade_cal":
            start = date.fromisoformat(_iso(params["start_date"]))
            end = date.fromisoformat(_iso(params["end_date"]))
            records = [
                {
                    "exchange": "SSE",
                    "cal_date": day.strftime("%Y%m%d"),
                    "is_open": int(day.weekday() < 5 and day.day != 7),
                    "pretrade_date": "20260903",
                }
                for number in range(start.toordinal(), end.toordinal() + 1)
                if (day := date.fromordinal(number))
            ]
        else:
            records = []
            universe = ("600000.SH", "600001.SH")
            if api in {"fund_basic", "fund_daily", "fund_adj"}:
                universe = ("510300.SH",)
            elif api == "index_daily":
                universe = ("000300.SH",)
            for security in universe:
                if params.get("ts_code") and params["ts_code"] != security:
                    continue
                row = dict.fromkeys(spec.fields, 1.0)
                row.update(
                    ts_code=security,
                    trade_date=params.get("trade_date", params.get("start_date")),
                    open=10.0,
                    high=11.0,
                    low=9.0,
                    close=10.0,
                    pre_close=10.0,
                    change=0.0,
                    pct_chg=0.0,
                    vol=100.0,
                    amount=100.0,
                    adj_factor=1.0,
                    up_limit=11.0,
                    down_limit=9.0,
                    turnover_rate=1.0,
                    total_mv=10000.0,
                    circ_mv=10000.0,
                    symbol=security[:6],
                    name="Example",
                    market="主板",
                    exchange="SSE",
                    curr_type="CNY",
                    list_status="L",
                    list_date="20200101",
                    delist_date=None,
                    ann_date="20260430",
                    f_ann_date="20260430",
                    end_date=params.get("period", "20260331"),
                    report_type="1",
                    comp_type="1",
                    n_income_attr_p=self.profit,
                    update_flag="0",
                    roe_yearly=10.0,
                    fund_type="ETF",
                    status="L",
                    div_proc="实施",
                    stk_div=0.0,
                    stk_bo_rate=0.0,
                    stk_co_rate=0.0,
                    cash_div=0.1,
                    cash_div_tax=0.1,
                    record_date="20260701",
                    ex_date="20260702",
                    pay_date="20260703",
                    div_listdate=None,
                    imp_ann_date="20260620",
                )
                records.append(row)
            if api == "fund_basic" and params.get("status") != "L":
                records = []
            if api == "stock_basic" and (
                params["exchange"] != "SSE" or params["list_status"] != "L"
            ):
                records = []
        offset, limit = params.get("offset", 0), min(params.get("limit", self.cap), self.cap)
        records = records[offset : offset + limit]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": fields,
                    "items": [[row.get(field) for field in fields] for row in records],
                },
            },
        )


def _iso(text: str) -> str:
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


@pytest.fixture
def market():
    transport = MarketTransport()
    http = httpx.Client(transport=httpx.MockTransport(transport))
    client = TushareClient(SecretValue("test-secret-value"), http_client=http, max_attempts=1)
    yield transport, client
    http.close()


def _plan(financial: bool = False) -> BatchPlan:
    datasets = ("stock_daily", "stock_adj_factor", "stock_daily_basic", "stock_price_limit")
    return BatchPlan(
        date(2026, 9, 4),
        date(2026, 9, 8),
        datasets + (("income", "fina_indicator") if financial else ()),
        date(2026, 3, 31) if financial else None,
        date(2026, 3, 31) if financial else None,
        refresh_days=1,
        refresh_periods=1,
    )


def test_batch_resume_and_calendar_holiday(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    report = BatchRunner(client, workers=4).run(_plan(), root)
    assert report["complete"], report
    assert len(report["tasks"]) == 9
    assert len(collection_roots(root)) == 9
    assert not any(params.get("trade_date") == "20260907" for _, params in transport.calls)
    before = len(transport.calls)
    resumed = BatchRunner(client).run(_plan(), root)
    assert resumed["complete"]
    assert {task["status"] for task in resumed["tasks"]} == {"skipped"}
    assert len(transport.calls) == before
    assert dry_run(_plan(), root)["partition_count"] == 8


def test_update_refreshes_window_and_keeps_immutable_generations(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    BatchRunner(client).run(_plan(), root)
    old = read_index(root)
    transport.calls.clear()
    report = BatchRunner(client).run(_plan(), root, mode="update")
    assert report["complete"], report
    assert all(params.get("trade_date") != "20260904" for _, params in transport.calls)
    assert len(read_index(root)["entries"]) == len(old["entries"]) + 5
    assert all((root / entry["path"]).is_dir() for entry in old["entries"])
    assert len(collection_roots(root)) == 9


def test_failed_batch_blocks_build_and_resume_fetches_only_missing(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    transport.fail_api = "adj_factor"
    report = BatchRunner(client, workers=1).run(_plan(), root)
    assert not report["complete"]
    failures = [task for task in report["tasks"] if task["status"] == "failed"]
    assert failures[0]["provider_code"] == 2002
    assert len([api for api, _ in transport.calls if api == "adj_factor"]) == 1
    with pytest.raises(ValueError, match="incomplete"):
        collection_roots(root)
    with pytest.raises(ValueError, match="incomplete"):
        index_collection(root)
    transport.fail_api = None
    transport.calls.clear()
    fixed = BatchRunner(client).run(_plan(), root)
    assert fixed["complete"], fixed
    assert set(api for api, _ in transport.calls) == {"adj_factor"}


def test_failed_update_does_not_refresh_successes_again_on_resume(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    BatchRunner(client).run(_plan(), root)
    transport.fail_api = "adj_factor"
    failed = BatchRunner(client, workers=1).run(_plan(), root, mode="update")
    assert not failed["complete"]
    transport.fail_api = None
    transport.calls.clear()
    resumed = BatchRunner(client).run(_plan(), root, mode="update")
    assert resumed["complete"]
    assert set(api for api, _ in transport.calls) == {"adj_factor"}


def test_damaged_partition_is_replaced_without_refetching_successes(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    BatchRunner(client).run(_plan(), root)
    bad = next(
        entry
        for entry in read_index(root)["entries"]
        if entry["identity"]["dataset_id"] == "stock_daily"
    )
    (root / bad["path"] / "response.jsonl.gz").write_bytes(b"damaged")
    transport.calls.clear()
    report = BatchRunner(client).run(_plan(), root)
    assert report["complete"], report
    assert set(api for api, _ in transport.calls) == {"daily"}
    collection_roots(root)


def test_artifact_published_before_index_commit_is_recovered(market, tmp_path: Path) -> None:
    transport, client = market
    root = tmp_path / "collection"
    BatchRunner(client).run(_plan(), root)
    value = read_index(root)
    value["entries"].pop()
    value["complete"] = False
    write_json(root / "collection.json", value, "raw_collection")
    transport.calls.clear()
    report = BatchRunner(client).run(_plan(), root)
    assert report["complete"], report
    assert transport.calls == []


def test_collection_rejects_tampered_paths_and_hashes(market, tmp_path: Path) -> None:
    _, client = market
    root = tmp_path / "collection"
    BatchRunner(client).run(_plan(), root)
    value = read_index(root)
    value["entries"][0]["path"] = "../outside"
    write_json(root / "collection.json", value, "raw_collection")
    with pytest.raises(ValueError, match="escapes root"):
        collection_roots(root)


def test_collection_single_writer_lock_and_move(market, tmp_path: Path) -> None:
    _, client = market
    root = tmp_path / "collection"
    BatchRunner(client).run(_plan(), root)
    with (
        collection_lock(root),
        pytest.raises(ValueError, match="DATA_COLLECTION_BUSY"),
        collection_lock(root),
    ):
        pytest.fail("second writer acquired lock")
    moved = tmp_path / "moved"
    root.rename(moved)
    assert len(collection_roots(moved / "collection.json")) == 9


def test_vip_variants_preserve_canonical_financial_facts(market, tmp_path: Path) -> None:
    _, client = market
    root = tmp_path / "collection"
    report = BatchRunner(client).run(_plan(True), root)
    assert report["complete"], report
    assert get_dataset_by_api("income_vip").dataset_id == "income"
    assert get_dataset_by_api("fina_indicator_vip").dataset_id == "fina_indicator"
    updated = BatchRunner(client).run(_plan(True), root, mode="update")
    assert updated["complete"], updated
    from xqatexp.research.tables import ResearchBuilder

    raw, _ = ResearchBuilder()._load_raw(collection_roots(root))
    assert len(raw["income"]) == len(raw["fina_indicator"]) == 2
    assert "income_vip" not in raw


def test_financial_conflict_is_not_silently_overwritten() -> None:
    rows = [
        {"ts_code": "600000.SH", "end_date": "20260331", "value": 1},
        {"ts_code": "600000.SH", "end_date": "20260331", "value": 2},
    ]
    with pytest.raises(ValueError, match="DATA_CONFLICT"):
        merge_financial_records(rows, ("ts_code", "end_date"))


def test_existing_skip_prevents_any_provider_request(market, tmp_path: Path) -> None:
    transport, client = market
    request = FetchRequest(
        "stock_daily",
        date(2026, 9, 4),
        date(2026, 9, 4),
        (),
        (),
        tmp_path / "daily",
        OverwritePolicy.ERROR,
    )
    RawFetchService(client).fetch(request)
    before = len(transport.calls)
    RawFetchService(client).fetch(replace(request, existing_policy=OverwritePolicy.SKIP))
    assert len(transport.calls) == before
    with pytest.raises(ValueError, match="does not match"):
        RawFetchService(client).fetch(
            replace(request, start_date=date(2026, 9, 3), existing_policy=OverwritePolicy.SKIP)
        )
    assert len(transport.calls) == before


def test_existing_skip_rejects_symlink_before_provider_request(market, tmp_path: Path) -> None:
    transport, client = market
    request = FetchRequest(
        "stock_daily",
        date(2026, 9, 4),
        date(2026, 9, 4),
        (),
        (),
        tmp_path / "daily",
        OverwritePolicy.ERROR,
    )
    RawFetchService(client).fetch(request)
    link = tmp_path / "linked"
    try:
        link.symlink_to(request.output_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    before = len(transport.calls)
    with pytest.raises(ArtifactPublishError, match="ARTIFACT_UNSAFE_OUTPUT_PATH"):
        RawFetchService(client).fetch(
            replace(request, output_path=link, existing_policy=OverwritePolicy.SKIP)
        )
    assert len(transport.calls) == before


@pytest.mark.parametrize("name", ["manifest.json", "request.json", "response.jsonl.gz"])
def test_collection_rejects_symlinked_raw_members(market, tmp_path: Path, name: str) -> None:
    _, client = market
    root = tmp_path / "collection"
    assert BatchRunner(client).run(_plan(), root)["complete"]
    path = root / read_index(root)["entries"][0]["path"]
    member = path / name
    copy = path / f"copy-{name}"
    member.rename(copy)
    try:
        member.symlink_to(copy.name)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="DATA_INPUT_CORRUPT"):
        collection_roots(root)


def test_offline_dry_run_requires_no_token_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    plan = tmp_path / "plan.toml"
    plan.write_text('start="2026-09-04"\nend="2026-09-08"\ndatasets=["stock_daily"]\n')
    root = tmp_path / "output"
    assert (
        main(["data", "fetch-batch", "--plan", str(plan), "--output", str(root), "--dry-run"]) == 0
    )
    assert not root.exists()
    assert report_periods(date(2025, 1, 1), date(2025, 12, 31)) == (
        date(2025, 3, 31),
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 31),
    )


@pytest.mark.parametrize(
    "settings",
    [
        "workers=9",
        "calls_per_minute=0",
        "timeout_seconds=nan",
        "workers=1.5",
        'token="secret"',
        "api_limits={daily=-1}",
    ],
)
def test_provider_configuration_rejects_invalid_values(tmp_path: Path, settings: str) -> None:
    path = tmp_path / "provider.toml"
    path.write_text(settings)
    with pytest.raises(ValueError, match="CONFIG_"):
        ProviderConfig.load(path)


def test_cancelled_batch_is_incomplete(market, tmp_path: Path) -> None:
    _, client = market
    cancelled = Event()
    cancelled.set()
    report = BatchRunner(client, cancelled=cancelled).run(_plan(), tmp_path / "collection")
    assert not report["complete"]
    assert any(task["status"] == "interrupted" for task in report["tasks"])


def test_vip_ignoring_offset_is_rejected(market) -> None:
    _, client = market
    original = client.query
    client.query = lambda api, fields, params: original(api, fields, {**params, "offset": 0})
    with pytest.raises(TushareSchemaError, match="not confirmed"):
        client.verify_pagination("income_vip", ("ts_code",), {"period": "20260331"})
