"""All views use isolated DBs; no test mutations reach production or seed DBs."""
import json
import shutil
from pathlib import Path
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
import utils
import reliability as r

VIEWS=['ダッシュボード','全アトラクション','アトラクション別予測','DPA/PP予測','日付指定予測','回り方プランナー','データ管理','アルコール情報']
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('park',['Disneyland','DisneySea'])
@pytest.mark.parametrize('scenario',['normal','open','empty','failure'])
def test_all_views(tmp_path,monkeypatch,park,scenario):
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path))
    monkeypatch.setenv('DISNEY_DISABLE_REMOTE_SYNC','1')
    if scenario=='empty':
        import sqlite3
        for name in ('disneyland.db','disneysea.db'):
            sqlite3.connect(tmp_path/name).close()
    def waits(settings):
        if scenario=='failure': raise RuntimeError('simulated upstream outage')
        pid=275 if '/275/' in settings['url'] else 274
        data=json.loads((ROOT/'tests'/'fixtures'/f'queue-{pid}.json').read_text(encoding='utf-8'))
        f=r.parse_queue(data, 'DisneySea' if pid==275 else 'Disneyland')
        if scenario=='open':
            f['Open']=True
            f['Wait']=60.0
            f.loc[f.index[0],'Wait']=0.0
            f['UpdatedUTC']=r.iso()
            f['FetchedUTC']=r.iso()
        return f,f[f.Attraction.isin(settings['rides'])].copy()
    monkeypatch.setattr(utils,'fetch_wait_times',waits)
    monkeypatch.setattr(utils,'get_weather',lambda:(20.,0.,'テスト天気',pd.DataFrame(),pd.DataFrame()))
    monkeypatch.setattr(utils,'fetch_ticket_prices',lambda:({},'テスト：未取得'))
    if scenario=='open':
        monkeypatch.setattr(utils,'is_park_open_now',lambda *a:(True,9,21,'テスト営業時間'))
    # Cache isolation across scenarios.
    import streamlit as st
    st.cache_data.clear()
    at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=35)
    at.session_state['park']=park
    at.run()
    assert not at.exception, [(e.message,e.stack_trace) for e in at.exception]
    for view in VIEWS:
        at.button_group(key='display_mode_segmented').set_value(view).run()
        assert not at.exception, (park,scenario,view,[(e.message,e.stack_trace) for e in at.exception])
        if view=='回り方プランナー':
            [b for b in at.button if b.label=='プランを作成'][0].click().run()
            assert not at.exception, [(e.message,e.stack_trace) for e in at.exception]
        if view=='データ管理':
            assert [b for b in at.button if b.label=='過去データを取り込む'][0].disabled
        if view=='DPA/PP予測':
            assert [b for b in at.button if b.label=='DPA売切れ履歴を全削除'][0].disabled
