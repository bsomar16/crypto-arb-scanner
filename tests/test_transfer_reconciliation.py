import unittest
from transfer_reconciliation import reconcile_transfer

class FakeAdapter:
    def __init__(self, name, withdraw=None, deposit=None):
        self.name=name; self.withdraw=withdraw or {}; self.deposit=deposit or {}
    def _signed(self, method, path, params):
        if 'withdraw/history' in path: return [self.withdraw]
        if 'deposit/hisrec' in path: return [self.deposit]
        return [self.withdraw]
    def _private(self, method, path, params=None, body=None):
        if 'withdraw/query-record' in path: return {'retCode':0,'result':{'rows':[self.withdraw]}}
        if 'deposit/query-record' in path: return {'retCode':0,'result':{'rows':[self.deposit]}}
        if 'withdrawal-history' in path: return {'code':'0','data':[self.withdraw]}
        if 'deposit-history' in path: return {'code':'0','data':[self.deposit]}
        return {'code':'0','data':[]}
    def _request(self, method, path, params=None, body=None, auth=False):
        if 'withdrawal-records' in path: return [self.withdraw]
        if 'deposit-records' in path: return [self.deposit]
        if 'withdraw/history' in path: return [self.withdraw]
        if 'deposit/hisrec' in path: return [self.deposit]
        return []

class TestTransferReconciliation(unittest.TestCase):
    def test_binance_completed_transfer(self):
        source=FakeAdapter('binance', withdraw={'id':'w1','coin':'SOL','network':'SOL','amount':'1','status':6,'txId':'tx1'})
        dest=FakeAdapter('binance', deposit={'id':'d1','coin':'SOL','network':'SOL','amount':'1','status':'SUCCESS','txId':'tx1','address':'ADDR'})
        result=reconcile_transfer(source,dest,transfer_id='w1',asset='SOL',network='SOL',expected_amount=1,expected_address='ADDR')
        self.assertEqual(result.status,'COMPLETED')
        self.assertEqual(result.destination.tx_hash,'tx1')

    def test_missing_destination_stays_confirming(self):
        source=FakeAdapter('bitget', withdraw={'orderId':'w1','coin':'SOL','chain':'SOL','size':'1','status':'success','tradeId':'tx1'})
        dest=FakeAdapter('bitget', deposit={})
        result=reconcile_transfer(source,dest,transfer_id='w1',asset='SOL',network='SOL',expected_amount=1)
        self.assertEqual(result.status,'CONFIRMING')

    def test_failed_source_blocks_sell(self):
        source=FakeAdapter('mexc', withdraw={'id':'w1','coin':'SOL','network':'SOL','amount':'1','status':8})
        dest=FakeAdapter('mexc', deposit={'txId':'tx1','coin':'SOL','network':'SOL','amount':'1','status':5})
        result=reconcile_transfer(source,dest,transfer_id='w1',asset='SOL',network='SOL',expected_amount=1)
        self.assertEqual(result.status,'FAILED')

    def test_destination_shortfall_blocks_sell(self):
        source=FakeAdapter('okx', withdraw={'wdId':'w1','ccy':'SOL','chain':'SOL','amt':'1','state':'COMPLETED','txId':'tx1'})
        dest=FakeAdapter('okx', deposit={'depId':'d1','ccy':'SOL','chain':'SOL','amt':'0.99','state':'COMPLETED','txId':'tx1'})
        result=reconcile_transfer(source,dest,transfer_id='w1',asset='SOL',network='SOL',expected_amount=1)
        self.assertEqual(result.status,'FAILED')

if __name__ == '__main__': unittest.main()
