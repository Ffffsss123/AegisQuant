"""Project-local decoding of exchange timestamps and a connection-scoped book cache."""
import math
import threading
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .policy import SafetyError

class QuoteUnavailable(SafetyError):
    def __init__(self,message,status='waiting_quote'):
        super().__init__(message);self.status=status

def exchange_time(raw,side,market='US'):
    numeric='svrRecvTime'+side+'Timestamp';text='svrRecvTime'+side
    if raw.HasField(numeric):
        value=getattr(raw,numeric)
        if math.isfinite(value) and value>0:
            try:return datetime.fromtimestamp(value,timezone.utc).isoformat()
            except (ValueError,OverflowError,OSError):pass
    try:
        value=getattr(raw,text) if raw.HasField(text) else ''
        stamp=datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            zone={'US':'America/New_York','HK':'Asia/Hong_Kong'}.get(market)
            if zone is None:raise ValueError('Unverified market timezone')
            stamp=stamp.replace(tzinfo=ZoneInfo(zone))
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError):
        raise QuoteUnavailable('Order book timestamp unavailable: '+side+'; freshness cannot be verified')

def decode_book(pb):
    # Reuse SDK price decoding locally, retaining fields its Python output omits.
    from moomoo.quote.quote_query import OrderBookQuery
    ret,msg,data=OrderBookQuery.unpack_rsp(pb)
    if ret!=0:raise QuoteUnavailable(str(msg))
    if not data['Bid'] or not data['Ask']:raise QuoteUnavailable('Missing two-sided order book')
    if data.get('order_book_type','NORMAL')!='NORMAL':raise QuoteUnavailable('Unexpected order book type')
    market=data['code'].split('.',1)[0]
    return data['code'],dict(bid=data['Bid'][0][0],ask=data['Ask'][0][0],
        bid_time=exchange_time(pb.s2c,'Bid',market),ask_time=exchange_time(pb.s2c,'Ask',market))

class BookCache:
    def __init__(self):self.lock=threading.Lock();self.connection=None;self.books={}
    def reset(self,connection=None):
        with self.lock:self.connection=connection;self.books.clear()
    def current(self):
        with self.lock:return self.connection
    def publish(self,connection,code,book):
        with self.lock:
            if connection==self.connection and connection is not None:self.books[code]=book
    def get(self,connection,code,now):
        with self.lock:
            if not connection or self.connection!=connection:
                raise QuoteUnavailable('Quote connection interrupted','connection_interrupted')
            value=self.books.get(code)
            if isinstance(value,QuoteUnavailable):raise value
            if value is None:raise QuoteUnavailable('Waiting for timestamped order book: '+code)
            book=dict(value)
        for side in ('bid_time','ask_time'):
            age=(now-datetime.fromisoformat(book[side])).total_seconds()
            if not 0<=age<=30:raise QuoteUnavailable('Stale/future order book: '+code,'quote_expired')
        return book

def quote_context(sdk,host,port):
    from moomoo.common.constant import ProtoId
    from moomoo.common.network_manager import PacketErr
    from moomoo.common.utils import merge_qot_mkt_stock_str

    class TimestampContext(sdk.OpenQuoteContext):
        def __init__(self):
            self.book_cache=BookCache()
            super().__init__(host=host,port=port)
        def on_api_socket_reconnected(self):
            self.book_cache.reset(self._conn_id)
            return super().on_api_socket_reconnected()
        def on_disconnect(self,conn_id,reason,msg):
            self.book_cache.reset()
            return super().on_disconnect(conn_id,reason,msg)
        def handle_push(self,conn_id,proto_id,serial_no,rsp_pb,err,msg):
            if proto_id==ProtoId.Qot_UpdateOrderBook and err==PacketErr.Ok:
                # Decode before the SDK callback queue: old queued callbacks cannot
                # repopulate the cache after disconnect/reconnect.
                code=merge_qot_mkt_stock_str(rsp_pb.s2c.security.market,rsp_pb.s2c.security.code)
                try:_,value=decode_book(rsp_pb)
                except QuoteUnavailable as exc:value=exc
                except Exception as exc:value=QuoteUnavailable('Invalid order book: '+str(exc))
                self.book_cache.publish(conn_id,code,value)
                return
            return super().handle_push(conn_id,proto_id,serial_no,rsp_pb,err,msg)
    return TimestampContext()
