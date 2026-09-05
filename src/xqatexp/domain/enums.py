from enum import StrEnum


class RunMode(StrEnum):
    BACKTEST = "BACKTEST"
    DAILY_TARGET = "DAILY_TARGET"
    DAILY_ADVICE = "DAILY_ADVICE"


class RunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class AssetType(StrEnum):
    A_SHARE = "A_SHARE"
    CSI300_ETF = "CSI300_ETF"
    CASH = "CASH"
    CSI300_INDEX = "CSI300_INDEX"


class MarketRegime(StrEnum):
    STRONG = "STRONG"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"


class TargetTransition(StrEnum):
    NEW = "NEW"
    RETAIN = "RETAIN"
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    EXIT = "EXIT"
    INITIAL = "INITIAL"


class TradeAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    HOLD = "HOLD"
    UNRESOLVED = "UNRESOLVED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class FillStatus(StrEnum):
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    UNFILLED = "UNFILLED"


class UnfilledReason(StrEnum):
    SUSPENDED = "SUSPENDED"
    LIMIT_UP_LOCKED = "LIMIT_UP_LOCKED"
    LIMIT_DOWN_LOCKED = "LIMIT_DOWN_LOCKED"
    NO_EXECUTION_PRICE = "NO_EXECUTION_PRICE"
    VOLUME_CAP = "VOLUME_CAP"
    CASH_INSUFFICIENT = "CASH_INSUFFICIENT"
    HOLDING_INSUFFICIENT = "HOLDING_INSUFFICIENT"
    SELLABLE_INSUFFICIENT = "SELLABLE_INSUFFICIENT"
    LOT_TOO_SMALL = "LOT_TOO_SMALL"
    DELISTED = "DELISTED"


class PriceKind(StrEnum):
    RESEARCH = "RESEARCH"
    EXECUTION = "EXECUTION"
    VALUATION = "VALUATION"
    REFERENCE = "REFERENCE"


class OverwritePolicy(StrEnum):
    ERROR = "ERROR"
    SKIP = "SKIP"
    OVERWRITE = "OVERWRITE"


class PositionCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class AccountScope(StrEnum):
    STRATEGY_MANAGED = "STRATEGY_MANAGED"


class AccountScopeCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
