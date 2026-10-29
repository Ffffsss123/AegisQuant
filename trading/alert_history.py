"""Read-only incremental alert timeline for the desktop; no trading-state writes."""
import json,sqlite3,threading
from pathlib import Path


def display_message(message):
    if any(x in message for x in ('Disconnect','网络中断','NN_ProtoRet_ByDisConnOrCacnel')):
        return 'OpenD 连接中断'
    if message=='Valuation unavailable: Stale intraday mark':return '持仓估值行情过期'
    if 'Read-only query timeout:' in message:return '只读查询超时：'+message.split('Read-only query timeout:',1)[1].strip()
    if 'PacketErr.Timeout' in message:return '行情请求超时'
    return message

class AlertHistory:
    def __init__(self):self.cache={};self.lock=threading.Lock()
    def records(self,path,active,source):
        with self.lock:return self._records(path,active,source)
    def _records(self,path,active,source):
        path=Path(path);key=str(path)
        if not path.exists():return self._group({},active,source)
        try:
            identity=path.stat().st_ino
            cached=self.cache.get(key)
            if not cached or cached['identity']!=identity:cached=dict(identity=identity,cursor=0,rows={},success={})
            c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=1)
            try:
                maximum=c.execute('select max(id) from events').fetchone()[0] or 0
                if maximum<cached['cursor']:cached=dict(identity=identity,cursor=0,rows={},success={})
                for eid,stamp,kind,body in c.execute("select id,ts,kind,body from events where id>? and id<=? and kind in ('alert','valuation','broker_positions','broker_orders','broker_history_orders') order by id",(cached['cursor'],maximum)):
                    try:
                        detail=json.loads(body)
                        if kind!='alert':
                            if kind=='valuation' or detail.get('return_code')==0:cached['success'][kind]=stamp
                            continue
                        message=detail.get('message')
                    except (TypeError,ValueError):continue
                    if not isinstance(message,str):continue
                    row=cached['rows'].setdefault(message,dict(first_seen=stamp,last_seen=stamp,count=0))
                    row['last_seen']=stamp;row['count']+=1
                cached['cursor']=maximum;self.cache[key]=cached
            finally:c.close()
            return self._group(cached['rows'],active,source,cached['success'])
        except (OSError,sqlite3.Error):return self._group(self.cache.get(key,{}).get('rows',{}),active,source)
    def _group(self,history,active,source,success=None):
        success=success or {}
        grouped={}
        for message in dict.fromkeys(active):
            title=display_message(message);row=history.get(message,{})
            g=grouped.setdefault(title,dict(id=source+':'+title,message=title,source=source,first_seen=None,last_seen=None,count=0,details=[]))
            g['details'].append(message);g['count']+=row.get('count',0)
            if row.get('first_seen'):g['first_seen']=min(g['first_seen'] or row['first_seen'],row['first_seen'])
            if row.get('last_seen'):g['last_seen']=max(g['last_seen'] or row['last_seen'],row['last_seen'])
        for g in grouped.values():
            proofs=[]
            for message in g['details']:
                proof=None
                if message.startswith('Valuation unavailable:'):proof=success.get('valuation')
                elif message.startswith('Strategy runtime error:'):
                    for query in ('history_orders','positions','orders'):
                        if 'Read-only query timeout: '+query in message:proof=success.get('broker_'+query);break
                    if proof is None and any(x in message for x in ('Disconnect','网络中断','NN_ProtoRet_ByDisConnOrCacnel','PacketErr.Timeout')):
                        stamps=[success.get('broker_'+x) for x in ('orders','history_orders','positions')]
                        if all(stamps):proof=min(stamps)
                last=history.get(message,{}).get('last_seen')
                if not proof or not last or proof<=last:break
                proofs.append(proof)
            g['resolved']=len(proofs)==len(g['details'])
            g['resolved_at']=max(proofs) if g['resolved'] else None
        return sorted(grouped.values(),key=lambda r:r['last_seen'] or '',reverse=True)
