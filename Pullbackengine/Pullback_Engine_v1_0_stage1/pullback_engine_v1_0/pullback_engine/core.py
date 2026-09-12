from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from math import isfinite
from statistics import median
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo
import math
import re


IST = ZoneInfo("Asia/Kolkata")
MARKET_START = time(9,15)
MARKET_END = time(15,15)
HUNT_START = time(10,0)
PRICE_MAX = 1200.0


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")


def parse_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        dt=value
    elif isinstance(value,(int,float)):
        dt=datetime.fromtimestamp(value, tz=IST)
    else:
        s=str(value).strip().replace("Z","+00:00")
        try:
            dt=datetime.fromisoformat(s)
        except ValueError:
            # public Psygrid format: YYYY-MM-DD HH:MM:SS IST
            s2=s.removesuffix(" IST")
            dt=datetime.strptime(s2,"%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def parse_candle(row: dict) -> Candle:
    ts=parse_timestamp(row.get("timestamp", row.get("epoch")))
    vals=[row.get(k) for k in ("open","high","low","close","volume")]
    if any(v is None for v in vals):
        raise ValueError("missing OHLCV")
    return Candle(ts,*[float(v) for v in vals])


def validate_candles(rows: Iterable[dict|Candle]) -> tuple[list[Candle], list[str]]:
    out=[]; errors=[]; seen=set(); prev=None
    for pos,row in enumerate(rows):
        try:
            c=row if isinstance(row,Candle) else parse_candle(row)
            if c.timestamp in seen:
                raise ValueError("duplicate timestamp")
            if prev is not None and c.timestamp <= prev:
                raise ValueError("timestamp not strictly increasing")
            if not all(isfinite(x) for x in (c.open,c.high,c.low,c.close,c.volume)):
                raise ValueError("non-finite OHLCV")
            if c.high < max(c.open,c.close) or c.low > min(c.open,c.close) or c.high < c.low:
                raise ValueError("impossible OHLC")
            if c.volume < 0:
                raise ValueError("negative volume")
            seen.add(c.timestamp); prev=c.timestamp; out.append(c)
        except Exception as exc:
            errors.append(f"{pos}:{type(exc).__name__}:{exc}")
    return out,errors


def aggregate_5m(candles: Sequence[Candle]) -> list[Candle]:
    """Aggregate only complete five-minute groups; no filling/interpolation."""
    groups={}
    for c in candles:
        local=c.timestamp.astimezone(IST)
        if local.time() < MARKET_START or local.time() >= MARKET_END:
            continue
        minute=(local.hour*60+local.minute)
        slot=minute-(minute-15)%5
        key=(local.date(),slot)
        groups.setdefault(key,[]).append(c)
    result=[]
    for (date,slot),items in sorted(groups.items()):
        if len(items)!=5:
            continue
        items=sorted(items,key=lambda x:x.timestamp)
        expected=[slot+i for i in range(5)]
        actual=[x.timestamp.astimezone(IST).hour*60+x.timestamp.astimezone(IST).minute for x in items]
        if actual!=expected:
            continue
        result.append(Candle(
            items[0].timestamp.replace(second=0,microsecond=0),
            items[0].open,max(x.high for x in items),min(x.low for x in items),
            items[-1].close,sum(x.volume for x in items)
        ))
    return result


def ema(closes: Sequence[float], period:int) -> list[Optional[float]]:
    if period<=0: raise ValueError("period")
    out=[None]*len(closes)
    if len(closes)<period: return out
    seed=sum(closes[:period])/period
    out[period-1]=seed
    alpha=2/(period+1)
    prev=seed
    for i in range(period,len(closes)):
        prev=alpha*closes[i]+(1-alpha)*prev
        out[i]=prev
    return out


def atr_wilder(candles: Sequence[Candle], period:int=14) -> list[Optional[float]]:
    out=[None]*len(candles)
    if len(candles)<period+1: return out
    trs=[None]*len(candles)
    for i in range(1,len(candles)):
        p=candles[i-1].close
        trs[i]=max(candles[i].high-candles[i].low,abs(candles[i].high-p),abs(candles[i].low-p))
    # seed uses the first `period` completed true ranges: transitions 1..period
    seed=sum(trs[1:period+1])/period
    out[period]=seed
    prev=seed
    for i in range(period+1,len(candles)):
        prev=((period-1)*prev+trs[i])/period
        out[i]=prev
    return out


def session_vwap(candles: Sequence[Candle]) -> list[Optional[float]]:
    out=[]; num=0.0; den=0.0; current_date=None
    for c in candles:
        d=c.timestamp.astimezone(IST).date()
        if d!=current_date:
            current_date=d; num=den=0.0
        tp=(c.high+c.low+c.close)/3
        num+=tp*c.volume; den+=c.volume
        out.append(num/den if den>0 else None)
    return out


def _gt_all(x, vals): return all(x>v for v in vals)
def _lt_all(x, vals): return all(x<v for v in vals)

def nifty_regime(candles: Sequence[Candle]) -> tuple[list[Optional[bool]],list[Optional[bool]]]:
    e20=ema([c.close for c in candles],20); e60=ema([c.close for c in candles],60)
    bull=[None]*len(candles); bear=[None]*len(candles)
    for i,c in enumerate(candles):
        if i<15 or e20[i] is None or e60[i] is None or e20[i-5] is None: continue
        bull[i]=(c.close>e20[i] and e20[i]>e60[i] and e20[i]>e20[i-5] and c.close>candles[i-15].close)
        bear[i]=(c.close<e20[i] and e20[i]<e60[i] and e20[i]<e20[i-5] and c.close<candles[i-15].close)
    return bull,bear


def pivot_high(c: Sequence[Candle], p:int) -> bool:
    if p<3 or p+3>=len(c): return False
    return c[p].high>c[p-1].high and c[p].high>c[p-2].high and c[p].high>c[p-3].high and c[p].high>=c[p+1].high and c[p].high>=c[p+2].high and c[p].high>=c[p+3].high


def pivot_low(c: Sequence[Candle], p:int) -> bool:
    if p<3 or p+3>=len(c): return False
    return c[p].low<c[p-1].low and c[p].low<c[p-2].low and c[p].low<c[p-3].low and c[p].low<=c[p+1].low and c[p].low<=c[p+2].low and c[p].low<=c[p+3].low


def confirmed_pivots(c: Sequence[Candle], through_index: Optional[int]=None):
    """Only emits p when p+3 is already completed (no lookahead)."""
    end=(len(c)-4 if through_index is None else min(through_index-3,len(c)-4))
    highs=[]; lows=[]
    for p in range(3,end+1):
        if pivot_high(c,p): highs.append(p)
        if pivot_low(c,p): lows.append(p)
    return highs,lows


def retracement(direction:str, h_imp:float,l_imp:float,pb_extreme:float)->float:
    denom=h_imp-l_imp
    if denom<=0: return math.nan
    if direction=="LONG": return (h_imp-pb_extreme)/denom
    return (pb_extreme-l_imp)/denom


def ols_slope_r2(values: Sequence[float]) -> tuple[float,float]:
    n=len(values)
    if n<2: return math.nan,math.nan
    xbar=(n-1)/2
    ybar=sum(values)/n
    ssx=sum((i-xbar)**2 for i in range(n))
    ssy=sum((y-ybar)**2 for y in values)
    if ssx==0 or ssy==0: return math.nan,0.0
    slope=sum((i-xbar)*(y-ybar) for i,y in enumerate(values))/ssx
    r2=(sum((i-xbar)*(y-ybar) for i,y in enumerate(values))**2)/(ssx*ssy)
    return slope,r2


def structure_a(c: Sequence[Candle], start:int,end:int,direction:str)->bool:
    vals=[x.close for x in c[start:end+1]]
    slope,r2=ols_slope_r2(vals)
    return bool(math.isfinite(slope) and r2>=0.50 and ((direction=="LONG" and slope<0) or (direction=="SHORT" and slope>0)))


def structure_b(c: Sequence[Candle], start:int,end:int,direction:str)->bool:
    rows=c[start:end+1]; n=len(rows)
    if n<2: return False
    bh,_=ols_slope_r2([x.high for x in rows]); bl,_=ols_slope_r2([x.low for x in rows])
    if not (math.isfinite(bh) and math.isfinite(bl)): return False
    directional=(bh<0 and bl<0) if direction=="LONG" else (bh>0 and bl>0)
    atrs=atr_wilder(c,14)
    av=[atrs[i] for i in range(start,end+1) if atrs[i] is not None]
    if not av: return False
    return directional and abs(bh-bl)<=0.5*(sum(av)/len(av)/n)


def structure_c(c: Sequence[Candle], start:int,end:int)->bool:
    if end-start+1<6: return False
    rows=c[start:end+1]
    first=rows[:3]; last=rows[-3:]
    re=max(x.high for x in first)-min(x.low for x in first)
    rl=max(x.high for x in last)-min(x.low for x in last)
    atrs=atr_wilder(c,14)
    ae=[atrs[start+i] for i in range(3) if atrs[start+i] is not None]
    al=[atrs[end-2+i] for i in range(3) if atrs[end-2+i] is not None]
    if len(ae)!=3 or len(al)!=3: return False
    return rl<=0.70*re and (sum(al)/3)<=0.80*(sum(ae)/3)


def canonical_structure(c: Sequence[Candle], start:int,end:int,direction:str)->str|None:
    for name,fn in (("A",structure_a),("B",structure_b),("C",structure_c)):
        try:
            ok=fn(c,start,end,direction)
        except Exception:
            ok=False
        if ok: return name
    return None


def momentum_metrics(c: Sequence[Candle], sl:int,sh:int,lpb:int):
    atrs=atr_wilder(c,14)
    imp_atr=[atrs[i] for i in range(sl,sh+1) if atrs[i] is not None]
    pb_atr=[atrs[i] for i in range(sh,lpb+1) if atrs[i] is not None]
    if not imp_atr or not pb_atr: return None,None,None
    mimp=abs(c[sh].close-c[sl].close)/sum(imp_atr)
    mpb=abs(c[lpb].close-c[sh].close)/sum(pb_atr)
    return mimp,mpb,mpb/mimp if mimp else math.nan


def volume_ratio(c: Sequence[Candle],start:int,end:int):
    if end-start+1<3: return None
    ve=median([c[i].volume for i in range(start,start+3)])
    vl=median([c[i].volume for i in range(end-2,end+1)])
    return None if ve==0 else vl/ve


def early_entry(direction:str, close:float, h_imp:float,l_imp:float,pb:float)->float:
    if direction=="LONG":
        d=h_imp-pb
        return math.nan if d<=0 else (close-pb)/d
    d=pb-l_imp
    return math.nan if d<=0 else (pb-close)/d


def reacceleration_1m(c: Sequence[Candle], t:int,direction:str)->bool:
    if t<3: return False
    if direction=="LONG":
        return c[t].close>max(c[t-1].close,c[t-2].close,c[t-3].close) and c[t].close>c[t-1].close and (c[t].close-c[t-1].close)>(c[t].close-c[t-3].close)/3
    return c[t].close<min(c[t-1].close,c[t-2].close,c[t-3].close) and c[t].close<c[t-1].close and (c[t-1].close-c[t].close)>(c[t-3].close-c[t].close)/3


def stock_trend(c: Sequence[Candle],t:int,direction:str):
    e20=ema([x.close for x in c],20); e60=ema([x.close for x in c],60)
    if t<5 or e20[t] is None or e60[t] is None or e20[t-5] is None: return None
    if direction=="LONG": return c[t].close>e20[t] and e20[t]>e60[t] and e20[t]>e20[t-5]
    return c[t].close<e20[t] and e20[t]<e60[t] and e20[t]<e20[t-5]


def entry_eligible(price:float, dt:datetime)->bool:
    return price<=PRICE_MAX and MARKET_START<=dt.astimezone(IST).time()<MARKET_END and dt.astimezone(IST).time()>=HUNT_START


def core_signal_long(*, regime, price, impulse_ok, retracement_ok, structure_ok, trend_ok, ep, reac_ok, stale=False):
    return bool(regime is True and price<=PRICE_MAX and impulse_ok is True and retracement_ok is True and structure_ok is True and trend_ok is True and ep<=0.45 and reac_ok is True and not stale)


def core_signal_short(**kwargs):
    return core_signal_long(**kwargs)


class SetupState(str,Enum):
    IDLE="IDLE"; IMPULSE_DETECTED="IMPULSE_DETECTED"; PULLBACK_CANDIDATE="PULLBACK_CANDIDATE"
    QUALIFIED_PULLBACK="QUALIFIED_PULLBACK"; TRIGGER_ARMED="TRIGGER_ARMED"; TRIGGERED="TRIGGERED"
    MONITORING="MONITORING"; COMPLETED="COMPLETED"; INVALIDATED="INVALIDATED"


@dataclass
class Setup:
    symbol:str
    direction:str
    impulse_start:int
    impulse_end:int
    pullback_extreme:int
    setup_id:str
    state:SetupState=SetupState.QUALIFIED_PULLBACK
    triggered_at:Optional[datetime]=None
    entry_price:Optional[float]=None
    trend_invalidated:bool=False


@dataclass
class StateStore:
    setups: dict[str,Setup]=field(default_factory=dict)
    triggered_ids:set[str]=field(default_factory=set)

    def add(self,s:Setup):
        if s.setup_id in self.setups: raise ValueError("duplicate SetupID")
        self.setups[s.setup_id]=s

    def trigger(self,setup_id:str,dt:datetime,price:float):
        s=self.setups[setup_id]
        if s.state==SetupState.TRIGGERED or setup_id in self.triggered_ids:
            return False
        s.state=SetupState.TRIGGERED; s.triggered_at=dt; s.entry_price=price
        self.triggered_ids.add(setup_id)
        return True

    def begin_monitoring(self,setup_id:str):
        s=self.setups[setup_id]
        if s.state==SetupState.TRIGGERED: s.state=SetupState.MONITORING


def trend_invalidation(c: Sequence[Candle],t:int,direction:str)->bool:
    e20=ema([x.close for x in c],20); e60=ema([x.close for x in c],60)
    if t<5 or e20[t] is None or e60[t] is None or e20[t-5] is None: return False
    def bad(i):
        if direction=="LONG":
            return c[i].close<e20[i] and e20[i]<=e20[i-5] and e20[i]<=e60[i]
        return c[i].close>e20[i] and e20[i]>=e20[i-5] and e20[i]>=e60[i]
    return t>=1 and bad(t) and bad(t-1)


def setup_id(symbol:str,date:str,seq:int)->str:
    clean=re.sub(r"[^A-Za-z0-9_-]","",symbol.upper())
    return f"{clean}-PB-{date.replace('-','')}-{seq:03d}"


def funnel_counts(symbol_rows:dict[str,dict])->dict[str,int]:
    stages=["universe","price_eligible","valid_data","regime","impulse","pullback","structure","trend","early_entry","reacceleration","signal"]
    return {s:sum(1 for x in symbol_rows.values() if x.get(s) is True) for s in stages}
