"""Dedicated remote SQLite branch; optimistic transactions and verified readback.

Never writes park databases. No credential is sent to the browser or persisted.
"""
import base64
import hashlib
import os
import sqlite3
import time
import requests
import measured as m

REPO='sorapanel7533/disney-crowd-ai'
BRANCH='measured-data'
FILE='measured.db'
API='https://api.github.com/repos/'+REPO

class Unavailable(RuntimeError):pass
class Conflict(Unavailable):pass

class GitHubStore:
    def __init__(self,token='',session=None):
        self.token=token;self.session=session or requests.Session()
    def request(self,method,path,body=None,missing=False):
        headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
        if self.token:headers['Authorization']='Bearer '+self.token
        try:res=self.session.request(method,API+path,json=body,headers=headers,timeout=(5,20))
        except requests.RequestException:raise Unavailable('外部保存先へ接続できません。保存未完了です。') from None
        if res.status_code==404 and missing:return None
        if res.status_code in (409,422):raise Conflict('同時更新を検出しました')
        if res.status_code not in (200,201):raise Unavailable('外部保存先を利用できません（HTTP '+str(res.status_code)+'）。保存未完了です。')
        return res.json()
    def load(self):
        ref=self.request('GET','/git/ref/heads/'+BRANCH,missing=True)
        if ref is None:return m.connect(),None,None
        head=ref['object']['sha'];commit=self.request('GET','/git/commits/'+head)
        tree=commit['tree']['sha'];entries=self.request('GET','/git/trees/'+tree)['tree']
        blob=next((x['sha'] for x in entries if x['path']==FILE and x['type']=='blob'),None)
        if not blob:raise Unavailable('外部DBが欠落しています。自動初期化せず停止しました。')
        data=self.request('GET','/git/blobs/'+blob)
        if data.get('size',0)>10*1024*1024:raise Unavailable('保存容量の確認が必要です')
        try:
            raw=base64.b64decode(data['content']);db=m.connect();db.deserialize(raw)
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError()
            db.execute('SELECT id,payload FROM measurements LIMIT 1')
        except (ValueError,sqlite3.Error,KeyError):raise Unavailable('外部DBの整合性を確認できません。保存未完了です。') from None
        return db,head,tree
    def commit(self,db,head,tree):
        raw=db.serialize()
        if len(raw)>10*1024*1024:raise Unavailable('保存容量の確認が必要です。保存未完了です。')
        expected=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        blob=self.request('POST','/git/blobs',{'encoding':'base64','content':base64.b64encode(raw).decode()})
        if blob['sha']!=expected:raise Unavailable('保存内容の照合に失敗しました')
        body={'tree':[{'path':FILE,'mode':'100644','type':'blob','sha':expected}]}
        # The transaction journal makes the SQLite snapshot independently recoverable.
        journal={'format':'measured-snapshot-v1','sqlite_blob':expected,
            'measurements':m.records(db),
            'audit':[dict(zip(('id','record_id','at','payload'),x)) for x in db.execute('SELECT * FROM measurement_audit')],
            'models':[dict(zip(('id','payload'),x)) for x in db.execute('SELECT * FROM measured_models')]}
        import gzip,json
        delta=gzip.compress(json.dumps(journal,ensure_ascii=False).encode(),mtime=0)
        checkpoint=self.request('POST','/git/blobs',{'encoding':'base64','content':base64.b64encode(delta).decode()})
        checkpoint_sha=hashlib.sha1(b'blob '+str(len(delta)).encode()+b'\0'+delta).hexdigest()
        if checkpoint['sha']!=checkpoint_sha:raise Unavailable('復元用データの照合に失敗しました')
        body['tree'].append({'path':'checkpoints/'+expected+'.json.gz','mode':'100644','type':'blob','sha':checkpoint_sha})
        if tree:body['base_tree']=tree
        newtree=self.request('POST','/git/trees',body)
        commit=self.request('POST','/git/commits',{'message':'Persist anonymous actual-wait transaction','tree':newtree['sha'],'parents':[head] if head else []})
        if head:
            # Non-force rejects a sibling commit if another writer advanced first.
            self.request('PATCH','/git/refs/heads/'+BRANCH,{'sha':commit['sha'],'force':False})
        else:self.request('POST','/git/refs',{'ref':'refs/heads/'+BRANCH,'sha':commit['sha']})
        return commit['sha']
    def transact(self,operation,verify):
        if not self.token:raise Unavailable('投稿の外部保存設定が未完了です。保存されていません。')
        for attempt in range(4):
            db,head,tree=self.load()
            try:
                result=operation(db)
                commit=self.commit(db,head,tree)
            except Conflict:
                time.sleep(.2*(attempt+1));continue
            finally:db.close()
            readback,_,_=self.load()
            try:
                if not verify(readback,result):raise Unavailable('保存後の確認ができませんでした。記録IDで再確認してください。')
            finally:readback.close()
            return result,commit
        raise Unavailable('同時投稿が集中しています。同じ記録IDで再試行してください。')
    def submit(self,row):
        return self.transact(lambda db:m.save(db,row),lambda db,x:any(y==x for y in m.records(db)))
    def finish(self,record_id,receipt,end):
        return self.transact(lambda db:m.finish(db,record_id,receipt,end),lambda db,x:any(y==x for y in m.records(db)))
    def moderate(self,record_id,status,**kwargs):
        if not kwargs.get('authorized'):raise PermissionError('管理者認証が必要です')
        return self.transact(lambda db:m.moderate(db,record_id,status,**kwargs),lambda db,x:any(y==x for y in m.records(db)))

def configured(st=None):
    token=os.environ.get('DISNEY_MEASURED_GITHUB_TOKEN','')
    if st is not None and not token:
        try:token=st.secrets.get('DISNEY_MEASURED_GITHUB_TOKEN','')
        except Exception:pass
    return GitHubStore(token)
