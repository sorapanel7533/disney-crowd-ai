"""A small GitHub browser transport probe. Never touches production databases."""
import json,time
from alcohol_collector import OfficialClient
import alcohol as a


def probe(headed=False):
    client=OfficialClient(transport='browser-headed' if headed else 'browser');result={'transport':'ordinary_headed_chromium' if headed else 'ordinary_headless_chromium','agent':client.session.headers['User-Agent'],'requests':[]}
    try:
        start=time.monotonic();client.check_robots();result['requests'].append({'path':'/robots.txt','success':True,'seconds':round(time.monotonic()-start,3)})
        for path in ('/legal.html','/tdl/restaurant/food/300/'):
            start=time.monotonic();text=client.get(a.BASE+path)
            result['requests'].append({'path':path,'success':True,'bytes':len(text.encode()),'seconds':round(time.monotonic()-start,3)})
        result['source_access_success']=True
    except (RuntimeError,ValueError,PermissionError) as exc:
        result['source_access_success']=False;result['reason']=str(exc)
    finally:client.close()
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--headed',action='store_true');args=p.parse_args()
    print(json.dumps(probe(args.headed),ensure_ascii=False))
