"""Isolated mobile-startup smoke app: deliberately has no project imports or I/O."""
import streamlit as st

st.set_page_config(page_title="ディズニー混雑AI・最小表示検証", page_icon="🏰")
st.title("ディズニー混雑AI")

parks = ["Disneyland", "DisneySea"]
if "park" not in st.session_state:
    st.session_state["park"] = "Disneyland"
park = st.radio("パーク", parks, index=parks.index(st.session_state["park"]), horizontal=True, key="park_radio")
if park is None:
    park = st.session_state["park"]
st.session_state["park"] = park

modes = [
    "ダッシュボード",
    "全アトラクション",
    "アトラクション別予測",
    "DPA/PP予測",
    "日付指定予測",
    "回り方プランナー",
    "アルコール情報",
    "実測待ち時間",
    "管理者画面",
]
if "mode" not in st.session_state:
    st.session_state["mode"] = modes[0]
mode = st.radio("表示モード", modes, index=modes.index(st.session_state["mode"]), horizontal=True, key="display_mode_radio")
if mode is None:
    mode = st.session_state["mode"]
st.session_state["mode"] = mode

st.write(f"選択中: {park} / {mode}")

st.markdown(
    """<style>
    @media (max-width: 760px) {
      .st-key-park_radio [role="radiogroup"],
      .st-key-display_mode_radio [role="radiogroup"] {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px !important;
        width: 100%;
      }
      .st-key-park_radio [role="radiogroup"] label,
      .st-key-display_mode_radio [role="radiogroup"] label { min-height: 44px; }
      .st-key-display_mode_radio [role="radiogroup"] > :last-child { grid-column: 1 / -1; }
    }
    </style>""",
    unsafe_allow_html=True,
)

