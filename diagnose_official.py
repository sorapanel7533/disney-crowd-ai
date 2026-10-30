"""Small, honest source connectivity probe; no catalog or park DB mutations."""
import json
import platform
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from urllib.robotparser import RobotFileParser

BASE='https://www.tokyodisneyresort.jp'
AGENT='Mozilla/5.0 (compatible; DisneyCrowdAI/3.1; +https://github.com/sorapanel7533/disney-crowd-ai)'


def probe():
    result={'os':platform.system(),'python':platform.python_version(),'agent':AGENT,'requests':[]}
    policy=None
    for path in ('/robots.txt','/legal.html','/tdl/restaurant/food/300/'):
        if path!='/robots.txt' and (not policy or not policy.can_fetch('DisneyCrowdAI',BASE+path)):break
        if result['requests']:time.sleep(2)
        start=time.monotonic()
        try:
            req=Request(BASE+path,headers={'User-Agent':AGENT,'Accept':'text/plain' if path.endswith('.txt') else 'text/html','Accept-Language':'ja'})
            with urlopen(req,timeout=12) as response:
                content=response.read(2*1024*1024+1)
                entry={'path':path,'status':response.status,'bytes':len(content),'content_type':response.headers.get('Content-Type')}
                if path=='/robots.txt' and response.status==200 and len(content)<512*1024:
                    policy=RobotFileParser();policy.parse(content.decode('utf-8').splitlines())
                    entry['menu_allowed']=policy.can_fetch('DisneyCrowdAI',BASE+'/tdl/restaurant/list/')
        except HTTPError as exc:entry={'path':path,'error':'http_'+str(exc.code)}
        except (URLError,TimeoutError,OSError) as exc:entry={'path':path,'error':type(exc).__name__}
        entry['seconds']=round(time.monotonic()-start,3);result['requests'].append(entry)
    return result


if __name__=='__main__':print(json.dumps(probe(),ensure_ascii=False))
