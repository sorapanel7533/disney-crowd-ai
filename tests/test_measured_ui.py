from datetime import date,time
from pathlib import Path
import pandas as pd
from streamlit.testing.v1 import AppTest
import measured_ui
import measured as m
import pytest
from test_measured import SharedRemote,RemoteClient

CODE="import streamlit as st\nfrom measured_ui import render\nrender(st,'Disneyland',False)"


def button(app,label):
    return next(item for item in app.button if item.label==label)


def assert_clean_timer(app):
    assert not app.exception
    assert not app.date_input and not app.time_input
    assert all('動作確認' not in item.label for item in app.checkbox)
    assert all('コミット' not in item.value and '記録ID' not in item.value for item in app.caption)

@pytest.mark.parametrize('method',['開始・乗車時刻から計算','待ち時間を直接入力'])
def test_guest_post_is_persisted_and_visible_in_separate_session(monkeypatch,method):
    remote=SharedRemote()
    monkeypatch.setattr(measured_ui,'configured',lambda st:RemoteClient(remote))
    monkeypatch.setattr(measured_ui,'shared',lambda st:RemoteClient(remote).load()[:2])
    monkeypatch.setattr(measured_ui,'history_for',lambda park:pd.DataFrame())
    a=AppTest.from_string(CODE,default_timeout=25).run()
    assert_clean_timer(a)
    a.radio(key='mw_mode').set_value('過去の記録を手動入力').run()
    assert len(a.date_input)==1
    assert not any(x.label in ('今から並ぶ','今、乗車した') for x in a.button)
    [x for x in a.radio if x.label=='入力方法'][0].set_value(method).run()
    a.date_input(key='mw_day').set_value(date(2026,11,1))
    a.time_input(key='mw_start').set_value(time(10,20))
    if method=='開始・乗車時刻から計算':
        a.time_input(key='mw_end_time').set_value(time(11,5))
    else:
        next(x for x in a.number_input if '実際に並んだ時間' in x.label).set_value(45)
    [x for x in a.checkbox if '現地の案内' in x.label][0].check().run()
    [x for x in a.number_input if '並び始めた時の案内表示' in x.label][0].set_value(70)
    button(a,'実測を投稿する').click().run()
    assert not a.exception,[(x.message,x.stack_trace) for x in a.exception]
    assert any('保存完了' in x.value for x in a.success)
    db,_,_=RemoteClient(remote).load();rows=m.records(db)
    assert len(rows)==1 and rows[0]['actual_wait']==45 and rows[0]['displayed_wait']==70
    assert rows[0]['status']=='pending' and not rows[0]['is_test']
    b=AppTest.from_string(CODE,default_timeout=25).run()
    assert b.query_params!=a.query_params
    [x for x in b.radio if x.label=='集計対象'][0].set_value('未承認を含む参考集計').run()
    assert not b.exception,[(x.message,x.stack_trace) for x in b.exception]
    assert any('1' in x.value and '件 / 表示値' in x.value for x in b.markdown)
    assert not m.frame(db).shape[0]


def test_timer_timestamps_survive_io_retry_and_restart(monkeypatch):
    import reliability as r
    import utils
    from measured_store import Unavailable

    remote=SharedRemote()
    clock=pd.Timestamp('2026-11-12T10:20:00+09:00')
    original_utc=r.utc
    monkeypatch.setattr(r,'utc',lambda value=None:original_utc(clock if value is None else value))
    retry=True

    class TimerClient(RemoteClient):
        def submit(self,row):
            nonlocal retry
            if retry:
                retry=False
                raise Unavailable('temporary save failure')
            return super().submit(row)

    def load(st):
        nonlocal clock
        clock+=pd.Timedelta(seconds=4)
        return TimerClient(remote).load()[:2]

    def waits(settings):
        nonlocal clock
        clock+=pd.Timedelta(seconds=9)
        frame=pd.DataFrame([{'RideID':7987,'UpdatedUTC':'2026-11-12T10:19:00+09:00',
                            'FetchedUTC':r.iso(),'Quality':'valid','Open':True,'Wait':70}])
        return frame,frame

    monkeypatch.setattr(measured_ui,'configured',lambda st:TimerClient(remote))
    monkeypatch.setattr(measured_ui,'shared',load)
    monkeypatch.setattr(utils,'fetch_wait_times',waits)
    a=AppTest.from_string(CODE,default_timeout=25).run()
    a.selectbox(key='mw_timer_ride_Disneyland').set_value(7987)
    clock=pd.Timestamp('2026-11-12T10:20:00+09:00')
    button(a,'今から並ぶ').click().run()
    assert a.error and not a.success
    assert not m.records(TimerClient(remote).load()[0])
    clock+=pd.Timedelta(minutes=2)
    button(a,'今から並ぶ').click().run()
    assert_clean_timer(a)
    db,_,_=TimerClient(remote).load()
    draft=m.records(db)[0]
    assert draft['status']=='draft' and draft['displayed_wait']==70
    assert draft['started_at']=='2026-11-12T01:20:00+00:00'
    assert draft['display_fetched_at']=='2026-11-12T01:20:13+00:00'
    # New full-app session restores the park and record from the saved URL.
    b=AppTest.from_file(str(Path(measured_ui.__file__).with_name('app.py')),default_timeout=25)
    b.query_params.update(a.query_params)
    b.run()
    assert_clean_timer(b)
    assert b.session_state['park']=='Disneyland'
    assert any('10:20:00' in item.value for item in b.markdown)
    clock=pd.Timestamp('2026-11-12T11:05:00+09:00')
    button(b,'今、乗車した').click().run()
    assert_clean_timer(b)
    assert any(item.value=='保存完了' for item in b.success)
    db,_,_=TimerClient(remote).load()
    saved=m.records(db)[0]
    assert saved['started_at']==draft['started_at']
    assert saved['boarded_at']=='2026-11-12T02:05:00+00:00'
    assert saved['actual_wait']==45 and saved['deviation']==-25
    assert saved['deviation_pct']==pytest.approx(-35.7142857)
