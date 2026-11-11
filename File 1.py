import time, sqlite3, requests
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

API='https://external-api.kalshi.com/trade-api/v2'
DB='paper_trades.db'
st.set_page_config(page_title='Kalshi BTC 15m | Paper Desk',layout='wide',page_icon='₿')
st.title('₿ Kalshi BTC 15-Minute | Paper Trading Desk')
st.caption('Live public Kalshi quotes • simulated orders only • no account connection • no real trades')

def conn():
    c=sqlite3.connect(DB,check_same_thread=False)
    c.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ticker TEXT, side TEXT, qty INTEGER, entry REAL, exit REAL, status TEXT, opened TEXT, closed TEXT, pnl REAL, note TEXT)')
    c.commit();return c

def get(path,params=None):
    r=requests.get(API+path,params=params,timeout=8,headers={'User-Agent':'KalshiPaperDashboard/1.0'});r.raise_for_status();return r.json()

def num(x):
    try:return float(x)
    except (ValueError,TypeError):return None

def quote(m,side):
    return num(m.get(('yes' if side=='UP / YES' else 'no')+'_ask_dollars'))

def bid(m,side):
    return num(m.get(('yes' if side=='UP / YES' else 'no')+'_bid_dollars'))

def settle(c,market):
    result=market.get('result','')
    if result not in ('yes','no'):return 0
    rows=c.execute("SELECT id,side,qty,entry FROM trades WHERE ticker=? AND status='OPEN'",(market['ticker'],)).fetchall()
    for id,side,qty,entry in rows:
        win=(result=='yes' and side=='UP / YES') or (result=='no' and side=='DOWN / NO')
        payout=1.0 if win else 0.0
        pnl=(payout-entry)*qty
        c.execute("UPDATE trades SET exit=?,status='SETTLED',closed=?,pnl=? WHERE id=?",(payout,datetime.now(timezone.utc).isoformat(),pnl,id))
    c.commit();return len(rows)

with st.sidebar:
    st.header('Paper controls')
    initial=st.number_input('Starting paper bankroll ($)',min_value=10.0,value=250.0,step=25.0)
    qty=st.number_input('Contracts per order',min_value=1,max_value=1000,value=5)
    min_edge=st.slider('Minimum distance from target (%)',0.01,0.30,0.08,0.01)
    max_spend=st.number_input('Max paper spend per order ($)',min_value=1.0,value=10.0)
    st.caption('Signal is a momentum/distance filter, NOT a calibrated probability or verified trading edge.')
    st.caption('Refreshes every 5 seconds while this page stays open. Browser notifications are not enabled.')

