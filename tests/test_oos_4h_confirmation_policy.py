import unittest
from oos_4h_confirmation_policy import BODY_THRESHOLDS, WINDOWS, POLICIES

class ConfirmationPolicyStudyTests(unittest.TestCase):
    def test_five_windows_are_fixed(self):
        self.assertEqual(len(WINDOWS),5)
        self.assertEqual(WINDOWS[0],("oos_1",500,1000))
        self.assertEqual(WINDOWS[-1],("oos_5",2500,3000))
        self.assertTrue(all(end-start==500 for _,start,end in WINDOWS))
    def test_thresholds_and_policies_are_predeclared(self):
        self.assertEqual(BODY_THRESHOLDS,(0.25,0.30,0.35,0.40,0.45,0.55))
        self.assertEqual(POLICIES,("TP1","TP3"))
if __name__=="__main__":
    unittest.main()
