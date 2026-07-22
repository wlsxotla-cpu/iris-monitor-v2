"""
KEIT SROME 수요조사 / 인터넷공시 스크래퍼

- 수요조사: https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveDmndSrvyLstView.do
  기본 화면(파라미터 없이 GET)이 이미 접수중 항목만 최신순으로 보여주고 있어서
  그대로 가져오면 된다.
- 인터넷공시: https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveIntrnDsclsLstView.do
  기본 화면은 전체(접수마감 포함) 오래된 순서로 나오고, GET 쿼리 파라미터
  (pageIndex 등)는 무시된다 - IRIS처럼 실제로는 POST 방식일 가능성이 높다.
  정확한 필터(접수중+최신순) 요청 방식이 아직 확인되지 않아 이 부분은
  비어있는 상태로 둔다 (추후 payload 확인되면 채울 예정).

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
INTRN_URL = "https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveIntrnDsclsLstView.do?prgmId=XPG201020000"

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
            }
        )

    return items


def fetch_dmnd_srvy(session):
    try:
        resp = session.get(DMND_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[warn] 수요조사 요청 실패: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    return parse_srome_items(soup, "수요조사")


def fetch_intrn_dscls(session):
    # TODO: 접수중+최신순 필터의 실제 요청(POST) 방식이 확인되면 채운다.
    print("[warn] 인터넷공시: 필터 요청 방식 미확인 - 건너뜀", file=sys.stderr)
    return []


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
