"""
KEIT SROME 수요조사 / 인터넷공시 스크래퍼

- 수요조사: https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveDmndSrvyLstView.do
  기본 화면(파라미터 없이 GET)이 이미 접수중 항목만 최신순으로 보여주고 있어서
  그대로 가져오면 된다.
- 인터넷공시: https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveIntrnDsclsLstView.do
  GET 요청이지만, pageIndex 하나만 보내면 서버가 무시하고 기본값(전체/오래된순)을
  돌려준다. 실제로는 sbjtPlnnAncmId, searchItem, searchItemContent,
  searchDmndYear, searchRcptStDt, searchRcptEdDt, rcveSe 등 전체 파라미터가
  다 있어야 필터가 제대로 적용된다 (rcveSe=01 이 접수중).

결과는 results/srome.json 에 저장한다.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

DMND_URL = "https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveDmndSrvyLstView.do?prgmId=XPG201010000"
INTRN_URL = "https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveIntrnDsclsLstView.do"
INTRN_PARAMS = {
    "pageIndex": "",
    "sbjtPlnnAncmId": "",
    "prgmId": "XPG201020000",
    "searchItem": "title",
    "searchItemContent": "",
    "searchDmndYear": "",
    "searchRcptStDt": "",
    "searchRcptEdDt": "",
    "rcveSe": "02",  # 접수중
}
INTRN_MAX_PAGES = 20

DETAIL_URL = "https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveRndPlnnDtlView.do"
PRGM_ID_BY_CATEGORY = {
    "수요조사": "XPG201010000",
    "인터넷공시": "XPG201020000",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

KST = timezone(timedelta(hours=9))

CALL_PATTERN = re.compile(r"^(\w+)\(([^)]*)\)")


def parse_onclick_args(onclick: str):
    if not onclick:
        return None, []
    onclick = onclick.strip()
    if onclick.startswith("javascript:"):
        onclick = onclick[len("javascript:"):]
    onclick = onclick.rstrip(";").strip()
    m = CALL_PATTERN.match(onclick)
    if not m:
        return None, []
    func_name = m.group(1)
    raw_args = m.group(2)
    args = [a.strip().strip("'").strip('"') for a in raw_args.split(",") if a.strip()]
    return func_name, args


def parse_srome_items(soup: BeautifulSoup, category: str):
    """<div class="table_box"> 구조를 그대로 파싱한다."""
    items = []

    for box in soup.find_all("div", class_="table_box"):
        wrap = box.find("div", class_="table_box_wrap")
        detail = wrap.find("div", class_="table_box_detail") if wrap else None
        if not detail:
            continue

        status_el = detail.select_one("p.banner span.badge")
        status = status_el.get_text(strip=True) if status_el else ""

        link = detail.select_one("p.subject a")
        title = ""
        detail_id = None
        if link:
            title_el = link.find("span", class_="title")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            _, args = parse_onclick_args(link.get("href"))
            if args:
                detail_id = args[0]

        fields = {}
        info = detail.find("div", class_="info")
        if info:
            for p in info.find_all("p"):
                label = p.find("span", class_="label")
                value = p.find("span", class_="value")
                if label and value:
                    fields[label.get_text(strip=True)] = value.get_text(strip=True)

        dday_el = box.select_one("div.table_box_btn span.badge")
        dday = dday_el.get_text(strip=True) if dday_el else ""

        detail_url = None
        if detail_id:
            prgm_id = PRGM_ID_BY_CATEGORY.get(category, "")
            detail_url = (
                f"{DETAIL_URL}?pageIndex=&sbjtPlnnAncmId={detail_id}&prgmId={prgm_id}"
                f"&searchItem=title&searchItemContent=&searchDmndYear="
                f"&searchRcptStDt=&searchRcptEdDt=&rcveSe=02"
            )

        items.append(
            {
                "category": category,
                "status": status,
                "title": title,
                "plan_year": fields.get("기획년도", ""),
                "period": fields.get("접수기간", ""),
                "notice_date": fields.get("공고일") or fields.get("등록일", ""),
                "dday": dday,
                "detail_id": detail_id,
                "detail_url": detail_url,
            }
        )

    return items


def get_total_pages(page_text: str) -> int:
    m = re.search(r"페이지\s*\d+\s*/\s*(\d+)", page_text)
    return int(m.group(1)) if m else 1


def fetch_dmnd_srvy(session):
    try:
        resp = session.get(DMND_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[warn] 수요조사 요청 실패: {e}", file=sys.stderr, flush=True)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items = parse_srome_items(soup, "수요조사")
    print(f"[info] 수요조사 {len(items)}건 파싱", file=sys.stderr, flush=True)
    return items


def fetch_intrn_dscls(session):
    items = []
    page_index = 1
    empty_streak = 0

    while True:
        params = dict(INTRN_PARAMS)
        params["pageIndex"] = str(page_index)
        try:
            resp = session.get(INTRN_URL, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[warn] 인터넷공시 요청 실패: 페이지 {page_index} ({e})", file=sys.stderr, flush=True)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        page_items = parse_srome_items(soup, "인터넷공시")
        total_pages = get_total_pages(soup.get_text("\n"))

        print(
            f"[info] 인터넷공시 페이지 {page_index}/{total_pages} 처리 중 ({len(page_items)}건 파싱)",
            file=sys.stderr,
            flush=True,
        )

        items.extend(page_items)

        if not page_items:
            empty_streak += 1
        else:
            empty_streak = 0

        if page_index >= total_pages or page_index >= INTRN_MAX_PAGES or empty_streak >= 2:
            break
        page_index += 1

    return items


def scrape():
    session = requests.Session()
    items = []
    items.extend(fetch_dmnd_srvy(session))
    items.extend(fetch_intrn_dscls(session))
    return items


if __name__ == "__main__":
    items = scrape()

    os.makedirs("results", exist_ok=True)
    payload = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "items": items,
    }
    with open("results/srome.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"총 {len(items)}건 저장 완료")
