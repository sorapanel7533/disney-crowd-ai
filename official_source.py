"""Small, identified and bounded official HTTP client. No browser/proxy fallback."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urljoin,urlparse
from urllib.robotparser import RobotFileParser
import requests
from lxml import html

BASE='https://www.tokyodisneyresort.jp'
PRODUCT='DisneyCrowdAI'
USER_AGENT=PRODUCT+'/3.1'
POLICY_FILE=Path(__file__).with_name('data')/'official-source-policy.json'


def stamp():return datetime.now(timezone.utc).isoformat()


def legal_fingerprint(source):
    doc=html.fromstring(source);main=doc.xpath('//*[@id="area0"]')
    if not main or not main[0].xpath('.//h1[contains(.,"法的制約と使用条件")]') or not main[0].xpath('.//h2[contains(.,"コンテンツの使用制限")]'):
        raise ValueError('Invalid official terms page; survey stopped')
    canonical=unicodedata.normalize('NFKC',' '.join(main[0].text_content().split()))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def valid_source(url):
    u=urlparse(url)
    return (u.scheme=='https' and u.netloc=='www.tokyodisneyresort.jp' and not u.query and not u.fragment
        and bool(re.fullmatch(r'/(?:robots\.txt|legal\.html|td[ls]/restaurant/(?:list(?:/|\.html)|(?:food|detail)/\d+/)|food/\d+/)',u.path)))


class OfficialClient:
    def __init__(self,delay=2,retries=1,policy_file=POLICY_FILE):
        self.session=requests.Session()
        # The old long comment/URL format reproducibly stalled at the source.
        # Keep the same declared crawler identity, using one standard product.
        self.session.headers['User-Agent']=USER_AGENT
        self.delay=delay;self.retries=retries;self.policy_file=Path(policy_file)
        self.previous=0;self.calls=0;self.failed=0;self.consecutive_failures=0
        self.robot=None;self.denied=False;self.audit=[];self.policy={}

    def close(self):self.session.close()

    def _allowed(self,url,robots_redirect=False):
        if not valid_source(url):raise ValueError('Disallowed official source URL')
        if self.denied:raise PermissionError('Source access denied; further requests stopped')
        if self.consecutive_failures>=3:raise RuntimeError('Source circuit open after three consecutive failures')
        if self.calls>=100:raise RuntimeError('Weekly request limit reached')
        if self.robot:
            if not self.robot.can_fetch(PRODUCT,url):raise PermissionError('robots disallows source')
        elif url!=BASE+'/robots.txt' and not robots_redirect:
            raise PermissionError('robots must be verified before other sources')

    def _request(self,url):
        time.sleep(max(0,self.delay-(time.monotonic()-self.previous)))
        self.previous=time.monotonic();self.calls+=1;started=stamp();start=time.monotonic()
        entry={'path':urlparse(url).path,'started_at_utc':started}
        try:
            response=self.session.get(url,timeout=(5,15),allow_redirects=False)
            entry.update(http_status=response.status_code,bytes=len(response.content))
            if len(response.content)>2*1024*1024:raise ValueError('Official response exceeds size limit')
            if response.status_code in (401,403,429):
                self.denied=True
                entry['error']='access_denied_or_rate_limited'
                raise PermissionError('official_source_http_'+str(response.status_code)+': '+entry['path'])
            response.raise_for_status()
            response.encoding='utf-8';self.consecutive_failures=0
            return response
        except requests.RequestException as exc:
            self.failed+=1;self.consecutive_failures+=1
            entry['error']='timeout' if isinstance(exc,requests.Timeout) else 'http_'+str(getattr(getattr(exc,'response',None),'status_code','unknown')) if isinstance(exc,requests.HTTPError) else 'connection'
            raise
        except (ValueError,PermissionError):
            self.failed+=1;self.consecutive_failures+=1
            entry.setdefault('error','response_validation')
            raise
        finally:
            entry.update(finished_at_utc=stamp(),seconds=round(time.monotonic()-start,3));self.audit.append(entry)

    def get(self,url):
        current=url;redirects=0;retry=0;robots_redirect=False
        while True:
            self._allowed(current,robots_redirect)
            try:response=self._request(current)
            except requests.RequestException as exc:
                status=getattr(getattr(exc,'response',None),'status_code',None)
                temporary=isinstance(exc,(requests.Timeout,requests.ConnectionError)) or status in (500,502,503,504)
                if temporary and retry<self.retries and self.consecutive_failures<3:
                    retry+=1;continue
                reason='timeout' if isinstance(exc,requests.Timeout) else 'http_'+str(status) if status is not None else 'connection'
                raise RuntimeError('official_source_'+reason+': '+urlparse(current).path) from None
            if response.status_code in (301,302,307,308):
                target=urljoin(current,response.headers.get('Location',''))
                if redirects>=3 or not valid_source(target):raise ValueError('Unapproved source redirect')
                # robots redirects may precede policy loading, but stay within
                # this exact public official allowlist and never cross origins.
                robots_redirect=url==BASE+'/robots.txt'
                if not self.robot and not robots_redirect:raise PermissionError('robots not verified')
                current=target;redirects+=1;retry=0;continue
            if response.status_code!=200:raise ValueError('Unexpected official response status')
            return response.text

    def check_robots(self):
        source=self.get(BASE+'/robots.txt').lstrip('\ufeff')
        if '<html' in source.lower() or not re.search(r'^\s*user-agent\s*:',source,re.M|re.I):
            raise ValueError('Invalid robots response; survey stopped')
        # Never silently accept rule constructs unsupported by this parser.
        for line in source.splitlines():
            key,sep,value=line.partition(':');value=value.split('#',1)[0].strip()
            if sep and key.strip().lower()=='allow' and value:
                raise PermissionError('robots Allow rules require compatibility review')
            if sep and key.strip().lower()=='disallow' and any(c in value for c in '*$'):
                raise PermissionError('robots patterns require compatibility review')
            if sep and key.strip().lower()=='crawl-delay':
                try:requested_delay=float(value)
                except ValueError:raise PermissionError('Invalid source crawl delay') from None
                if not 0<=requested_delay<=30:raise PermissionError('Source crawl delay exceeds bounded survey budget')
                self.delay=max(self.delay,requested_delay)
        self.robot=RobotFileParser();self.robot.parse(source.splitlines())
        for park in ('tdl','tds'):
            if not self.robot.can_fetch(PRODUCT,BASE+f'/{park}/restaurant/list/'):
                raise PermissionError('robots disallows menu survey')
        delay=self.robot.crawl_delay(PRODUCT)
        if delay and delay>30:raise PermissionError('Source crawl delay exceeds bounded survey budget')
        self.delay=max(self.delay,delay or 0)
        rate=self.robot.request_rate(PRODUCT)
        if rate:
            if rate.requests<=0 or rate.seconds/rate.requests>30:raise PermissionError('Source request rate exceeds bounded survey budget')
            self.delay=max(self.delay,rate.seconds/rate.requests)
        self.policy['robots']={'url':BASE+'/robots.txt','sha256':hashlib.sha256(source.encode()).hexdigest(),'checked_at_utc':stamp()}

    def check_terms(self):
        source=self.get(BASE+'/legal.html');digest=legal_fingerprint(source)
        try:
            approved=json.loads(self.policy_file.read_text(encoding='utf-8'))
            expected=approved['legal_main_sha256'];scope=approved['scope']
        except (OSError,ValueError,KeyError):
            raise ValueError('Official terms review configuration unavailable') from None
        if digest!=expected:
            raise PermissionError('Official terms changed; review required before catalog update')
        self.policy['terms']={'url':BASE+'/legal.html','sha256':digest,'checked_at_utc':stamp(),'scope':scope}

    def summary(self):
        return {'user_agent':USER_AGENT,'requests':self.calls,'failed_requests':self.failed,
                'policy':self.policy,'request_log':self.audit}
