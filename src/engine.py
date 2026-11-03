"""五步选股规则。仅研究辅助，非投资建议；数据缺失时禁止编造候选。"""
from __future__ import annotations
import math
from datetime import date, datetime


def number(x):
    try:
        if x is None or str(x).strip() in ('', '-', '—', 'nan', 'None'):
            return None
        value=float(str(x).replace(',','').replace('%',''))
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):
        return None


def fround(value, digits=2):
    return round(float(value),digits)


def evaluate(row, bars, fundamentals, market_score, sector_score, account=28000, risk_pct=1.0, max_weight=.15):
    """Requires at least 65 true daily bars and a disclosed financial record."""
    if len(bars)<65 or not fundamentals:
        return None
    closes=[number(b['close']) for b in bars]
    highs=[number(b['high']) for b in bars]
    lows=[number(b['low']) for b in bars]
    vols=[number(b['volume']) for b in bars]
    if any(x is None or x<=0 for x in closes[-65:]+highs[-20:]+lows[-20:]+vols[-21:]):
        return None
    price=number(row.get('price'))
    amount=number(row.get('amount'))
    pe=number(row.get('pe'))
    rev=number(fundamentals.get('revenue_growth'))
    profit=number(fundamentals.get('profit_growth'))
    if price is None or price<=0 or amount is None or amount<1e8 or rev is None or profit is None or pe is None:
        return None
    if abs(price/closes[-1]-1)>.03: # mismatch of spot vs historical close is NOT acceptable
        return None
    ma20=sum(closes[-20:])/20
    ma60=sum(closes[-60:])/60
    prior_high=max(highs[-21:-1])
    low20=min(lows[-20:])
    avgvol=sum(vols[-21:-1])/20
    volratio=vols[-1]/avgvol if avgvol else 0
    ret20=price/closes[-21]-1
    sector_score=max(0,min(100,sector_score))
    market_score=max(0,min(100,market_score))
    s_market=round(15*market_score/100,1)
    s_sector=round(20*sector_score/100,1)
    s_fund=(10 if rev>=15 else 7 if rev>0 else 0)+(10 if profit>=15 else 6 if profit>0 else 0)+(5 if 0<pe<=35 else 3 if 0<pe<=60 else 0)
    s_tech=(8 if price>ma20>ma60 else 4 if price>ma20 else 0)+(8 if price>=prior_high else 4 if price>=prior_high*.97 else 0)+(7 if 1.1<=volratio<=2.8 else 4 if .8<=volratio<3.5 else 0)+(7 if 0.02<=ret20<=.22 else 3 if ret20>0 else 0)
    turnover=number(row.get('turnover'))
    days=int(number(row.get('listed_days')) or 0)
    s_risk=(4 if amount>=5e8 else 2)+(3 if days>=250 else 0)+(3 if turnover is not None and 1<=turnover<=12 else 0)
    total=round(s_market+s_sector+s_fund+s_tech+s_risk,1)
    if days<120 or row.get('st') or price<ma20 or ma20<ma60 or pe<=0 or rev<0 or profit<0 or volratio>4 or turnover is None or turnover>20:
        return None
    if total<65:
        return None
    # Reference price plans, NOT executable guarantees. 1R risk, 2R/3R possible reward.
    entry_lo=max(ma20,price*.975)
    entry_hi=min(price,price*1.005)
    if entry_lo>entry_hi:
        return None
    stop=min(ma20*.97,low20*.985)
    stop=max(0.01,stop)
    if stop>=entry_lo:
        return None
    risk_per_share=entry_hi-stop
    # A shares: standard board 100, STAR 200 (minimum), BSE 100 (varies incremental)
    lot=200 if str(row['code']).startswith('688') else 100
    risk_budget=account*risk_pct/100
    max_alloc=account*max_weight
    shares=max(0,math.floor(min(risk_budget/risk_per_share,max_alloc/entry_hi)/lot)*lot)
    if shares < lot:
        return None # no executable standard lot within risk and position caps
    return {
        'code':str(row['code']).zfill(6),'name':str(row['name']), 'sector':str(row['sector']),
        'date':str(bars[-1]['date']), 'price':fround(price), 'score':total,
        'steps':{'大盘':s_market,'行业':s_sector,'基本面':s_fund,'技术量价':s_tech,'风险流动性':s_risk},
        'ma20':fround(ma20),'ma60':fround(ma60),'volume_ratio':fround(volratio),
        'high20_prior':fround(prior_high),'revenue_growth':fround(rev),'profit_growth':fround(profit),
        'pe':fround(pe),'turnover':fround(turnover),'trade_amount_yi':fround(amount/1e8),
        'plan':{'entry_low':fround(entry_lo),'entry_high':fround(entry_hi),'risk_invalid':fround(stop),
                'target_2r':fround(entry_hi+2*risk_per_share),'target_3r':fround(entry_hi+3*risk_per_share),
                'shares':shares,'budget_risk':fround(shares*risk_per_share),'max_position':fround(shares*entry_hi),
                'note':'仅观察价位；突破、回踩与成交量须次日盘中确认，不保证买入或止损成交。'},
        'financial_period':str(fundamentals.get('period','未标注')),
        'reason':f"MA20 {ma20:.2f} / MA60 {ma60:.2f}，量比 {volratio:.2f}，营收同比 {rev:.1f}%，归母净利同比 {profit:.1f}%"
    }


def report(status,trade_date,universe,candidates,**extra):
    return {'status':status,'as_of':trade_date,'generated_at':datetime.now().astimezone().isoformat(timespec='seconds'),
            'universe_count':universe,'candidates':sorted(candidates,key=lambda r:r['score'],reverse=True)[:5],
            'disclaimer':'规则化候选观察名单，不构成具体买卖推荐；收盘后研究数据非实时行情，未验证盈利能力。', **extra}
