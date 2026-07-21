"""
IRIS 공고 현황 대시보드 (Streamlit)

GitHub의 wlsxotla-cpu/iris-monitor-v2 리포지토리에서 GitHub Actions가
매일 만들어두는 results/latest.json 을 읽어서 카드 형태로 보여준다.
이 앱 자체는 IRIS 사이트에 접속하지 않으므로, 예전처럼 Streamlit Cloud가
IRIS 쪽에서 IP 차단을 당하는 문제가 생기지 않는다.
"""

import pandas as pd
import requests
import streamlit as st

RAW_JSON_URL = (
    "https://raw.githubusercontent.com/wlsxotla-cpu/iris-monitor-v2/main/results/latest.json"
)

DETAIL_URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do"

FORM_FIELDS = [
    "bizSearch", "bsnsTl", "ancmPrg", "pageIndex", "ancmId", "ancmNo",
    "ancmTurn", "seq", "hirkSorgnBsnsCd", "bsnsAncmTap", "shSorgnYyBsnsCd",
    "sorgnIdArr", "ancmSttArr", "pbofrTpArr", "qualCndtArr", "blngGovdSeArr",
    "techFildArr", "shBsnsYy",
]

st.set_page_config(page_title="IRIS 공고 현황", layout="wide")

st.markdown(
    """
    <style>
    .org-header {
        background: #2c5aa0;
        color: white;
        padding: 10px 16px;
        border-radius: 8px 8px 0 0;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-weight: 600;
        font-size: 1.05rem;
        margin-top: 18px;
    }
    .org-header .count {
        background: rgba(255,255,255,0.25);
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.85rem;
    }
    .tab-count {
        color: #666;
        font-size: 0.85rem;
        margin: 4px 0 10px 0;
    }
    .ancm-card {
        border: 1px solid #e5e5e5;
        border-top: none;
        border-radius: 0 0 8px 8px;
        padding: 12px 16px;
        margin-bottom: 2px;
    }
    .ancm-title { font-weight: 600; margin-bottom: 4px; }
    .ancm-meta { color: #777; font-size: 0.85rem; margin-bottom: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📋 IRIS 공고 현황")


@st.cache_data(ttl=300)
def load_data():
    resp = requests.get(RAW_JSON_URL, timeout=15)
    resp.raise_for_status()
    return resp.json()


try:
    data = load_data()
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

items = data.get("items", [])
if not items:
    st.info("현재 조회된 공고가 없습니다.")
    st.stop()

df = pd.DataFrame(items)
df["org_list"] = df["org"].apply(
    lambda o: [x.strip() for x in o.split(",") if x.strip()] if o else ["부처 미표시"]
)
# 콤마로 여러 부처가 같이 적힌 공동(다부처) 공고는, 관련된 부처 그룹 모두에 노출한다.
exploded = df.explode("org_list").rename(columns={"org_list": "org_label"})

with st.sidebar:
    st.header("⚙️ 설정")
    tab_options = sorted(exploded["tab"].unique())
    org_options = sorted(exploded["org_label"].unique())

    qp = st.query_params
    saved_tabs = [t for t in qp.get("tabs", "").split(",") if t in tab_options]
    saved_orgs = [o for o in qp.get("orgs", "").split(",") if o in org_options]

    selected_tabs = st.multiselect(
        "탭", tab_options, default=saved_tabs or tab_options
    )
    selected_orgs = st.multiselect(
        "소관부처", org_options, default=saved_orgs or org_options
    )
    keyword = st.text_input("제목 검색", value=qp.get("kw", ""))

    # 선택값을 URL에 반영 (다음에 이 URL로 들어오면 그대로 복원됨)
    st.query_params["tabs"] = ",".join(selected_tabs)
    st.query_params["orgs"] = ",".join(selected_orgs)
    if keyword:
        st.query_params["kw"] = keyword
    elif "kw" in st.query_params:
        del st.query_params["kw"]

    st.caption("💡 지금 이 필터 상태로 주소창 URL을 즐겨찾기 해두면 다음에도 그대로 열립니다.")
    st.caption(f"마지막 갱신: {data.get('updated_at', '알 수 없음')}")
    if st.button("🔄 새로고침"):
        st.cache_data.clear()
        st.rerun()

filtered = exploded[exploded["tab"].isin(selected_tabs) & exploded["org_label"].isin(selected_orgs)]
if keyword:
    filtered = filtered[filtered["title"].str.contains(keyword, case=False, na=False)]

st.write(f"총 **{filtered['title'].nunique()}**건 (공동부처 공고는 관련 부처 모두에 표시됩니다)")


def detail_button_html(ancm_id, ancm_prg):
    if not ancm_id or not ancm_prg:
        return ""
    hidden_inputs = "".join(
        f'<input type="hidden" name="{f}" value="{ancm_prg if f == "ancmPrg" else (ancm_id if f == "ancmId" else "")}">'
        for f in FORM_FIELDS
    )
    return f"""
    <form action="{DETAIL_URL}" method="post" target="_blank" style="display:inline;margin:0;">
        {hidden_inputs}
        <button type="submit" style="
            padding:4px 10px;border-radius:6px;border:1px solid #2c5aa0;
            background:white;color:#2c5aa0;cursor:pointer;font-size:0.85rem;">
            🔗 IRIS에서 보기
        </button>
    </form>
    """


for org_label in sorted(filtered["org_label"].unique(), key=lambda x: (x == "부처 미표시", x)):
    org_items = filtered[filtered["org_label"] == org_label]

    tab_counts = org_items["tab"].value_counts()
    tab_summary = "  ·  ".join(f"{t} {c}건" for t, c in tab_counts.items())

    st.markdown(
        f"""
        <div class="org-header">
            <span>{org_label}</span>
            <span class="count">{len(org_items)}건</span>
        </div>
        <div class="tab-count">{tab_summary}</div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(3)
    for i, (_, row) in enumerate(org_items.iterrows()):
        with cols[i % 3]:
            html_button = detail_button_html(row.get("ancm_id"), row.get("ancm_prg"))
            st.markdown(
                f"""
                <div class="ancm-card">
                    <div class="ancm-title">{row['title']}</div>
                    <div class="ancm-meta">
                        {row['tab']} · {row['agency']}<br>
                        공고번호 {row['ancm_no']}<br>
                        {row['ancm_date']} · {row['status']} / {row['type']}
                    </div>
                    {html_button}
                </div>
                """,
                unsafe_allow_html=True,
            )
