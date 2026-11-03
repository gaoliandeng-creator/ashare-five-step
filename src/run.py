"""收盘数据采集 + 筛选 + 生成静态网站，网络失败时明确报错，不发布伪推荐。"""
from __future__ import annotations
import argparse, concurrent.futures, json, os, random, time, sys
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import Counter
from engine import evaluate, number, report

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Shanghai')

def quarters_for(day):
    y=day.year
    # report-date cutoff at statutory deadline; only rely on publicly filed latest quarter
    if day>=date(y,11,1):return f'{y}0930'
    if day>=date(y,9,1):return f'{y}0630'
    if day>=date(y,5,1):return f'{y}0331'
    return f'{y-1}1231'

def retry(fn,tries=3):
    for i in range(tries):
        try:return fn()
        except Exception:
            if i==tries-1:raise
            time.sleep(1.4*(i+1)+random.random())

def collect(account,max_scan,day):
    import akshare as ak
    now=datetime.now(TZ)
    if day>now.date():raise RuntimeError('请求日期尚未结束')
    if day==now.date() and (now.hour,now.minute)<(16,10):
        raise RuntimeError('北京时间16:10之前不发布当日收盘推荐，避免未完结行情')
    if day.weekday()>=5:raise RuntimeError('周末不是交易日；不生成新的候选榜单')
    spot=retry(ak.stock_zh_a_spot_em)
    required={'代码','名称','最新价','成交额','市盈率-动态','换手率','涨跌幅'}
    if not required.issubset(spot.columns):raise RuntimeError(f'行情字段缺失: {required-set(spot.columns)}')
    spot=spot.copy()
    spot['代码']=spot['代码'].astype(str).str.zfill(6)
    spot['涨幅num']=spot['涨跌幅'].map(number)
    spot['成交额num']=spot['成交额'].map(number)
    spot['现价num']=spot['最新价'].map(number)
    spot['换手num']=spot['换手率'].map(number)
    spot=spot.dropna(subset=['涨幅num','成交额num','现价num','换手num'])
    spot=spot[(spot['成交额num']>=1e8)&(spot['现价num']>=2)]
    spot=spot[~spot['名称'].astype(str).str.contains('ST|退|\\*',regex=True)]
    spot=spot[~spot['代码'].str.startswith(('9','2'))]
    if len(spot)<100:raise RuntimeError('行情数据覆盖不足，拒绝发布：有效样本<100')
    breadth=float((spot['涨幅num']>0).mean())
    # Market breadth based only on SAME DAY universe; label explicitly.
    market_score=max(0,min(100,round(35+65*breadth,1)))
    period=quarters_for(day)
    fin=retry(lambda:ak.stock_yjbb_em(date=period))
    cols={'股票代码','营业收入-同比增长','净利润-同比增长'}
    if not cols.issubset(fin.columns):raise RuntimeError(f'财务字段缺失: {cols-set(fin.columns)}')
    fundamentals={}
    for _,r in fin.iterrows():
        code=str(r['股票代码']).zfill(6)
        rev=number(r['营业收入-同比增长'])
        prof=number(r['净利润-同比增长'])
        if rev is not None and prof is not None:
            fundamentals[code]={'revenue_growth':rev,'profit_growth':prof,'period':period}
    if len(fundamentals)<100:raise RuntimeError('财务数据样本不足，拒绝推荐')
    if '行业' in spot.columns:
        spot['所属行业']=spot['行业'].fillna('未分类')
    else:
        # Eastmoney spot may NOT include industry; use single broad market score, disclose limitation.
        spot['所属行业']='行业未提供'
    grp=spot.groupby('所属行业')['涨幅num'].agg(['mean','count']).to_dict('index')
    # Partial ranking of sector proxies. If industry missing, use neutral score and transparently mark.
    has_sector=spot['所属行业'].ne('行业未提供').any()
    sector_scores={k:max(0,min(100,50+v['mean']*8)) for k,v in grp.items()}
    # do not select only today's winners: sort on liquidity & moderately positive momentum to bound requests
    spot=spot[spot['代码'].isin(fundamentals)]
    spot=spot[(spot['涨幅num']>-6)&(spot['涨幅num']<9.8)&(spot['换手num']<20)]
    spot=spot.sort_values('成交额num',ascending=False).head(max_scan)
    if len(spot)<10:raise RuntimeError('可研究的股票样本过少')
    start=(day-timedelta(days=340)).strftime('%Y%m%d')
    end=day.strftime('%Y%m%d')
    def worker(rec):
        code=rec['代码']
        def get():return ak.stock_zh_a_hist(symbol=code,period='daily',start_date=start,end_date=end,adjust='qfq')
        try:
            df=retry(get,tries=2)
            if df is None or df.empty or not {'日期','收盘','最高','最低','成交量'}.issubset(df.columns):return None,'empty'
            df=df.sort_values('日期')
            bars=[{'date':str(r['日期'])[:10],'close':r['收盘'],'high':r['最高'],'low':r['最低'],'volume':r['成交量']} for _,r in df.iterrows()]
            if bars[-1]['date']!=day.isoformat():return None,'stale'
            row={'code':code,'name':rec['名称'],'sector':rec['所属行业'],
                 'price':rec['现价num'],'amount':rec['成交额num'],'pe':rec['市盈率-动态'],
                 'turnover':rec['换手num'],'st':False,'listed_days':len(bars)}
            sc=sector_scores.get(rec['所属行业'],50) if has_sector else 50
            return evaluate(row,bars,fundamentals.get(code),market_score,sc,account=account), 'ok'
        except Exception as exc:return None,'error'
    records=spot.to_dict('records')
    pool=min(4,max(1,int(os.getenv('WORKERS','3'))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=pool) as executor:
        outcome=list(executor.map(worker,records))
    counts=Counter(label for _,label in outcome)
    if counts['ok']<max(15,int(len(records)*.4)):
        raise RuntimeError(f'历史行情验证通过率不足: {dict(counts)}；不发布过时名单')
    candidates=[x for x,_ in outcome if x is not None]
    return report('ready',day.isoformat(),len(spot),candidates,
      report_period=period,market_breadth=round(breadth*100,2),
      data_sources=['AKShare 东方财富全市场快照','AKShare 东方财富复权日K线','AKShare 东方财富业绩报表'],
      checked_history=counts['ok'],screened_pass=len(candidates),
      sector_method='同日行业平均涨幅代理' if has_sector else '源缺行业字段，采用中性50分',
      scan_limit=max_scan,notes=['仅流动性优先的样本扫描，不是全市场逐股历史回测','业绩报表按报告期归属，未逐条验证公告发布日期','仅在历史日K线最后日期与指定交易日一致时发布','上市交易日数以请求窗口内历史K线估计，非完整上市天数'])

def build_site(payload):
    site=ROOT/'site'
    (site/'data').mkdir(exist_ok=True,parents=True)
    (site/'data'/'latest.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    tpl=(ROOT/'site'/'template.html').read_text(encoding='utf-8')
    # JSON safely embed, so index.html works even when opened from disk
    json_inline=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    (site/'index.html').write_text(tpl.replace('__INITIAL_PAYLOAD__',json_inline),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',default=datetime.now(TZ).date().isoformat())
    parser.add_argument('--account',type=float,default=28000)
    parser.add_argument('--max-scan',type=int,default=120)
    parser.add_argument('--preview',action='store_true',help='Generate a clearly labeled awaiting-data site without internet')
    args=parser.parse_args()
    if args.preview:
        out=report('awaiting_data',args.date,0,[],error='尚未运行联网数据采集；不可展示真实候选股票')
    else:
        try:out=collect(args.account,max(30,min(args.max_scan,500)),date.fromisoformat(args.date))
        except Exception as exc:
            print(f'FAILED: {exc}',file=sys.stderr)
            sys.exit(1) # don't overwrite last successful published snapshot
    build_site(out)
    print(json.dumps({'status':out['status'],'date':out['as_of'],'candidates':len(out['candidates'])},ensure_ascii=False))
