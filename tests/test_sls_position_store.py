import unittest
from sls_position_store import advance_position, register_signal, active_keys


class SlsPositionStoreTests(unittest.TestCase):
    def signal(self, coin="ETH", interval="1h", ts=1000):
        return {"strategy":"SLS","signal_action":"BUY","coin":coin,"interval":interval,
                "entry":100.0,"atr":2.0,"candle_open_time":ts}

    def test_register_and_lock_same_market_timeframe(self):
        state={}
        self.assertTrue(register_signal(state,self.signal()))
        self.assertIn("ETH:1h",active_keys(state))
        self.assertFalse(register_signal(state,self.signal(ts=2000)))
        self.assertTrue(register_signal(state,self.signal(interval="4h")))

    def test_stop_precedes_target_and_closes(self):
        state={}; register_signal(state,self.signal())
        p=state["ETH:1h"]
        events=advance_position(state,"ETH","1h",[{"t":2000,"h":p["targets"][4]+1,"l":p["stop"]-0.1}])
        self.assertEqual([e["event"] for e in events],["STOP"])
        self.assertEqual(p["close_reason"],"STOP")

    def test_each_target_is_tracked_and_all_targets_close(self):
        state={}; register_signal(state,self.signal())
        p=state["ETH:1h"]; events=[]
        for n,target in enumerate(p["targets"],2):
            events.extend(advance_position(state,"ETH","1h",[{"t":n*1000,"h":target,"l":p["stop"]+1}]))
        self.assertEqual([e["event"] for e in events if e["event"].startswith("TP")],
                         ["TP1","TP2","TP3","TP4","TP5"])
        self.assertEqual(p["close_reason"],"ALL_TARGETS")

    def test_replays_only_new_candles(self):
        state={}; register_signal(state,self.signal())
        p=state["ETH:1h"]
        row={"t":2000,"h":p["targets"][0],"l":p["stop"]+1}
        self.assertEqual(len(advance_position(state,"ETH","1h",[row])),1)
        self.assertEqual(advance_position(state,"ETH","1h",[row]),[])


if __name__=="__main__":
    unittest.main()
