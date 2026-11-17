"""All views use isolated DBs; no test mutations reach production or seed DBs."""
import json
from pathlib import Path
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
import utils
import reliability as r

VIEWS=['ダッシュボード','全アトラクション','アトラクション別予測','DPA/PP予測','日付指定予測','回り方プランナー','アルコール情報','実測待ち時間','管理者画面']
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
    plot_titles = []
    pyplot = st.pyplot
    image = st.image

    def record_figure(figure=None, **kwargs):
        if figure is not None:
            plot_titles.extend(axis.get_title() for axis in figure.axes)
        return pyplot(figure, **kwargs)

    def record_image(data, **kwargs):
        if isinstance(data, bytes):
            plot_titles.append("wait graph")
        return image(data, **kwargs)

    monkeypatch.setattr(st, "pyplot", record_figure)
    monkeypatch.setattr(st, "image", record_image)
    at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=35)
    at.secrets['ADMIN_VIEW_PIN'] = '2468'
    if park == "DisneySea":
        at.session_state["park"] = park
    at.run()
    assert at.session_state["park"] == park
    assert list(at.radio(key="park_radio").options) == ["Disneyland", "DisneySea"]
    assert at.radio(key="park_radio").value == park
    assert list(at.radio(key="display_mode_radio").options) == VIEWS
    assert not at.exception, [(e.message,e.stack_trace) for e in at.exception]
    for view in VIEWS:
        plot_titles.clear()
        at.radio(key='display_mode_radio').set_value(view).run()
        assert not at.exception, (park,scenario,view,[(e.message,e.stack_trace) for e in at.exception])
        assert not at.caption, (park, scenario, view)
        if view == "ダッシュボード":
            assert not any("投稿用 X文面" in header.value for header in at.subheader)
            assert not any("Crowd Index" in frame.value.columns and "予測種別" in frame.value.columns for frame in at.dataframe)
            park_name = "ランド" if park == "Disneyland" else "シー"
            assert f"{park_name} 1週間の混雑予測" in plot_titles
        if view == "全アトラクション":
            assert not any("保存履歴" in header.value for header in at.subheader)
        if view == "アトラクション別予測":
            assert not any("予測誤差履歴" in header.value for header in at.subheader)
            if plot_titles:
                assert not any("予測表示待ち時間（分）" in frame.value.columns for frame in at.dataframe)
        if view == "日付指定予測":
            headings = [item.value for item in at.subheader]
            assert "時間帯別・アトラクション別待ち時間予測" in headings
            assert not any(word in heading for word in ("おすすめ行動プラン", "リスク診断", "健全性", "注意点") for heading in headings)
            if scenario == "open":
                assert plot_titles
                ride = at.selectbox(key=f"_date_forecast_ride_{park}")
                assert len(ride.options) > 1
                plot_titles.clear()
                ride.select_index(1).run()
                assert plot_titles and not at.exception
                at.selectbox(key=f"_date_forecast_{park}_weather").select("雨").run()
                assert at.number_input(key=f"_date_forecast_{park}_rain").value == 1
                plot_titles.clear()
                at.number_input(key=f"_date_forecast_{park}_rain").set_value(4.5).run()
                assert plot_titles and not at.exception
        if view == "アルコール情報":
            assert any("メンテナンス中" in item.value for item in at.info)
            assert not at.selectbox
        if view=='回り方プランナー':
            [b for b in at.button if b.label=='プランを作成'][0].click().run()
            assert not at.exception, [(e.message,e.stack_trace) for e in at.exception]
        if view=='管理者画面':
            assert not at.selectbox
            at.text_input[0].set_value("2468")
            next(b for b in at.button if b.label == "管理者画面を開く").click().run()
            assert any("投稿用 X文面" in item.value for item in at.subheader)
            assert any(item.label == "1週間予測の詳細" for item in at.expander)
            for section in ("全アトラクション", "アトラクション別予測", "日付指定予測", "アルコール情報", "実測待ち時間", "データ・システム管理"):
                at.selectbox(key="_admin_section").set_value(section).run()
                assert not at.exception, (section, [(e.message,e.stack_trace) for e in at.exception])
                assert not any("詳細を読み込めませんでした" in w.value or "保存履歴を読み込めませんでした" in w.value for w in at.warning), section
                if section == "日付指定予測":
                    for table in ("この日のおすすめ行動プラン", "時間帯別・アトラクション別待ち時間予測の詳細", "この日の予測リスク診断", "予測データの健全性", "予測の注意点"):
                        assert any(item.label == table for item in at.expander)
                    if scenario == "open":
                        assert at.number_input(key=f"_date_forecast_{park}_rain").value == 4.5
            assert [b for b in at.button if b.label=='過去データを取り込む'][0].disabled
        if view=='DPA/PP予測':
            assert [b for b in at.button if b.label=='DPA売切れ履歴を全削除'][0].disabled
