import unittest
from sls_position import SlsPositionManager


class SlsPositionTests(unittest.TestCase):
    def test_stop_has_priority_over_targets(self):
        m=SlsPositionManager(); self.assertTrue(m.open(100, 2, 10, 120))
        p=m.position
        events=m.on_confirmed_bar(p.targets[-1]+1, p.stop-0.01, 11)
        self.assertEqual([x["event"] for x in events], ["STOP"])
        self.assertEqual(p.close_reason, "STOP")

    def test_targets_are_independent_and_close_after_all_five(self):
        m=SlsPositionManager(); m.open(100, 2, 0, 120)
        p=m.position
        all_events=[]
        for i,t in enumerate(p.targets,1):
            all_events += m.on_confirmed_bar(t, p.stop+1, i)
        self.assertEqual([x["event"] for x in all_events if x["event"].startswith("TP")],
                         ["TP1","TP2","TP3","TP4","TP5"])
        self.assertEqual(p.close_reason, "ALL_TARGETS")

    def test_position_locks_new_entry_until_closed(self):
        m=SlsPositionManager(); self.assertTrue(m.open(100,2,0,2))
        self.assertFalse(m.open(101,2,1,2))
        p=m.position
        events=m.on_confirmed_bar(101,p.stop+1,3)
        self.assertEqual(events[-1]["reason"],"TIMEOUT")
        self.assertFalse(m.in_position)
        self.assertTrue(m.open(101,2,4,2))

    def test_opening_bar_does_not_exit(self):
        m=SlsPositionManager(); m.open(100,2,5,120)
        self.assertEqual(m.on_confirmed_bar(200,1,5), [])


if __name__ == "__main__":
    unittest.main()
