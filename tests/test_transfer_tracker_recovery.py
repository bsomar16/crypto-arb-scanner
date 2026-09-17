import tempfile
import unittest

from transfer_tracker import Transfer, TransferTracker


class TransferTrackerRecoveryTests(unittest.TestCase):
    def test_active_transfer_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = TransferTracker(tmp)
            first.create(Transfer(
                id="w1", asset="SOL", amount=1.0,
                source_exchange="binance", destination_exchange="bybit",
                network="SOL", status="SUBMITTED", txid="tx1",
                created_ms=100, updated_ms=100,
            ))
            first.transition("w1", "CONFIRMING", txid="tx1", updated_ms=200)

            restarted = TransferTracker(tmp)
            self.assertEqual(len(restarted.active()), 1)
            recovered = restarted.transfers["w1"]
            self.assertEqual(recovered.status, "CONFIRMING")
            self.assertEqual(recovered.txid, "tx1")
            self.assertEqual(recovered.amount, 1.0)

            restarted.transition("w1", "COMPLETED", txid="tx1", updated_ms=300)
            restarted_again = TransferTracker(tmp)
            self.assertEqual(restarted_again.active(), [])
            self.assertEqual(restarted_again.transfers["w1"].status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
