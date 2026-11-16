"""Read-only tables displayed after the administrator viewing gate."""
from datetime import timedelta

import pandas as pd
import streamlit as st

from reliability import japanese_name

ADMIN_SECTIONS = (
    "ダッシュボード", "全アトラクション", "アトラクション別予測", "日付指定予測",
    "アルコール情報", "実測待ち時間", "データ・システム管理",
)

JAPANESE_COLUMNS = {
    "Attraction": "アトラクション", "attraction": "アトラクション",
    "Date": "日付", "Crowd Index": "混雑指数",
    "TimeLabel": "時刻", "Time": "時刻（小数）", "Hour": "時", "Minute": "分",
    "Predicted Wait": "予測待ち時間（分）", "created_at": "予測作成日時",
    "target_date": "予測日", "target_hour": "予測時", "target_minute": "予測分",
    "actual_wait": "観測待ち時間（分）", "predicted_wait": "予測待ち時間（分）",
    "error": "予測誤差（分）", "quality": "品質", "source": "取得元",
    "Quality": "品質", "Source": "取得元",
}


def table(title, frame, key, park, source, timestamp=None, empty="該当するデータはまだありません。"):
    with st.expander(title, expanded=False):
        if frame.empty:
            st.info(empty)
            return
        latest = frame[timestamp].max() if timestamp and timestamp in frame else None
        st.caption(f"{len(frame):,}件 ／ 保存元・計算元 {source}" + (f" ／ 最新記録 {latest}" if pd.notna(latest) else ""))
        display = frame.copy()
        for column in ("Attraction", "attraction"):
            if column in display:
                display[column] = display[column].map(japanese_name)
        display = display.rename(columns=JAPANESE_COLUMNS)
        if len(display) > 2000:
            st.caption("画面には最大2,000件を表示します。CSVには絞り込み後の全件が含まれます。")
        st.dataframe(display.head(2000), height=360, width="stretch", hide_index=True)
        st.download_button(
            "CSVをダウンロード", display.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{park}-{key}.csv", mime="text/csv", key=f"admin_csv_{key}",
        )


def history(conn, park, today):
    st.subheader("保存履歴")
    attractions = pd.read_sql_query(
        "SELECT DISTINCT attraction FROM wait_times WHERE park=? ORDER BY attraction", conn, params=(park,)
    )["attraction"].dropna().tolist()
    if not attractions:
        st.info("選択したパークの保存履歴がまだありません。")
        return
    ride = st.selectbox("アトラクション", attractions, format_func=japanese_name, key=f"admin_history_ride_{park}")
    period = st.date_input("表示する期間", (today - timedelta(days=7), today), max_value=today, key=f"admin_history_period_{park}")
    if len(period) != 2:
        st.info("開始日と終了日を選んでください。")
        return
    kinds = ["すべて", "有効な営業中の観測", "休止・閉園・欠測など"]
    kind = st.selectbox("データ種別", kinds, key="admin_history_kind")
    extra = {
        kinds[0]: "",
        kinds[1]: " AND quality='valid' AND is_open=1 AND wait_time>=0",
        kinds[2]: " AND NOT (COALESCE(quality,'')='valid' AND COALESCE(is_open,0)=1 AND COALESCE(wait_time,-1)>=0)",
    }[kind]
    rows = pd.read_sql_query(
        "SELECT datetime AS 保存時刻, wait_time AS 待ち時間, temperature AS 気温, rain AS 降水量,"
        " CASE WHEN is_open=1 THEN '営業中' WHEN is_open=0 THEN '休止・閉園' ELSE '不明' END AS 営業状態,"
        " source, quality FROM wait_times"
        " WHERE park=? AND attraction=? AND datetime>=? AND datetime<?"
        + extra + " ORDER BY datetime DESC",
        conn, params=(park, ride, str(period[0]), str(period[1] + timedelta(days=1))),
    )
    table("保存履歴の詳細", rows, "observations", park, "待ち時間履歴DB", "保存時刻",
          "指定した施設・期間・データ種別に該当する保存履歴がありません。")
