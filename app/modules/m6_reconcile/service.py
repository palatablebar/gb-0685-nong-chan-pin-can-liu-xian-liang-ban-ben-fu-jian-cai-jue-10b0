from app.modules.m6_reconcile.compare import balance
from app.modules.m6_reconcile.adjudicate import finalize

def execute(state):
    state['reconcile']=balance(state)
    state['adjudication']=state['reconcile']['adjudication']
    finalize(state)
    state['reconcile']['certificate']=state['adjudication']['certificate']
    return state
