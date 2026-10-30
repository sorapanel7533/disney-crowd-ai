"""Official menu facts only. No images, copied descriptions, inventory, or guessed dates."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urljoin, urlparse
from lxml import html
import reliability as r

BASE='https://www.tokyodisneyresort.jp'
PARKS={'tdl':'Disneyland','tds':'DisneySea'}
TABLES={'alcohol_products','alcohol_stores','alcohol_sales','alcohol_periods','alcohol_prices',
        'alcohol_runs','alcohol_sources','alcohol_diffs','alcohol_overrides'}


def text(node):
    return ' '.join(node.text_content().split())


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def official_url(value):
    parsed=urlparse(urljoin(BASE,value))
    if parsed.scheme!='https' or parsed.netloc!='www.tokyodisneyresort.jp' or not re.fullmatch(r'/(?:td[ls]/restaurant/(?:food|detail)/\d+/|food/\d+/)',parsed.path):
        raise ValueError('Official park menu/store URL required')
    return BASE+parsed.path


def category(name):
    for pattern,label in [(r'ビール|ビア|ピルスナー|ペールエール|ラガー','ビール'),(r'カクテル|サワー|ハイボール','カクテル'),
                          (r'ワイン|シャンパ[ーニュン]','ワイン'),(r'ウイスキー|ウィスキー|バーボン','ウイスキー'),
                          (r'日本酒|清酒','日本酒'),(r'焼酎|梅酒','焼酎・梅酒'),(r'ブランデー','ブランデー')]:
        if re.search(pattern,name): return label
    return 'その他・種類未確認'


def dates(value):
    # A date without a range is never invented as an end date.
    found=re.findall(r'(20\d{2})年(\d{1,2})月(\d{1,2})日',value)
    import datetime
    parsed=[datetime.date(*map(int,x)).isoformat() for x in found]
    if len(parsed)>2: raise ValueError('Ambiguous official sales period')
    return (parsed[0] if parsed else None,parsed[1] if len(parsed)==2 else None)


def period_state(start,end,day=None,suspensions=()):
    day=str(day or r.utc().tz_convert(r.JST).date())
    if start and day<start: return '販売開始前'
    if end and day>end: return '期間終了'
    if any(p['start']<=day<=p['end'] for p in suspensions): return '公式掲載上の販売休止期間'
    if start or end: return '公式掲載上の販売期間内'
    return '販売期間未確認'


def parse_stores(source,park):
    if park not in PARKS: raise ValueError('Unknown park')
    doc=html.fromstring(source); stores=[]
    # The public list filter uses tag 6006 (alcohol service), not menu keywords.
    if not doc.xpath('//label[@for="searchTag6006"]'): raise ValueError('Alcohol list filter structure changed')
    for li in doc.xpath('//li[@data-tags]'):
        if '6006' not in json.loads(li.get('data-tags')): continue
        links=li.xpath(f'.//a[starts-with(@href,"/{park}/restaurant/detail/")]')
        if not links: continue
        link=links[0]; names=link.xpath('.//h3'); areas=link.xpath('.//p[@class="area"]')
        if not names or not areas: raise ValueError('Store name/area structure changed')
        if text(areas[0])=='パーク外': continue
        href=official_url(link.get('href')); sid=re.search(r'/detail/(\d+)/',href)[1]
        menus=li.xpath(f'.//a[starts-with(@href,"/{park}/restaurant/food/")]')
        stores.append({'store_id':f'{park}:{sid}','park':PARKS[park],'name':text(names[0]).replace(' NEW',''),
                       'area':text(areas[0]),'url':href,'menu_url':official_url(menus[0].get('href')) if menus else None})
    if not stores: raise ValueError('No alcohol-service stores parsed')
    return stores


def parse_menu(source,store):
    doc=html.fromstring(source)
    headings=doc.xpath('//h1')
    if not headings or unicodedata.normalize('NFKC',text(headings[0]))!=unicodedata.normalize('NFKC',store['name']):
        raise ValueError('Menu/store identity mismatch')
    if not doc.xpath('//*[contains(@class,"section")]'): raise ValueError('Menu section structure changed')
    rows=[]
    alcohol_headings={'アルコールドリンク','ビール','カクテル','ウイスキー','ウィスキー','ブランデー','赤ワイン','白ワイン','ロゼワイン','スパークリングワイン','日本酒','梅酒','焼酎','おすすめドリンク（アルコール）'}
    def add(name,price,period,product_id,url,context):
        if re.search(r'ノン[・\s-]?アルコール|アルコールを含まない|アルコール分\s*0|0\.0%',name+' '+context): return
        main=period.split('当メニュー')[0]
        start,end=dates(main)
        suspensions=[]
        if '販売休止' in period:
            pause=dates(period.split('当メニュー')[-1])
            if not all(pause): raise ValueError('Ambiguous suspension period')
            suspensions=[{'start':pause[0],'end':pause[1]}]
        if start and end and start>end: raise ValueError('Invalid menu period')
        nums=re.findall(r'[¥￥]\s*([\d,]+)',price)
        amounts=[int(n.replace(',','')) for n in nums]
        if any(n<100 or n>100000 for n in amounts): raise ValueError('Invalid published price')
        kind=category(name)
        if kind=='その他・種類未確認': kind=category(context)
        rows.append({'product_id':product_id,'name':name,'kind':kind,'store_id':store['store_id'],
                     'url':official_url(url),'price':amounts[0] if len(amounts)==1 else None,
                     'price_text':price if nums else None,'tax_included':bool(re.search('税込',text(doc))),
                     'start':start,'end':end,'suspensions':suspensions,'limited':bool(start and end),
                     'verification':'official_menu','source_sha256':hashlib.sha256(source.encode('utf-8')).hexdigest(),
                     'notes':'商品名・価格等の事実を整理。提供状況は公式ページでご確認ください。'})
    for a in doc.xpath('//a[starts-with(@href,"/food/")]'):
        names=a.xpath('.//h3'); badges=a.xpath('.//*[contains(@class,"badgeAlcohol")]')
        section=a.xpath('ancestor::*[contains(concat(" ",normalize-space(@class)," ")," section ")][1]/h2')
        heading=text(section[0]) if section else ''
        if not names or not (badges or heading in alcohol_headings): continue
        name=text(names[0]); price=' / '.join(text(p) for p in a.xpath('.//p[@class="text"]') if '¥' in text(p) or '￥' in text(p))
        period=' '.join(text(x) for x in a.xpath('.//*[contains(@class,"definitionList")]'))
        fid=re.fullmatch(r'/food/(\d+)/',a.get('href'))
        if fid: add(name,price,period,'food:'+fid[1],a.get('href'),heading+' '+text(a))
    for section in doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," section ")]'):
        heads=section.xpath('./h2')
        if not heads or text(heads[0]) not in alcohol_headings: continue
        for row in section.xpath('./table//tr'):
            names=row.xpath('./th'); prices=row.xpath('./td')
            if not names or not prices: raise ValueError('Alcohol price table structure changed')
            name=text(names[0]); key='table:'+store['store_id']+':'+digest(unicodedata.normalize('NFKC',name))
            add(name,' / '.join(text(p) for p in prices),'',key,store['menu_url'],text(heads[0])+' '+text(row))
    # A valid empty menu means details unconfirmed, never an invented drink.
    return list({(x['product_id'],x['store_id']):x for x in rows}.values())


def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path,timeout=30,check_same_thread=False)
    conn.execute('PRAGMA busy_timeout=30000')
    migrate(conn)
    return conn


def migrate(conn):
    # Same transport envelope across tables keeps journals/recovery schema checked.
    with conn:
        for table in sorted(TABLES):
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (record_key TEXT PRIMARY KEY,updated_at_utc TEXT NOT NULL,payload_json TEXT NOT NULL)')


def records(conn,table):
    if table not in TABLES: raise ValueError('Unknown table')
    return {k:json.loads(payload) for k,payload in conn.execute(f'SELECT record_key,payload_json FROM {table}')}


def put(conn,table,key,value,now):
    if table not in TABLES: raise ValueError('Unknown table')
    conn.execute(f'INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(record_key) DO UPDATE SET updated_at_utc=excluded.updated_at_utc,payload_json=excluded.payload_json WHERE excluded.updated_at_utc>={table}.updated_at_utc',
                 (key,now,json.dumps(value,ensure_ascii=False,sort_keys=True)))


def last_success(conn):
    runs=records(conn,'alcohol_runs').values()
    return max((x for x in runs if x['status']=='success'),key=lambda x:x['finished_at_utc'],default=None)


def apply_survey(conn,stores,menus,failures,now=None,run_id=None,force=False):
    """Atomic successful-store updates; failures and abnormal shrinkage retain last good."""
    now=r.iso(now); local=r.utc(now).tz_convert(r.JST); week=local.strftime('%G-W%V')
    run_id=run_id or 'weekly:'+week
    existing=records(conn,'alcohol_runs').get(run_id)
    if existing and existing['status']=='success' and not force: return existing
    oldsales=records(conn,'alcohol_sales'); previous=records(conn,'alcohol_stores')
    products=records(conn,'alcohol_products'); diffs=[]; quarantined=[]; accepted=[]
    with conn:
        for store in stores:
            sid=store['store_id']; rows=menus.get(sid)
            prior={k:x for k,x in oldsales.items() if x['store_id']==sid and x.get('listed',True)}
            incoming={x['product_id']+'|'+sid:x for x in rows or []}
            # Any disappearance exceeding 30% (including an empty parse) needs review.
            shrink=rows is not None and bool(prior) and len(incoming)<len(prior)*.7
            if shrink: quarantined.append(sid)
            state='quarantined_drop' if shrink else 'failed' if rows is None else 'success'
            put(conn,'alcohol_sources',run_id+'|'+sid,{'run_id':run_id,'store_id':sid,'url':store['menu_url'],
                'checked_at_utc':now,'status':state,'reason':failures.get(sid,'abnormal product count decrease' if shrink else ''),
                'parsed_products':len(incoming)},now)
            # Preserve store facts even when its product page failed.
            put(conn,'alcohol_stores',sid,{**store,'checked_at_utc':now,'product_details':state,
                 'last_menu_success_utc':now if state=='success' else previous.get(sid,{}).get('last_menu_success_utc')},now)
            if state!='success': continue
            accepted.append(sid)
            for key,row in incoming.items():
                pid=row['product_id']; old=oldsales.get(key); first=products.get(pid,{}).get('first_seen_utc',now)
                put(conn,'alcohol_products',pid,{k:row[k] for k in ('product_id','name','kind','url','verification','notes')}|{'first_seen_utc':first,'checked_at_utc':now},now)
                sale={**row,'checked_at_utc':now,'listed':True,'first_seen_utc':old.get('first_seen_utc',now) if old else now}
                put(conn,'alcohol_sales',key,sale,now)
                for table,fields in [('alcohol_periods',('start','end','suspensions')),('alcohol_prices',('price','price_text','tax_included'))]:
                    value={field:row[field] for field in fields}
                    if old is None or any(old.get(field)!=row[field] for field in fields):
                        put(conn,table,key+'|'+digest(json.dumps(value,sort_keys=True)),{'sale_key':key,'recorded_at_utc':now,**value},now)
                kinds=[]
                if pid not in products: kinds.append('product_added')
                if not old: kinds.append('store_added')
                if old and old.get('price_text')!=row.get('price_text'): kinds.append('price_changed')
                if old and any(old.get(f)!=row.get(f) for f in ('start','end','suspensions')): kinds.append('period_changed')
                state_now=period_state(row['start'],row['end'],local.date(),row.get('suspensions',[]))
                if state_now=='販売開始前' and not old: kinds.append('future_product')
                if state_now=='期間終了' and (not old or period_state(old.get('start'),old.get('end'),r.utc(old.get('checked_at_utc',now)).tz_convert(r.JST).date())!='期間終了'): kinds.append('period_ended')
                for kind in kinds: diffs.append({'kind':kind,'sale_key':key,'product_id':pid,'store_id':sid,'before':old,'after':sale,'detected_at_utc':now})
            for key,old in prior.items():
                if key not in incoming:
                    # Listing removed is distinct from a dated sales end.
                    put(conn,'alcohol_sales',key,{**old,'listed':False,'listing_removed_at_utc':now},now)
                    diffs.append({'kind':'store_removed','sale_key':key,'product_id':old['product_id'],'store_id':sid,'detected_at_utc':now})
        for i,diff in enumerate(diffs): put(conn,'alcohol_diffs',run_id+'|'+str(i)+'|'+digest(json.dumps(diff,sort_keys=True)),diff,now)
        allrows=[x for sid in accepted for x in menus[sid]]
        status='success' if len(accepted)==len(stores) and not failures else 'partial' if accepted else 'failed'
        result={'run_id':run_id,'week':week,'status':status,'finished_at_utc':now,'target_stores':len(stores),
                'successful_stores':len(accepted),'stores_with_products':sum(bool(menus[sid]) for sid in accepted),
                'products':len({x['product_id'] for x in allrows}),'priced_products':len({x['product_id'] for x in allrows if x['price_text']}),
                'dated_products':len({x['product_id'] for x in allrows if x['start'] or x['end']}),
                'unconfirmed_stores':len(stores)-sum(bool(menus[sid]) for sid in accepted),
                'quarantined_stores':quarantined,'failures':failures,'diffs':len(diffs)}
        put(conn,'alcohol_runs',run_id,result,now)
    return result


def failure(conn,reason,now=None,run_id=None):
    now=r.iso(now);run_id=run_id or now
    value={'run_id':run_id,'status':'failed','finished_at_utc':now,'reason':reason}
    with conn: put(conn,'alcohol_runs',run_id,value,now)
    return value


def freshness(conn,now=None):
    now=r.utc(now);good=last_success(conn)
    age=(now-r.utc(good['finished_at_utc'])).total_seconds()/86400 if good else None
    local=now.tz_convert(r.JST);next_day=local.normalize()+__import__('pandas').Timedelta(days=(7-local.weekday())%7,hours=6)
    if next_day<=local: next_day+=__import__('pandas').Timedelta(days=7)
    latest=max(records(conn,'alcohol_runs').values(),key=lambda x:x['finished_at_utc'],default=None)
    week=local.strftime('%G-W%V')
    automatic=[x for key,x in records(conn,'alcohol_runs').items() if key.startswith('weekly:'+week)]
    last_auto=max(automatic,key=lambda x:x['finished_at_utc'],default=None)
    retry=local.normalize()+__import__('pandas').Timedelta(hours=6,minutes=17)
    if retry<=local:retry+=__import__('pandas').Timedelta(days=1)
    return {'last_success_utc':good['finished_at_utc'] if good else None,'age_days':age,
            'level':'unavailable' if age is None else 'warning' if age>=14 else 'attention' if age>=8 else 'fresh',
            'next_scheduled_jst':next_day.isoformat(),'latest_run':latest,
            'catch_up_scheduled_jst':retry.isoformat() if not last_auto or last_auto['status']!='success' else None,
            'note':'GitHub Actionsの起動遅延・欠落があるため予定時刻です。'}


def catalog(conn,now=None):
    stores=records(conn,'alcohol_stores'); products=records(conn,'alcohol_products')
    # Admin annotations are a separate overlay. Crawls never write this table.
    overrides=records(conn,'alcohol_overrides'); output=[]
    for key,sale in records(conn,'alcohol_sales').items():
        product=products[sale['product_id']]; store=stores[sale['store_id']]
        row={**product,**sale,'park':store['park'],'area':store['area'],'store_name':store['name'],'store_url':store['url'],'sale_key':key}
        row['state']=period_state(row['start'],row['end'],r.utc(now).tz_convert(r.JST).date(),row.get('suspensions',[])) if row.get('listed',True) else '公式掲載から削除・販売状況未確認'
        row['new']=(r.utc(now)-r.utc(product['first_seen_utc'])).total_seconds()<7*86400
        row['admin_note']=overrides.get(key,{}).get('note')
        output.append(row)
    return output


def filter_catalog(rows,query='',parks=(),areas=(),stores=(),kinds=(),max_price=None,limited=False,within=False,new=False,exclude_beer=False):
    return [x for x in rows if (not query or query.casefold() in x['name'].casefold()) and (not parks or x['park'] in parks)
            and (not areas or x['area'] in areas) and (not stores or x['store_id'] in stores) and (not kinds or x['kind'] in kinds)
            and (max_price is None or x['price'] is not None and x['price']<=max_price)
            and (not limited or x['limited']) and (not within or x['state']=='公式掲載上の販売期間内') and (not new or x['new'])
            and (not exclude_beer or x['kind']!='ビール')]


def annotate(conn,sale_key,note,authorized=False,now=None):
    if not authorized: raise PermissionError('Administrator authentication required')
    if sale_key not in records(conn,'alcohol_sales'): raise ValueError('Unknown sale')
    if not isinstance(note,str) or len(note)>500: raise ValueError('Annotation must be at most 500 characters')
    with conn: put(conn,'alcohol_overrides',sale_key,{'note':note,'source':'administrator','reviewed_at_utc':r.iso(now)},r.iso(now))


def bootstrap(conn):
    seed=Path(__file__).with_name('data')/'alcohol-bootstrap.json'
    if not records(conn,'alcohol_runs') and seed.exists():
        data=json.loads(seed.read_text(encoding='utf-8'))
        return apply_survey(conn,data['stores'],data['menus'],data['failures'],data['checked_at_utc'],data['run_id'])
