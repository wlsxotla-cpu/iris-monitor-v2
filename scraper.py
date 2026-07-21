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

MAX_PAGES = 10  # 안전장치: 페이지 수 파싱이 잘못되더라도 무한히 돌지 않도록 상한선

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


def parse_items(soup: BeautifulSoup, tab: str, page_num: int):
    """실제 li 구조에 맞춰 정확하게 파싱한다.

    <li>
      <span class="inst_title">부처 > 전문기관</span>
      <div class="form-row">
        <div class="group1">
          <strong class="title"><a onclick="...">제목</a></strong>
          <div class="etc_info">
            <span><em>공고번호 :</em>값</span>
            <span class="ancmDe"><em>공고일자 :</em>값</span>
            <span class="rcveSttSeNmLst"><em>공고상태 :</em>값</span>
            <span class="pbofrTpSeNmLst"><em>공모유형 :</em>값</span>
          </div>
        </div>
      </div>
    </li>
    """
    items = []

    for li in soup.find_all("li"):
        inst = li.find("span", class_="inst_title")
        link = li.select_one("strong.title a")
        etc = li.find("div", class_="etc_info")
        if not inst or not link or not etc:
            continue

        org, _, agency = inst.get_text(strip=True).partition(">")
        org, agency = org.strip(), agency.strip()

        title = link.get_text(strip=True)
        href = link.get("href")
        onclick = link.get("onclick")

        fields = {}
        for span in etc.find_all("span"):
            em = span.find("em")
            if not em:
                continue
            label = em.get_text(strip=True)
            value = span.get_text(strip=True)[len(em.get_text(strip=True)):].strip()
            fields[label] = value

        def get_field(*keywords):
            for label, value in fields.items():
                if all(k in label for k in keywords):
                    return value
            return ""

        items.append(
            {
                "tab": tab,
                "page_num": page_num,
                "org": org,
                "agency": agency,
                "title": title,
                "ancm_no": get_field("공고번호"),
                "ancm_date": get_field("공고일자"),
                "status": get_field("공고상태"),
                "type": get_field("공모유형"),
                "detail_url": href if href and not href.startswith("javascript") and href.strip() not in ("", "#") else None,
                "raw_link": onclick or (href if href else None),
                "attachments": [],
            }
        )

    return items


def fetch_page(session, ancm_prg: str, page_index: int):
    payload = dict(BASE_PAYLOAD)
    payload["ancmPrg"] = ancm_prg
    payload["pageIndex"] = str(page_index)
    resp = session.post(URL, data=payload, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def resolve_detail_form_fields(items):
    """onclick 인자(ancmId, ancmPrg)를 각 항목에 붙인다.
    실제 상세페이지는 이 값들로 폼을 POST 제출해야 열리므로, 여기서는
    URL을 만들지 않고 폼 제출에 필요한 값만 남겨둔다 (대시보드에서 처리)."""
    for item in items:
        func_name, args = parse_onclick_args(item.get("raw_link"))
        if len(args) >= 2:
            item["ancm_id"] = args[0]
            item["ancm_prg"] = args[1]
        else:
            item["ancm_id"] = None
            item["ancm_prg"] = None


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
        empty_streak = 0
        while True:
            try:
                html = fetch_page(session, code, page_index)
            except Exception as e:
                print(f"[warn] 요청 실패: {tab} 페이지 {page_index} ({e})", file=sys.stderr)
                break

            soup = BeautifulSoup(html, "html.parser")
            page_text = soup.get_text("\n")

            total_pages = get_total_pages(page_text)
            page_items = parse_items(soup, tab, page_index)
            all_items.extend(page_items)

            if not page_items:
                empty_streak += 1
                print(f"[warn] {tab} 페이지 {page_index}: 파싱된 항목 0건", file=sys.stderr)
            else:
                empty_streak = 0

            # 안전장치: 전체 페이지 수를 잘못 읽었거나 빈 페이지가 계속되면 중단
            if page_index >= total_pages or page_index >= MAX_PAGES or empty_streak >= 2:
                break
            page_index += 1

    resolve_detail_form_fields(all_items)

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
