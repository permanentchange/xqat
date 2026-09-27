from __future__ import annotations

from datetime import date
from typing import Protocol


class TradingCalendar(Protocol):
    def next_trading_day(self, after: date) -> date: ...


class DecisionSchedule(Protocol):
    def is_decision_day(self, calendar: TradingCalendar, current_day: date) -> bool: ...


class DailyCloseSchedule:
    def is_decision_day(self, calendar: TradingCalendar, current_day: date) -> bool:
        del calendar, current_day
        return True


class WeeklyLastTradingDayCloseSchedule:
    def is_decision_day(self, calendar: TradingCalendar, current_day: date) -> bool:
        try:
            following = calendar.next_trading_day(current_day)
        except (IndexError, ValueError):
            return current_day.weekday() == 4
        current_iso = current_day.isocalendar()
        following_iso = following.isocalendar()
        return (current_iso.year, current_iso.week) != (following_iso.year, following_iso.week)
