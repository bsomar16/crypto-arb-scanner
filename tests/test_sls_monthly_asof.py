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
        jan_2024 = int(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        feb_2024 = int(datetime.datetime(2024, 2, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        dataset = {"candles": {"ETH": {"1M": [
            {"t": jan_2024, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "q": 1}
            for _ in range(200)
        ]}}}
        data = sls_backtest._normalise_dataset(dataset)
        self.assertEqual(sls_backtest._provider(data, feb_2024 - 1)("ETH", "1M"), None)


if __name__ == "__main__":
    unittest.main()