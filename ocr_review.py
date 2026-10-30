"""External history is quarantined until every accepted cell has independent review."""
from datetime import date
import hashlib
import json
from urllib.parse import urlparse
import re
import pandas as pd
import reliability as r


def image_manifest(page_html,page_url,park,target_date):
    from lxml import html
    from urllib.parse import urljoin
    target=str(pd.Timestamp(target_date).date()); compact=target.replace('-','')
    doc=html.fromstring(page_html)
    title=doc.xpath('string(//title)')
    if target.replace('-','/') not in title: raise ValueError('Page title date does not match requested date')
    prefix='lan' if park=='Disneyland' else 'sea'
    rows=[]
    for node in doc.xpath('//img[@src]'):
        url=urljoin(page_url,node.get('src'))
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='disneyreal.asumirai.info': continue
        if not re.fullmatch(rf'/realtime/images/{prefix}-wait-{compact}-\d+-\d+\.png',parsed.path): continue
        alt=node.get('alt','')
        if not all(x in alt for x in [str(pd.Timestamp(target).year)+'年',str(pd.Timestamp(target).month)+'月',str(pd.Timestamp(target).day)+'日']): continue
        rows.append({'park':park,'target_date':target,'page_url':page_url,'image_url':url,'alt':alt,
            'status':'quarantined','reason':'Column mapping, numeric cells and source rights require review'})
    return rows


def migrate(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS history_candidates (candidate_key TEXT PRIMARY KEY,park TEXT,
            target_date TEXT,source_url TEXT,image_sha256 TEXT,payload_json TEXT,status TEXT,reason TEXT,
            reviewed_at_utc TEXT,reviewer TEXT)''')


def quarantine(conn,rows,park,target_date,source_url,image_bytes=None):
    migrate(conn)
    digest=hashlib.sha256(image_bytes).hexdigest() if image_bytes else ''
    count=0
    with conn:
        for row in rows:
            payload=json.dumps(row,ensure_ascii=False,sort_keys=True,default=str)
            key=hashlib.sha256(f'{park}|{target_date}|{source_url}|{digest}|{payload}'.encode()).hexdigest()
            count+=conn.execute('INSERT OR IGNORE INTO history_candidates VALUES (?,?,?,?,?,?,?,?,NULL,NULL)',
                (key,park,str(target_date),source_url,digest,payload,'quarantined','Unreviewed external value; excluded from training')).rowcount
    return count


def validate_cell(row,target_date,allowed_names):
    text=str(row.get('text',row.get('wait_time',''))).strip()
    if not re.fullmatch(r'\d{1,3}',text): raise ValueError('Ambiguous/non-numeric OCR cell')
    value=int(text)
    if not 0<=value<=300: raise ValueError('Out-of-range wait')
    stamp=pd.Timestamp(row['datetime'])
    if stamp.date()!=pd.Timestamp(target_date).date(): raise ValueError('Wrong source date')
    if not 7<=stamp.hour<22 or stamp.second: raise ValueError('Invalid source time')
    if row.get('attraction') not in allowed_names: raise ValueError('Unmatched attraction name')
    if row.get('is_open') not in (1,True): raise ValueError('Unknown operating state cannot become an open wait')
    return stamp,value


def approve(conn,key,ground_truth,reviewer,authorized=False):
    if not authorized: raise PermissionError('Administrator authorization required')
    if not str(reviewer).strip(): raise ValueError('Reviewer required')
    migrate(conn)
    row=conn.execute('SELECT park,target_date,source_url,image_sha256,payload_json,status FROM history_candidates WHERE candidate_key=?',(key,)).fetchone()
    if not row: raise ValueError('Unknown candidate')
    if row[5]=='approved': return 0
    park,day,source,digest,payload,_=row; payload=json.loads(payload)
    names={x['name'] for x in r.load_catalog().values() if x.get('park')==park}
    # Older catalog has park_id; support it without guessing a facility.
    if not names: names={x['name'] for x in r.load_catalog().values() if x.get('park_id')==r.PARK_IDS[park]}
    stamp,value=validate_cell(payload,day,names)
    if not ground_truth.get('source_rights_checked') or not ground_truth.get('date_and_name_checked'):
        raise ValueError('Source rights/date/name review required')
    if str(ground_truth.get('datetime'))!=str(payload['datetime']) or ground_truth.get('attraction')!=payload['attraction'] or ground_truth.get('wait_time')!=value:
        raise ValueError('Independent transcription disagrees; keep quarantined')
    if digest and ground_truth.get('image_sha256')!=digest: raise ValueError('Reviewed image hash differs')
    now=r.iso(); observed=r.iso(stamp); observation_key=f'verified-external|{park}|{payload["attraction"]}|{observed}|{source}'
    with conn:
        conn.execute('INSERT OR IGNORE INTO wait_times (datetime,attraction,wait_time,is_open,park,source,observed_at_utc,fetched_at_utc,quality,observation_key) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (str(stamp),payload['attraction'],value,1,park,source,observed,now,'verified_external',observation_key))
        conn.execute("UPDATE history_candidates SET status='approved',reviewed_at_utc=?,reviewer=?,reason='Independent source transcription matched' WHERE candidate_key=?",(now,reviewer,key))
    return 1


def run_ocr(image_bytes):
    """Optional local research dependency. Tokens never become wait_times here."""
    try:
        from rapidocr import RapidOCR
        engine=RapidOCR(params={'EngineConfig.onnxruntime.intra_op_num_threads':1,'EngineConfig.onnxruntime.inter_op_num_threads':1})
        output=engine(image_bytes)
        tokens=[{'text':text,'confidence':float(conf),'box':box.tolist()} for text,conf,box in zip(
            output.txts if output.txts is not None else [],output.scores if output.scores is not None else [],output.boxes if output.boxes is not None else [])]
        return {'engine':'rapidocr optional research engine','tokens':tokens,'status':'quarantined','image_sha256':hashlib.sha256(image_bytes).hexdigest()}
    except (ImportError,RuntimeError,OSError) as exc:
        return {'engine':'rapidocr optional research engine','status':'unavailable','message':str(exc),'tokens':[]}
