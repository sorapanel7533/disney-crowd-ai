"""Read-only policy/connectivity probe; no database mutations."""
import json
import argparse
from pathlib import Path
import subprocess
import tempfile
import time
from official_source import OfficialClient,BASE
from official_source import USER_AGENT
import alcohol as a


def transport_probe():
    """Only the public robots policy; same honest identity, no denied retry."""
    results=[]
    with tempfile.TemporaryDirectory() as folder:
        for protocol in ('--http1.1','--http2'):
            time.sleep(2)
            path=Path(folder)/'robots.txt'
            try:
                process=subprocess.run(['curl',protocol,'--silent','--show-error','--user-agent',USER_AGENT,
                    '--connect-timeout','5','--max-time','18','--max-filesize','2097152',
                    '--output',str(path),'--write-out','%{json}',BASE+'/robots.txt'],capture_output=True,text=True,timeout=23)
                measured=json.loads(process.stdout)
                result={key:measured.get(key) for key in ('http_code','http_version','time_namelookup','time_connect','time_appconnect','time_starttransfer','time_total','remote_ip','ssl_verify_result')}
                body=path.read_text(encoding='utf-8') if path.exists() else ''
                result.update(protocol=protocol,exit_code=process.returncode,robots_shape='User-agent:' in body and '<html' not in body.lower())
                results.append(result)
                if measured.get('http_code') in (401,403,429):break
            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                results.append({'protocol':protocol,'error':type(exc).__name__})
    return results


def probe(check_transport=False):
    client=OfficialClient();result={}
    try:
        client.check_robots();client.check_terms()
        stores=a.parse_stores(client.get(BASE+'/tdl/restaurant/list/'),'tdl')
        products=a.parse_menu(client.get(stores[0]['menu_url']),stores[0])
        result={'source_access_success':True,'sample_store':stores[0]['name'],'sample_products':len(products)}
    except (RuntimeError,PermissionError,ValueError) as exc:
        result={'source_access_success':False,'reason':str(exc)}
    finally:
        result['source']=client.summary();client.close()
    if check_transport and not result['source_access_success'] and 'official_source_timeout' in result.get('reason',''):
        result['transport_probe']=transport_probe()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--transport-check',action='store_true')
    result=probe(parser.parse_args().transport_check);print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['source_access_success'] else 1)
