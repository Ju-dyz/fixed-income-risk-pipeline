from pathlib import Path
from datetime import timedelta
import numpy as np, pandas as pd
from .pricing import bond_metrics, key_rate_weights

PORTFOLIOS = [('P01','Treasury Liquidity','Government'),('P02','Core Credit','Investment Grade'),
              ('P03','Long Duration','Liability Hedging'),('P04','Rates Macro','Rates/Swaps'),
              ('P05','Securitized Income','MBS/Credit')]
TENORS = np.array([2.,5.,10.,30.])

def _instruments(rng, n=50):
    types = ['UST']*15 + ['CORP']*20 + ['IRS']*10 + ['MBS']*5
    rows=[]
    start=pd.Timestamp('2020-01-02')
    for i,t in enumerate(types[:n],1):
        yrs=float(rng.choice([1,2,3,5,7,10,15,20,30])); mat=pd.Timestamp('2026-10-30')+pd.DateOffset(days=int(yrs*365.25))
        coupon=float(np.clip(rng.normal(.042 if t!='MBS' else .052,.012),.005,.085))
        rows.append(dict(instrument_id=f'I{i:04d}',cusip=f'{10000000+i:08d}{i%10}',isin=f'US{10000000+i:08d}{i%100:02d}',
          instrument_type=t,issuer=('US Treasury' if t=='UST' else f'Issuer {i%12+1}' if t=='CORP' else 'Swap Counterparty' if t=='IRS' else f'Agency Pool {i%3+1}'),
          currency='USD',coupon_rate=coupon,maturity_date=mat.date(),issue_date=(start+pd.DateOffset(days=i*13)).date(),
          rating=None if t in ('UST','IRS') else rng.choice(['AAA','AA','A','BBB']),sector={'UST':'Government','CORP':rng.choice(['Financials','Industrials','Utilities']),'IRS':'Rates','MBS':'Securitized'}[t],face_value=100.,synthetic_flag=True))
    return pd.DataFrame(rows)

