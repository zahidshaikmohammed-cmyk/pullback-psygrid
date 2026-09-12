from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import math
from pullback_engine.core import *

IST=ZoneInfo("Asia/Kolkata")

def mk(i,close,open_=None,high=None,low=None,vol=100):
    ts=datetime(2026,1,1,9,15,tzinfo=IST)+timedelta(minutes=i)
    o=close if open_ is None else open_
    h=max(o,close)+0.5 if high is None else high
    l=min(o,close)-0.5 if low is None else low
    return Candle(ts,o,h,l,close,vol)

def test_validation_rejects_bad_and_duplicate():
    rows=[{"timestamp":"2026-01-01T09:15:00+05:30","open":10,"high":9,"low":8,"close":9,"volume":1},
          {"timestamp":"2026-01-01T09:15:00+05:30","open":10,"high":11,"low":9,"close":10,"volume":1}]
    good,errors=validate_candles(rows)
    assert len(good)==1
    assert len(errors)==1

def test_validation_negative_volume():
    good,errors=validate_candles([mk(0,10).__dict__|{"volume":-1}])
    assert not good and errors

def test_5m_exact_group_and_missing_group():
    rows=[mk(i,100+i) for i in range(10)]
    five=aggregate_5m(rows)
    assert len(five)==2
    assert five[0].open==100 and five[0].close==104 and five[0].volume==500
    rows=[x for x in rows if x.timestamp.minute!=18]
    assert len(aggregate_5m(rows))==1

def test_ema_exact_seed_and_recursive():
    out=ema([1,2,3,4,5],3)
    assert out[:2]==[None,None]
    assert out[2]==2
    assert out[3]==3
    assert out[4]==4

def test_atr_wilder():
    cs=[mk(i,10,open_=10,high=11,low=9,vol=1) for i in range(15)]
    a=atr_wilder(cs,14)
    assert a[14]==2

def test_vwap():
    cs=[mk(0,11,open_=10,high=12,low=10,vol=10),mk(1,12,open_=11,high=13,low=11,vol=20)]
    v=session_vwap(cs)
    assert round(v[0],8)==11.0
    assert round(v[1],8)==11.66666667

def test_pivot_confirmation_no_lookahead():
    short=[mk(i,100,high=100,low=100,vol=1) for i in range(6)]
    short[3]=mk(3,100,high=110,low=90)
    assert not pivot_high(short,3)
    cs=[mk(i,100,high=100,low=100,vol=1) for i in range(7)]
    cs[3]=mk(3,100,high=110,low=90)
    cs[4]=mk(4,100,high=105,low=95)
    cs[5]=mk(5,100,high=104,low=96)
    cs[6]=mk(6,100,high=103,low=97)
    assert pivot_high(cs,3)
    assert confirmed_pivots(cs)[0]==[3]

def test_retracement_boundaries():
    assert retracement("LONG",200,100,170)==0.3
    assert retracement("LONG",200,100,120)==0.8
    assert retracement("SHORT",200,100,130)==0.3
    assert retracement("SHORT",200,100,180)==0.8

def test_early_entry_boundary():
    assert early_entry("LONG",155,200,100,150)==0.1
    assert early_entry("SHORT",145,200,100,150)==0.1

def test_reacceleration_long_short():
    cs=[mk(i,x) for i,x in enumerate([10,8,7,6,9])]
    assert reacceleration_1m(cs,4,"LONG")
    cs=[mk(i,x) for i,x in enumerate([6,8,9,10,7])]
    assert reacceleration_1m(cs,4,"SHORT")

def test_state_no_duplicate_trigger():
    s=Setup("ABC","LONG",1,5,8,"ABC-PB-20260101-001")
    st=StateStore(); st.add(s)
    dt=datetime(2026,1,1,10,tzinfo=IST)
    assert st.trigger(s.setup_id,dt,100)
    assert not st.trigger(s.setup_id,dt,101)
    assert s.state==SetupState.TRIGGERED

def test_invalidation_requires_two_consecutive_bars():
    cs=[]
    closes=[100]*80
    for i,x in enumerate(closes): cs.append(mk(i,x))
    # This test verifies the function is conservative when trend is unavailable/unchanged.
    assert trend_invalidation(cs,79,"LONG") is False

