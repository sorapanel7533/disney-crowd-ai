from pathlib import Path
import json
import pytest
from streamlit.testing.v1 import AppTest
import alcohol as a


@pytest.mark.parametrize('scenario',['populated','empty','stale','failure'])
def test_alcohol_real_screen_filters_and_fallbacks(tmp_path,monkeypatch,scenario):
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path));monkeypatch.setenv('DISNEY_DISABLE_REMOTE_SYNC','1')
    conn=a.connect(tmp_path/'alcohol.db')
    if scenario=='populated':a.bootstrap(conn)
    else:
        with conn:a.put(conn,'alcohol_runs','fixture',{'run_id':'fixture','status':'failed','finished_at_utc':a.r.iso(),'reason':'test'},a.r.iso())
    if scenario=='stale':
        with conn:a.put(conn,'alcohol_runs','old',{'run_id':'old','status':'success','finished_at_utc':'2026-01-01T00:00:00Z','target_stores':1,'successful_stores':1,'stores_with_products':0,'unconfirmed_stores':1},a.r.iso())
    conn.close()
    if scenario=='failure':monkeypatch.setattr(a,'catalog',lambda *args:(_ for _ in ()).throw(ValueError('isolated simulated DB read failure')))
    at=AppTest.from_string('import streamlit as st\nfrom alcohol_ui import render\nrender(st)',default_timeout=30).run()
    assert not at.exception
    if scenario=='failure':
        assert any('ほかの画面' in x.value for x in at.warning);return
    assert any('アルコール情報' in x.value for x in at.subheader)
    if scenario=='stale': assert any('14日以上' in x.value for x in at.error)
    if scenario=='empty': assert any('まだ登録' in x.value for x in at.warning)
    if scenario=='populated':
        assert not any('読み込めません' in x.value for x in at.warning)
        at.text_input(key='alcohol_query').set_value('ビール').run()
        assert not at.exception
        at.multiselect(key='alcohol_stores').set_value(['tdl:300']).run()
        assert not at.exception
        at.button_group(key='alcohol_park').set_value('シー').run()
        assert at.multiselect(key='alcohol_stores').value==[]
        assert at.button_group(key='alcohol_filter_park').value=='シー'
        at.button_group(key='alcohol_filter_park').set_value('ランド').run()
        assert at.button_group(key='alcohol_park').value=='ランド'
        assert not at.exception
        at.toggle(key='alcohol_exclude_beer').set_value(True).run()
        assert any('検索結果: 0商品' in x.value for x in at.caption)
        at.toggle(key='alcohol_exclude_beer').set_value(False).run()
        assert not any('検索結果: 0商品' in x.value for x in at.caption)
        at.text_input(key='alcohol_query').set_value('').run()
        at.toggle(key='alcohol_exclude_beer').set_value(True).run()
        assert not any('ビール ·' in x.value for x in at.caption)
        assert any('ワイン' in x.value or 'カクテル' in x.value for x in at.caption)
        at.radio(key='alcohol_search_mode').set_value('店舗から探す').run()
        assert not at.exception
        at.radio(key='alcohol_search_mode').set_value('更新履歴').run()
        assert not at.exception
