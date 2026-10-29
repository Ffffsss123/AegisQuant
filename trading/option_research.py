"""Validate real historical option quote evidence; never synthesize option prices."""
import hashlib
import json
from pathlib import Path
from datetime import datetime,date,timedelta
from .policy import SafetyError,number,option_candidates


def validate_evidence(path):
    if not path:raise SafetyError('Real historical option quote evidence is required')
    report_path=Path(path).resolve();report=json.loads(report_path.read_text())
    source=report_path.parent/report['input_file']
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=report.get('input_sha256'):raise SafetyError('Historical option evidence changed')
    computed=research(json.loads(raw))
    if computed['closed_trades']<1:raise SafetyError('No completed historical option trade in evidence')
    if report.get('results')!=computed:raise SafetyError('Evidence report does not reproduce')
    return computed


def research(data):
    if not data.get('source_url','').startswith('https://') or data.get('synthetic') is not False:
        raise SafetyError('Historical quote provenance required; synthetic data not accepted for live qualification')
    fx=number(data['nzdusd']);events=data['observations']
    if not 0<fx<2:raise SafetyError('Invalid FX')
    last=None;position=None;trades=[];spent=0;missing=0
    for e in events:
        now=datetime.fromisoformat(e['time'])
        if now.tzinfo is None or (last and now<=last):raise SafetyError('Observation timestamps must increase')
        last=now
        if e['signal_date']>=now.date().isoformat():raise SafetyError('Signals must precede the execution day')
        contracts={c['code']:c for c in e['contracts']}
        if position:
            c=contracts.get(position['code']);signal=e['signals'].get(position['underlying'])
            due=(date.fromisoformat(position['expiry'])-now.date()).days<=7
            reverse=signal and ((position['right']=='CALL' and signal['sell']) or (position['right']=='PUT' and signal['fast']>=signal['slow']))
            if due or reverse:
                if c is None:missing+=1;continue
                from .policy import quote_price
                bid=quote_price(c['book'],'SELL',now,True)
                proceeds=bid*100-5
                trades.append(dict(code=position['code'],entry=position['time'],exit=e['time'],pnl_usd=proceeds-spent))
                position=None
            continue
        selected=option_candidates(e['contracts'],e['signals'],now.date(),now,25*fx,5)
        if selected:
            position=dict(selected[0],time=e['time']);spent=position['ask']*100+5
    if missing:raise SafetyError('Missing option exit observations; cannot qualify')
    if position:raise SafetyError('Open option at end; extend quote evidence through exit')
    return dict(closed_trades=len(trades),pnl_usd=sum(t['pnl_usd'] for t in trades),trades=trades,
        limitations='Historical quotes supplied by user/provider; no independent vendor audit. Fixed fees and FX. Not a profitability approval.')


def main():
    import argparse
    p=argparse.ArgumentParser(description='真实期权历史报价验证；不使用股票收益代理期权收益')
    p.add_argument('input');p.add_argument('--output',required=True)
    a=p.parse_args();source=Path(a.input).resolve();output=Path(a.output).resolve()
    data=source.read_bytes();result=research(json.loads(data))
    import os
    output.parent.mkdir(parents=True,exist_ok=True)
    report=dict(input_file=os.path.relpath(source,output.parent),input_sha256=hashlib.sha256(data).hexdigest(),results=result)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