c=conn()
@st.fragment(run_every='5s')
def dashboard():
    try:
        raw=get('/markets',{'series_ticker':'KXBTC15M','status':'open','limit':100})
        markets=raw.get('markets',[])
        now=datetime.now(timezone.utc)
        eligible=[]
        for m in markets:
            try:
                end=datetime.fromisoformat(m['close_time'].replace('Z','+00:00'))
                if end>now:eligible.append((end,m))
            except Exception:pass
        if not eligible:
            st.warning('No currently open BTC 15-minute market found. Waiting for next market.');return
        end,m=min(eligible,key=lambda x:x[0]);ticker=m['ticker']
        remaining=max(0,(end-now).total_seconds())
        yesask=quote(m,'UP / YES');noask=quote(m,'DOWN / NO')
        yesbid=bid(m,'UP / YES');nobid=bid(m,'DOWN / NO')
        st.session_state.setdefault('hist',{})
        hist=st.session_state.hist.setdefault(ticker,[])
        mid=(yesask+yesbid)/2 if yesask is not None and yesbid is not None else num(m.get('last_price_dollars'))
        if mid is not None:
            hist.append((now,mid));st.session_state.hist[ticker]=hist[-900:]
        # A transparent signal: YES mid moves over the last 60 seconds, NOT underlying BTC momentum.
        earlier=next((v for ts,v in hist if (now-ts).total_seconds()<=60),None)
        change=(mid-earlier) if mid is not None and earlier is not None else None
        signal='WAIT'
        if remaining>120 and len(hist)>=5 and change is not None:
            if change>=0.05:signal='UP WATCH'
            elif change<=-0.05:signal='DOWN WATCH'
        st.subheader(f'Active market: {ticker}')
        st.caption('Market rules: '+str(m.get('rules_primary') or m.get('title') or 'Check Kalshi for settlement terms'))
        a,b,d,e=st.columns(4)
        a.metric('Time left',f'{int(remaining//60):02d}:{int(remaining%60):02d}')
        b.metric('YES ask',f'${yesask:.3f}' if yesask is not None else 'N/A')
        d.metric('NO ask',f'${noask:.3f}' if noask is not None else 'N/A')
        e.metric('Signal',signal)
        if signal!='WAIT':st.info(f'{signal}: contract-price momentum only; not an instruction to buy.')
        if len(hist)>1:
            fig=go.Figure(go.Scatter(x=[x[0] for x in hist],y=[x[1] for x in hist],mode='lines',name='YES mid'))
            fig.update_layout(height=300,yaxis_title='YES contract price ($)',xaxis_title='UTC',margin=dict(l=15,r=15,t=15,b=15))
            st.plotly_chart(fig,use_container_width=True,key='chart_'+ticker)
        else:st.caption('Building chart from live snapshots. Historical points are not fabricated.')
        st.caption('Chart tracks Kalshi YES contract price, not underlying BTC/USD. Snapshots accumulate only while the app runs.')
        st.markdown('#### Paper orders')
        side=st.radio('Simulated contract', ['UP / YES','DOWN / NO'],horizontal=True,key='side')
        ask=quote(m,side);cost=qty*ask if ask is not None else None
        st.write(f'Estimated paper entry cost: **${cost:.2f}**' if cost is not None else 'Ask quote unavailable')
        spent=c.execute("SELECT COALESCE(SUM(qty*entry),0) FROM trades WHERE status='OPEN'").fetchone()[0]
        realized=c.execute('SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status !=\'OPEN\'').fetchone()[0]
        bankroll=initial+realized-spent
        if st.button('Paper BUY (no real order)',disabled=ask is None or cost is None or cost>max_spend or cost>bankroll or remaining<=15,key='buy'):
            c.execute('INSERT INTO trades(ticker,side,qty,entry,status,opened,note) VALUES (?,?,?,?,?,?,?)',(ticker,side,qty,ask,'OPEN',now.isoformat(),'Paper fill at displayed ask; no slippage modeled'))
            c.commit();st.success('Paper order recorded at quoted ask.');st.rerun()
        st.markdown('#### Open positions')
        rows=c.execute("SELECT id,ticker,side,qty,entry FROM trades WHERE status='OPEN' ORDER BY id DESC").fetchall()
        for id,tk,sd,n,entry in rows:
            mm=m if tk==ticker else None
            if mm is None:
                try:mm=get('/markets/'+tk)['market']
                except Exception:pass
            if mm and mm.get('result') in ('yes','no'):
                settle(c,mm);continue
            current_bid=bid(mm,sd) if mm else None
            col1,col2=st.columns([5,1])
            col1.write(f'#{id} | {tk} | {sd} ×{n} | entry ${entry:.3f} | bid '+(f'${current_bid:.3f}' if current_bid is not None else 'N/A'))
            if col2.button('Paper SELL',key=f'sell{id}',disabled=current_bid is None):
                pnl=(current_bid-entry)*n
                c.execute("UPDATE trades SET exit=?,status='SOLD',closed=?,pnl=? WHERE id=?",(current_bid,now.isoformat(),pnl,id));c.commit();st.rerun()
        realized=c.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status!='OPEN'").fetchone()[0]
        st.metric('Realized paper P/L (excludes fees)',f'${realized:+.2f}')
        history=pd.read_sql_query('SELECT ticker,side,qty,entry,exit,status,pnl,opened,closed FROM trades ORDER BY id DESC LIMIT 100',c)
        st.dataframe(history,use_container_width=True,hide_index=True)
        st.caption('Paper fills use displayed quotes, not executable guaranteed prices. No fees, spreads beyond quoted bid/ask, latency, or slippage are modeled. Positions only settle automatically when this app is running and Kalshi reports a result.')
        st.caption('Last successful API refresh: '+now.strftime('%Y-%m-%d %H:%M:%S UTC'))
    except requests.RequestException as ex:
        st.error('Kalshi API request failed: '+str(ex))
    except Exception as ex:
        st.error('Dashboard error: '+str(ex))

dashboard()
