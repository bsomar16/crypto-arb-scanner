import datetime
import unittest

import sls_backtest


class SlsMonthlyAsOfTests(unittest.TestCase):
    def test_monthly_candle_uses_actual_calendar_month_length(self):
        jan_2024 = int(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        feb_2024 = int(datetime.datetime(2024, 2, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        mar_2024 = int(datetime.datetime(2024, 3, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        self.assertEqual(sls_backtest._candle_close_ms(jan_2024, "1M"), feb_2024)
        self.assertEqual(sls_backtest._candle_close_ms(feb_2024, "1M"), mar_2024)

    def test_monthly_candle_not_available_before_calendar_close(self):
        start = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
        rows = []
        for i in range(240):
            month_index = start.year * 12 + start.month - 1 + i
            year, month_zero = divmod(month_index, 12)
            month = month_zero + 1
            opened = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
            rows.append({"t": int(opened.timestamp() * 1000), "o": 1, "h": 1,
                         "l": 1, "c": 1, "v": 1, "q": 1})
        data = sls_backtest._normalise_dataset({"candles": {"ETH": {"1M": rows}}})
        feb_2020 = int(datetime.datetime(2020, 2, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        fetch = sls_backtest._provider(data, feb_2020 - 1)
        self.assertEqual(fetch("ETH", "1M")[-1]["t"], int(datetime.datetime(2019, 12, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000))


if __name__ == "__main__":
    unittest.main()