from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dataset_id: str
    api_name: str
    fields: tuple[str, ...]
    business_key: tuple[str, ...]
    fixed_params: Mapping[str, str] = field(default_factory=dict)
    volume_unit: str | None = None
    amount_unit: str | None = None
    empty_allowed: bool = False
    points: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixed_params", MappingProxyType(dict(self.fixed_params)))


def _spec(
    dataset_id: str,
    api_name: str,
    fields: str,
    key: str,
    *,
    fixed: Mapping[str, str] | None = None,
    volume_unit: str | None = None,
    amount_unit: str | None = None,
    empty_allowed: bool = False,
    points: int | None = None,
) -> DatasetSpec:
    return DatasetSpec(
        dataset_id,
        api_name,
        tuple(fields.split(",")),
        tuple(key.split(",")),
        fixed or {},
        volume_unit,
        amount_unit,
        empty_allowed,
        points,
    )


_DATASETS = {
    spec.dataset_id: spec
    for spec in (
        _spec(
            "stock_basic",
            "stock_basic",
            "ts_code,symbol,name,market,exchange,curr_type,list_status,list_date,delist_date",
            "ts_code",
            points=2000,
        ),
        _spec(
            "trade_calendar",
            "trade_cal",
            "exchange,cal_date,is_open,pretrade_date",
            "exchange,cal_date",
            fixed={"exchange": "SSE"},
            points=2000,
        ),
        _spec(
            "stock_daily",
            "daily",
            "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
            "ts_code,trade_date",
            volume_unit="hand",
            amount_unit="thousand_cny",
        ),
        _spec(
            "stock_adj_factor",
            "adj_factor",
            "ts_code,trade_date,adj_factor",
            "ts_code,trade_date",
            points=2000,
        ),
        _spec(
            "stock_daily_basic",
            "daily_basic",
            "ts_code,trade_date,close,turnover_rate,total_mv,circ_mv",
            "ts_code,trade_date",
            points=2000,
        ),
        _spec(
            "stock_suspend",
            "suspend_d",
            "ts_code,trade_date,suspend_timing,suspend_type",
            "ts_code,trade_date,suspend_type",
            fixed={"suspend_type": "S"},
            empty_allowed=True,
            points=2000,
        ),
        _spec(
            "stock_price_limit",
            "stk_limit",
            "ts_code,trade_date,pre_close,up_limit,down_limit",
            "ts_code,trade_date",
            points=2000,
        ),
        _spec(
            "stock_st_status",
            "stock_st",
            "ts_code,name,trade_date,type,type_name",
            "ts_code,trade_date",
            empty_allowed=True,
            points=3000,
        ),
        _spec(
            "fund_basic",
            "fund_basic",
            "ts_code,name,fund_type,list_date,delist_date,status,market",
            "ts_code",
            fixed={"market": "E"},
            points=2000,
        ),
        _spec(
            "fund_daily",
            "fund_daily",
            "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
            "ts_code,trade_date",
            volume_unit="hand",
            amount_unit="thousand_cny",
            points=5000,
        ),
        _spec(
            "fund_adj_factor",
            "fund_adj",
            "ts_code,trade_date,adj_factor",
            "ts_code,trade_date",
            points=2000,
        ),
        _spec(
            "index_daily",
            "index_daily",
            "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
            "ts_code,trade_date",
            fixed={"ts_code": "000300.SH"},
            volume_unit="hand",
            amount_unit="thousand_cny",
            points=2000,
        ),
        _spec(
            "income",
            "income",
            "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_income_attr_p,update_flag",
            "ts_code,end_date,report_type,ann_date,f_ann_date,update_flag",
            empty_allowed=True,
            points=2000,
        ),
        _spec(
            "fina_indicator",
            "fina_indicator",
            "ts_code,ann_date,end_date,roe,roe_waa,roe_yearly,profit_dedt,update_flag",
            "ts_code,end_date,ann_date,update_flag",
            empty_allowed=True,
            points=2000,
        ),
        _spec(
            "dividend",
            "dividend",
            "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate,imp_ann_date",
            "ts_code,end_date,ann_date,div_proc,record_date,ex_date",
            empty_allowed=True,
            points=2000,
        ),
    )
}


def dataset_ids() -> tuple[str, ...]:
    return tuple(sorted(_DATASETS))


def all_datasets() -> tuple[DatasetSpec, ...]:
    return tuple(_DATASETS[dataset_id] for dataset_id in dataset_ids())


def get_dataset(dataset_id: str) -> DatasetSpec:
    try:
        return _DATASETS[dataset_id]
    except KeyError as error:
        raise ValueError(f"CONFIG_VALUE_INVALID: unknown dataset_id {dataset_id}") from error


def get_dataset_by_api(api_name: str) -> DatasetSpec:
    matches = tuple(spec for spec in _DATASETS.values() if spec.api_name == api_name)
    if len(matches) != 1:
        raise ValueError(f"DATA_PROVIDER_SCHEMA_MISMATCH: unknown api_name {api_name}")
    return matches[0]
