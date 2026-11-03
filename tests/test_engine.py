import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from engine import evaluate,number
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.bars=[]
        for i in range(90):
            p=10+i*.04
            self.bars.append({'date':f'2026-09-{(i%30)+1:02d}','close':p,'high':p+.12,'low':p-.12,'volume':10000})
        self.bars[-1]['date']='2026-11-03'
        self.row={'code':'600001','name':'教学样本','sector':'测试','price':self.bars[-1]['close'],'amount':9e8,'pe':20,'turnover':3,'listed_days':1000,'st':False}
        self.fin={'revenue_growth':25,'profit_growth':20,'period':'20260930'}
    def test_missing_finance_no_recommendation(self):
        self.assertIsNone(evaluate(self.row,self.bars,{},90,90))
    def test_new_listing_excluded(self):
        self.row['listed_days']=50
        self.assertIsNone(evaluate(self.row,self.bars,self.fin,90,90))
    def test_stale_price_excluded(self):
        self.row['price']=200
        self.assertIsNone(evaluate(self.row,self.bars,self.fin,90,90))
    def test_valid_plan(self):
        p=evaluate(self.row,self.bars,self.fin,90,90)
        self.assertIsNotNone(p)
        self.assertLess(p['plan']['risk_invalid'],p['plan']['entry_low'])
        self.assertLessEqual(p['plan']['budget_risk'],280)
        self.assertEqual(p['plan']['shares']%100,0)
    def test_too_small_account_excluded(self):
        self.assertIsNone(evaluate(self.row,self.bars,self.fin,90,90,account=100))
    def test_number(self):
        self.assertEqual(number('1,230.5%'),1230.5)
        self.assertIsNone(number('—'))
if __name__=='__main__':unittest.main()
