"""Owner-dispatched, acknowledged annotations. No passwords or tokens in payloads."""
import uuid
import alcohol as a
import reliability as r


def validate(payload):
    if set(payload)!={'event_id','sale_key','note','created_at_utc'}: raise ValueError('Unexpected admin fields')
    uuid.UUID(payload['event_id'])
    if not isinstance(payload['note'],str) or len(payload['note'])>500: raise ValueError('Invalid note')
    if not isinstance(payload['sale_key'],str) or len(payload['sale_key'])>200: raise ValueError('Invalid sale key')
    age=(r.utc()-r.utc(payload['created_at_utc'])).total_seconds()
    if age>86400*7 or age< -300: raise ValueError('Expired/future admin event')
    return payload


def export(sale_key,note,authorized=False):
    if not authorized: raise PermissionError('Administrator authentication required')
    return validate({'event_id':str(uuid.uuid4()),'sale_key':sale_key,'note':note,'created_at_utc':r.iso()})


def apply(conn,payload):
    validate(payload)
    prior=a.records(conn,'alcohol_overrides').get(payload['sale_key'],{})
    if prior.get('event_id')==payload['event_id']: return False
    a.annotate(conn,payload['sale_key'],payload['note'],authorized=True)
    value=a.records(conn,'alcohol_overrides')[payload['sale_key']]
    with conn:a.put(conn,'alcohol_overrides',payload['sale_key'],{**value,'event_id':payload['event_id'],'acknowledged_at_utc':r.iso()},r.iso())
    return True
