"""Read-only policy/connectivity probe; no database mutations."""
import json
from official_source import OfficialClient,BASE
import alcohol as a


def probe():
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
    return result


if __name__=='__main__':
    result=probe();print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['source_access_success'] else 1)