def generate(cfg):
    root=Path(cfg['root']); raw=root/cfg['paths']['raw']; proc=root/cfg['paths']['processed']
    raw.mkdir(parents=True,exist_ok=True); proc.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(cfg['project']['random_seed'])
    dates=pd.bdate_range(end=cfg['project']['end_date'],periods=cfg['project']['business_days'])
    inst=_instruments(rng); ports=pd.DataFrame(PORTFOLIOS,columns=['portfolio_id','portfolio_name','strategy']); ports['synthetic_flag']=True
    # Correlated level/slope/curvature shocks, then coherent tenor curves.
    cov=np.array([[1.0,.25,.05],[.25,.5,.1],[.05,.1,.25]]) * 0.00008**2
    factors=rng.multivariate_normal([0,0,0],cov,len(dates)).cumsum(axis=0)
    base=np.array([.042,.043,.044,.046]); curve=base+factors[:,[0]]+factors[:,[1]]*np.array([-1,-.3,.3,1])+factors[:,[2]]*np.array([.5,-.5,-.5,.5])
    curve_rows=[{'business_date':d.date(),**{f'y{int(t)}':curve[j,k] for k,t in enumerate(TENORS)}} for j,d in enumerate(dates)]
    curve_df=pd.DataFrame(curve_rows)
    market=[]; risk=[]; positions=[]; transactions=[]
    holdings={}
    for p,_,_ in PORTFOLIOS:
        chosen=rng.choice(inst.instrument_id, size=24, replace=False)
        holdings[p]={x:float(rng.integers(500,8000)*1000) for x in chosen}
    for di,d in enumerate(dates):
        for _,r in inst.iterrows():
            years=max((pd.Timestamp(r.maturity_date)-d).days/365.25,.05)
            rf=float(np.interp(years,TENORS,curve[di])); spread={'UST':0,'IRS':.001,'CORP':.011,'MBS':.018}[r.instrument_type]
            spread += ((int(r.instrument_id[1:])%7)-3)*.0007 + factors[di,2]*.8
            y=max(.001,rf+spread)
            if r.instrument_type=='IRS': price=100+(r.coupon_rate-y)*min(years,10)*90; dur=min(years,10)*.78; conv=dur**2*.55
            else: price,dur,conv=bond_metrics(100,r.coupon_rate,y,years)
            quote_ts=d+timedelta(hours=16)
            market.append(dict(business_date=d.date(),instrument_id=r.instrument_id,price=price,yield_rate=y,spread_rate=spread,quote_timestamp=quote_ts,is_stale=False))
            for p in holdings:
                if r.instrument_id not in holdings[p]: continue
                qty=holdings[p][r.instrument_id]
                # sparse, coherent transactions
                if di and rng.random()<.015:
                    trade_qty=float(rng.integers(-300,301)*1000); qty += trade_qty; holdings[p][r.instrument_id]=max(qty,0)
                    transactions.append(dict(trade_id=f'T{len(transactions)+1:08d}',trade_timestamp=d+timedelta(hours=10,minutes=int(rng.integers(0,300))),
                      settlement_date=(d+pd.offsets.BDay(2)).date(),portfolio_id=p,instrument_id=r.instrument_id,quantity=trade_qty,trade_price=price,source_system='TRADE_SYS'))
                qty=holdings[p][r.instrument_id]; mv=qty*price/100
                dv01=mv*dur*.0001; cs01=dv01 if r.instrument_type in ('CORP','MBS') else 0.
                krd=key_rate_weights(years)*dv01
                positions.append(dict(business_date=d.date(),portfolio_id=p,instrument_id=r.instrument_id,quantity=qty,clean_price=price,accrued_interest=0.,market_value=mv,source_system='POS_SYS'))
                risk.append(dict(business_date=d.date(),portfolio_id=p,instrument_id=r.instrument_id,market_value=mv,modified_duration=dur,convexity=conv,dv01=dv01,cs01=cs01,krd_2y=krd[0],krd_5y=krd[1],krd_10y=krd[2],krd_30y=krd[3]))
    mkt=pd.DataFrame(market); pos=pd.DataFrame(positions); riskdf=pd.DataFrame(risk); tx=pd.DataFrame(transactions)
    # Ground truth remains pristine.
    for name,df in [('instrument',inst),('portfolio',ports),('curve',curve_df),('market',mkt),('position',pos),('risk',riskdf),('transaction',tx)]: df.to_csv(proc/f'ground_truth_{name}.csv',index=False)
    # Source A positions: inconsistent names/IDs/timestamps and one duplicate.
    pa=pos.rename(columns={'business_date':'AsOfDate','portfolio_id':'Book','instrument_id':'SecurityID','quantity':'ParAmount','clean_price':'Price'})[['AsOfDate','Book','SecurityID','ParAmount','Price']].copy()
    pa['Book']=pa.Book.map({p:n for p,n,_ in PORTFOLIOS}); pa.loc[pa.index[::173],'Book']=pa.loc[pa.index[::173],'Book'].str.lower().str.replace(' ','_')
    idmap=inst.set_index('instrument_id').cusip; pa.loc[pa.index[::3],'SecurityID']=pa.loc[pa.index[::3],'SecurityID'].map(idmap)
    pa=pd.concat([pa,pa.iloc[[20]]],ignore_index=True); pa.to_csv(raw/'positions_source_a.csv',index=False)
    # Source B market: percent yields, mixed timestamps, missing/stale/duplicate quotes.
    mb=mkt.rename(columns={'business_date':'valuation_dt','instrument_id':'sec_id','price':'px','yield_rate':'yield_pct','spread_rate':'spread_bps','quote_timestamp':'quote_time'})[['valuation_dt','sec_id','px','yield_pct','spread_bps','quote_time']].copy()
    mb['yield_pct']*=100; mb['spread_bps']*=10000; mb.loc[mb.index[::487],'px']=np.nan
    mb.loc[mb.index[::619],'quote_time']=(pd.to_datetime(mb.loc[mb.index[::619],'quote_time'])-timedelta(days=4)).astype(str)
    mb.loc[mb.index[::211],'sec_id']=mb.loc[mb.index[::211],'sec_id'].map(inst.set_index('instrument_id')['isin'])
    mb=pd.concat([mb,mb.iloc[[10]]],ignore_index=True); mb.to_csv(raw/'market_source_b.csv',index=False)
    tc=tx.rename(columns={'trade_timestamp':'EXEC_TS','settlement_date':'SETTLE_DT','portfolio_id':'DESK_BOOK','instrument_id':'INTERNAL_SEC','quantity':'NOTIONAL','trade_price':'EXEC_PX'})
    tc.to_csv(raw/'transactions_source_c.csv',index=False)
    inst.to_csv(raw/'security_master.csv',index=False); ports.to_csv(raw/'portfolio_master.csv',index=False)
    return {'dates':dates,'instruments':len(inst),'positions':len(pos),'market':len(mkt)}

