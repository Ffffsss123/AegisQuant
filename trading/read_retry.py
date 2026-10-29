"""Typed transient failures for explicitly read-only OpenD interfaces."""
from .policy import SafetyError

READ_ONLY={'accounts','positions','orders','history_orders','cash','fees','global','calendar','snapshot','quota','subscription'}

class ReadQueryTimeout(SafetyError):
    status='query_retry'
    def __init__(self,interface):
        self.interface=interface
        super().__init__('Read-only query timeout: '+interface)

def timeout_response(data):
    return str(data).strip()=='NN_ProtoRet_TimeOut'
