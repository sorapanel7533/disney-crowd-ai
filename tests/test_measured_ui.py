from datetime import date,time
import pandas as pd
from streamlit.testing.v1 import AppTest
import measured_ui
import measured as m
import pytest
from test_measured import SharedRemote,RemoteClient

@pytest.mark.parametrize('method',['開始・乗車時刻から計算','待ち時間を直接入力'])
def test_guest_post_is_persisted_and_visible_in_separate_session(monkeypatch,method):
    remote=SharedRemote()
    monkeypatch.setattr(measured_ui,'configured',lambda st:RemoteClient(remote))
    monkeypatch.setattr(measured_ui,'shared',lambda st:RemoteClient(remote).load()[:2])
    monkeypatch.setattr(measured_ui,'history_for',lambda park:pd.DataFrame())
    code="import streamlit as st\nfrom measured_ui import render\nrender(st,'Disneyland',False)"
    a=AppTest.from_string(code,default_timeout=25).run()
    assert not a.exception
    [x for x in a.radio if x.label=='入力方法'][0].set_value(method).run()
    a.checkbox(key='mw_test_submission').check()
    a.date_input(key='mw_day').set_value(date(2026,11,1))
    a.time_input(key='mw_start').set_value(time(10,20))
    if method=='開始・乗車時刻から計算':
        a.date_input(key='mw_end_day').set_value(date(2026,11,1))
        a.time_input(key='mw_end_time').set_value(time(11,5))
    [x for x in a.checkbox if '現地の案内' in x.label][0].check().run()
    [x for x in a.number_input if '現地で確認' in x.label][0].set_value(70.)
    [x for x in a.button if x.label=='実測を投稿する'][0].click().run()
    assert not a.exception,[(x.message,x.stack_trace) for x in a.exception]
    assert any('保存完了' in x.value for x in a.success)
    db,_,_=RemoteClient(remote).load();rows=m.records(db)
    assert len(rows)==1 and rows[0]['actual_wait']==45 and rows[0]['displayed_wait']==70
    assert rows[0]['status']=='pending' and rows[0]['is_test']
    b=AppTest.from_string(code,default_timeout=25).run()
    assert b.query_params!=a.query_params
    b.checkbox(key='mw_test_view').check()
    [x for x in b.radio if x.label=='集計対象'][0].set_value('未承認を含む参考集計').run()
    assert not b.exception,[(x.message,x.stack_trace) for x in b.exception]
    assert any('1' in x.value and '件 / 表示値' in x.value for x in b.markdown)
    assert not m.frame(db).shape[0]
