from abc import ABC, abstractmethod
import pandas as pd


class DataUnavailableError(Exception):
    """Raised when data cannot be fetched for the requested ticker/interval."""
    pass


class DataProvider(ABC):
    @abstractmethod
    def get_ohlcv(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        """
        Fetch OHLCV data.

        Returns a DataFrame with columns [open, high, low, close, volume]
        and a UTC-normalized DatetimeIndex. Raises DataUnavailableError on failure.
        """
        ...
