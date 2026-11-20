"""The dashboards: TODAY, VALUE MAP, ENTRY LADDER, INCOME and CALL BOOK."""

from obb_terminal.ui.desk.call_book import CallBookPage
from obb_terminal.ui.desk.feed import DeskFeed
from obb_terminal.ui.desk.income import IncomePage
from obb_terminal.ui.desk.ladder import EntryLadderPage
from obb_terminal.ui.desk.today import TodayPage
from obb_terminal.ui.desk.value_map import ValueMapPage

__all__ = ["CallBookPage", "DeskFeed", "EntryLadderPage", "IncomePage", "TodayPage", "ValueMapPage"]
