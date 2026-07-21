"""
IRIS(범부처통합연구지원시스템) 사업공고 스크래퍼 (requests 기반, 브라우저 미사용)

- https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do 에
  실제 브라우저가 보내는 것과 같은 POST 요청을 그대로 보내서 목록 HTML을
  받아와 파싱한다. (Playwright/헤드리스 브라우저를 쓰지 않아 훨씬 빠르다)
- 전체 소관부처 기준으로 "접수예정" / "접수중" 데이터를 모두 가져온다.
  부처 필터링은 여기서 하지 않고, 어떤 부처를 볼지는 대시보드(Streamlit)에서
  사용자가 직접 고른다.
- 이전 결과와 비교하는 로직은 없다 (매번 전체 현재 목록을 그대로 저장).

주의:
  이 요청 방식(POST + 페이로드)은 사용자가 브라우저 개발자도구에서 직접
  확인해서 알려준 내용을 기반으로 만든 1차 버전입니다. 세션/쿠키가 추가로
  필요하거나, 응답 구조가 예상과 달라 파싱이 실패할 수 있습니다. 그런
  경우 Actions 로그를 공유해주시면 바로 수정하겠습니다.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"

# "접수예정"=ancmPre, "접수중"=ancmIng, "마감"=ancmEnd
TAB_CODES = {
    "접수예정": "ancmPre",
    "접수중": "ancmIng",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

BASE_PAYLOAD = {
    "bizSearch": "",
    "bsnsTl": "",
    "ancmPrg": "",
    "pageIndex": "1",
    "ancmId": "",
    "ancmNo": "",
    "ancmTurn": "",
    "seq": "",
    "hirkSorgnBsnsCd": "",
    "bsnsAncmTap": "",
    "shSorgnYyBsnsCd": "",
    "sorgnIdArr": "",
    "ancmSttArr": "",
    "pbofrTpArr": "",
    "qualCndtArr": "",
    "blngGovdSeArr": "",
    "techFildArr": "",
    "shBsnsYy": "",
}

KST = timezone(timedelta(hours=9))

KNOWN_ORGS = [
    "범부처", "과학기술정보통신부", "산업통상부", "중소벤처기업부", "국토교통부",
    "교육부", "기상청", "농림축산식품부", "농촌진흥청", "국가유산청",
    "문화체육관광부", "방위사업청", "보건복지부", "산림청", "식품의약품안전처",
    "원자력안전위원회", "해양수산부", "행정안전부", "기후에너지환경부", "법무부",
    "국방부", "고용노동부", "경찰청", "재정경제부", "소방청", "해양경찰청",
    "관세청", "조달청", "질병관리청", "개인정보보호위원회", "국민안전처",
    "대통령경호처", "우주항공청", "방송미디어통신위원회", "고준위 방사성폐기물 관리위원회",
    "다부처",
]
_ORG_ALT = "|".join(sorted((re.escape(o) for o in KNOWN_ORGS), key=len, reverse=True))

ITEM_PATTERN = re.compile(
    r"(?P<org>" + _ORG_ALT + r")\s*>\s*(?P<agency>[^\n]+?)\s*\n+"
    r"\s*(?P<title>[^\n]+?)\s*\n+"
    r"\s*공고번호\s*:\s*(?P<ancm_no>[^\n]*?)\s*"
    r"공고일자\s*:\s*(?P<ancm_date>[\d\-]+)\s*"
    r"공고상태\s*:\s*(?P<status>[^\n]*?)\s*"
    r"공모유형\s*:\s*(?P<type>[^\n]+?)\s*\n",
    re.MULTILINE,
)

CALL_PATTERN = re.compile(r"^(\w+)\(([^)]*)\)")


def parse_onclick_args(onclick: str):
    if not onclick:
        return None, []
    m = CALL_PATTERN.match(onclick.strip())
    if not m:
        return None, []
    func_name = m.group(1)
    raw_args = m.group(2)
    args = [a.strip().strip("'").strip('"') for a in raw_args.split(",") if a.strip()]
    return func_name, args


def get_total_pages(page_text: str) -> int:
    m = re.search(r"현재\s*페이지\s*\d+\s*/\s*(\d+)", page_text)
    return int(m.group(1)) if m else 1


def parse_items(page_text: str, tab: str, page_num: int):
    items = []
    for m in ITEM_PATTERN.finditer(page_text):
        items.append(
            {
                "tab": tab,
                "page_num": page_num,
                "org": m.group("org").strip(),
                "agency": m.group("agency").strip(),
                "title": m.group("title").strip(),
                "ancm_no": m.group("ancm_no").strip(),
                "ancm_date": m.group("ancm_date").strip(),
                "status": m.group("status").strip(),
                "type": m.group("type").strip(),
                "detail_url": None,
                "raw_link": None,
                "attachments": [],
            }
        )
    return items


def attach_links(soup: BeautifulSoup, items):
    """응답 HTML에서 제목 텍스트와 일치하는 링크의 href/onclick을 붙인다."""
    by_text = {}
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        if not text:
            continue
        href = a.get("href")
        onclick = a.get("onclick")
        if href or onclick:
            by_text.setdefault(text, {"href": href, "onclick": onclick})

    for item in items:
        r = by_text.get(item["title"])
        if not r:
            continue
        href = r.get("href")
        onclick = r.get("onclick")
        if href and not href.startswith("javascript") and href != "#":
            item["detail_url"] = href
        if onclick:
            item["raw_link"] = onclick
        elif href:
            item["raw_link"] = href


def fetch_page(session, ancm_prg: str, page_index: int):
    payload = dict(BASE_PAYLOAD)
    payload["ancmPrg"] = ancm_prg
    payload["pageIndex"] = str(page_index)
    resp = session.post(URL, data=payload, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def scrape():
    all_items = []

    session = requests.Session()
    # 세션 쿠키 확보를 위해 먼저 일반 GET으로 한 번 접속한다.
    try:
        session.get(URL, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"[warn] 초기 접속 실패 (계속 진행): {e}", file=sys.stderr)

    for tab, code in TAB_CODES.items():
        page_index = 1
        while True:
            try:
                html = fetch_page(session, code, page_index)
            except Exception as e:
                print(f"[warn] 요청 실패: {tab} 페이지 {page_index} ({e})", file=sys.stderr)
                break

            soup = BeautifulSoup(html, "html.parser")
            page_text = soup.get_text("\n")

            total_pages = get_total_pages(page_text)
            page_items = parse_items(page_text, tab, page_index)
            attach_links(soup, page_items)
            all_items.extend(page_items)

            if not page_items:
                print(f"[warn] {tab} 페이지 {page_index}: 파싱된 항목 0건", file=sys.stderr)

            if page_index >= total_pages:
                break
            page_index += 1

    return all_items


def render_markdown(items):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"# IRIS 공고 현황 ({now})", ""]
    lines.append(f"조회 탭: {', '.join(TAB_CODES.keys())} (전체 부처)")
    lines.append("")

    if not items:
        lines.append("조회된 공고가 없습니다. (요청/파싱 오류 가능성 있음 - 로그 확인 필요)")
        return "\n".join(lines)

    for tab in TAB_CODES.keys():
        tab_items = [i for i in items if i["tab"] == tab]
        lines.append(f"## {tab} ({len(tab_items)}건)")
        lines.append("")
        for i in tab_items:
            title_line = f"- **{i['title']}**"
            if i.get("detail_url"):
                title_line = f"- **[{i['title']}]({i['detail_url']})**"
            lines.append(title_line)
            lines.append(f"  - 부처/전문기관: {i['org']} > {i['agency']}")
            lines.append(f"  - 공고번호: {i['ancm_no']}")
            lines.append(f"  - 공고일자: {i['ancm_date']}")
            lines.append(f"  - 상태: {i['status']} / 공모유형: {i['type']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    items = scrape()
    md = render_markdown(items)

    os.makedirs("results", exist_ok=True)
    with open("results/latest.md", "w", encoding="utf-8") as f:
        f.write(md)

    payload = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "tabs": list(TAB_CODES.keys()),
        "items": items,
    }
    with open("results/latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"총 {len(items)}건 저장 완료")