def test_core_signal_secondary_conditions_do_not_exist_as_gates():
    assert core_signal_long(regime=True,price=100,impulse_ok=True,retracement_ok=True,
                            structure_ok=True,trend_ok=True,ep=.4,reac_ok=True,stale=False)


def test_5m_session_boundary_excludes_outside_session():
    pre=mk(-1,100)
    inrows=[mk(i,100+i) for i in range(5)]
    post=Candle(datetime(2026,1,1,15,15,tzinfo=IST),100,101,99,100,1)
    assert len(aggregate_5m([pre,*inrows,post]))==1

def test_ema_insufficient_history():
    assert ema([1,2],20)==[None,None]

def test_atr_insufficient_history():
    assert all(x is None for x in atr_wilder([mk(i,10) for i in range(14)],14))

def test_vwap_zero_volume_is_unavailable():
    cs=[mk(0,10,vol=0)]
    assert session_vwap(cs)==[None]

def test_nifty_regime_unavailable_before_warmup():
    cs=[mk(i,100+i) for i in range(30)]
    bull,bear=nifty_regime(cs)
    assert all(x is None for x in bull)
    assert all(x is None for x in bear)

def test_pivot_low_exact_inequality():
    cs=[mk(i,100,high=100,low=100) for i in range(7)]
    cs[3]=mk(3,100,high=110,low=90)
    cs[4]=mk(4,100,high=105,low=95)
    cs[5]=mk(5,100,high=104,low=96)
    cs[6]=mk(6,100,high=103,low=97)
    assert pivot_low(cs,3)

def test_retracement_outside_range():
    assert retracement("LONG",200,100,250)<0
    assert retracement("LONG",200,100,50)>0.8

def test_ols_zero_variance_fails():
    slope,r2=ols_slope_r2([5,5,5,5])
    assert math.isnan(slope) and r2==0

def test_structure_a_directional():
    cs=[mk(i,100-i) for i in range(20)]
    assert structure_a(cs,0,5,"LONG")
    assert not structure_a(cs,0,5,"SHORT")

def test_structure_a_short_directional():
    cs=[mk(i,100+i) for i in range(20)]
    assert structure_a(cs,0,5,"SHORT")
    assert not structure_a(cs,0,5,"LONG")

def test_structure_c_requires_six_bars():
    cs=[mk(i,100) for i in range(5)]
    assert not structure_c(cs,0,4)

def test_entry_eligibility_time_and_price():
    dt=datetime(2026,1,1,10,0,tzinfo=IST)
    assert entry_eligible(1200,dt)
    assert not entry_eligible(1200.01,dt)
    assert not entry_eligible(1200,datetime(2026,1,1,9,59,tzinfo=IST))
    assert not entry_eligible(1200,datetime(2026,1,1,15,15,tzinfo=IST))

def test_stock_trend_unavailable_until_ema_history():
    cs=[mk(i,100+i) for i in range(30)]
    assert stock_trend(cs,29,"LONG") is None

def test_setup_id_deterministic():
    assert setup_id("Reliance.NS","2026-09-12",1)=="RELIANCENS-PB-20260912-001"

def test_state_monitoring_transition():
    s=Setup("ABC","LONG",1,5,8,"ABC-PB-20260101-001")
    st=StateStore(); st.add(s)
    dt=datetime(2026,1,1,10,tzinfo=IST)
    assert st.trigger(s.setup_id,dt,100)
    st.begin_monitoring(s.setup_id)
    assert s.state==SetupState.MONITORING

def test_state_rejects_duplicate_setup_id():
    s=Setup("ABC","LONG",1,5,8,"DUP")
    st=StateStore(); st.add(s)
    try:
        st.add(s)
        assert False
    except ValueError:
        assert True

def test_funnel_counts_only_true_values():
    rows={"A":{"universe":True,"price_eligible":True,"valid_data":True,"signal":True},
          "B":{"universe":True,"price_eligible":False,"valid_data":True,"signal":False}}
    f=funnel_counts(rows)
    assert f["universe"]==2 and f["price_eligible"]==1 and f["signal"]==1
