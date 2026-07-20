"""
IRIS(범부처통합연구지원시스템) 사업공고 스크래퍼

- https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do 에서
  지정한 소관부처 기준으로 "접수예정" / "접수중" 탭의 공고 목록을 가져와
  results/latest.md 파일로 저장한다.
- 이전 결과와 비교하는 로직은 없다 (매번 전체 현재 목록을 그대로 저장).

주의:
  이 스크립트는 IRIS 사이트가 실제 브라우저에서 어떻게 동작하는지를
  기반으로 최선으로 작성한 1차 버전입니다. 사이트의 정확한 HTML
  구조를 직접 확인하고 테스트하지 못한 상태이므로, 처음 실행했을 때
  버튼/체크박스를 찾지 못하는 오류가 날 수 있습니다.
  그런 경우 Actions 실행 로그(오류 메시지, 가능하면 스크린샷)를
  공유해주시면 선택자를 바로 수정하겠습니다.
"""

import os
import re
import sys
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright

URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"

# 조회할 소관부처 (직접 수정 가능)
DEPARTMENTS = ["산업통상부", "중소벤처기업부", "과학기술정보통신부"]

# 조회할 탭 ("접수예정", "접수중", "마감" 중 선택)
TABS = ["접수예정", "접수중"]

KST = timezone(timedelta(hours=9))

# IRIS 사이트에 실제 존재하는 소관부처 전체 목록 (org 필드가 이 중 하나로만
# 인식되도록 제한해서, 이전 항목의 상태 태그가 다음 항목의 부처명 자리로
# 잘못 섞여 들어가는 파싱 오류를 방지한다).
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
# 길이가 긴 것부터 매칭해야 부분 문자열로 잘못 잘리는 걸 방지한다.
_ORG_ALT = "|".join(sorted((re.escape(o) for o in KNOWN_ORGS), key=len, reverse=True))

# "부처 > 전문기관 / 제목 / 공고번호 / 공고일자 / 공고상태 / 공모유형 / 상태태그"
# 형태로 반복되는 블록을 뽑아내기 위한 정규식.
ITEM_PATTERN = re.compile(
    r"(?P<org>" + _ORG_ALT + r")\s*>\s*(?P<agency>[^\n]+?)\s*\n+"
    r"\s*(?P<title>[^\n]+?)\s*\n+"
    r"\s*공고번호\s*:\s*(?P<ancm_no>[^\n]*?)\s*"
    r"공고일자\s*:\s*(?P<ancm_date>[\d\-]+)\s*"
    r"공고상태\s*:\s*(?P<status>[^\n]*?)\s*"
    r"공모유형\s*:\s*(?P<type>[^\n]+?)\s*\n",
    re.MULTILINE,
)


def set_departments(page):
    """소관부처 체크박스 중 전체선택을 해제하고 지정한 부처만 선택한다."""
    try:
        all_select = page.get_by_text("전체선택", exact=True).first
        # 이미 전체선택 상태라면 해제
        checkbox = all_select.locator(
            "xpath=preceding-sibling::input[@type='checkbox'] | xpath=following-sibling::input[@type='checkbox']"
        )
        if checkbox.count() and checkbox.first.is_checked():
            all_select.click()
    except Exception as e:
        print(f"[warn] 전체선택 해제 실패 (무시하고 진행): {e}", file=sys.stderr)

    for dept in DEPARTMENTS:
        try:
            page.get_by_text(dept, exact=True).first.click()
        except Exception as e:
            print(f"[warn] 부처 선택 실패: {dept} ({e})", file=sys.stderr)


def click_search(page):
    try:
        page.get_by_text("검색", exact=True).first.click()
    except Exception as e:
        print(f"[warn] 검색 버튼 클릭 실패: {e}", file=sys.stderr)
    page.wait_for_timeout(1500)


def get_total_pages(page_text: str) -> int:
    m = re.search(r"현재\s*페이지\s*\d+\s*/\s*(\d+)", page_text)
    return int(m.group(1)) if m else 1


def parse_items(page_text: str, tab: str):
    items = []
    for m in ITEM_PATTERN.finditer(page_text):
        items.append(
            {
                "tab": tab,
                "org": m.group("org").strip(),
                "agency": m.group("agency").strip(),
                "title": m.group("title").strip(),
                "ancm_no": m.group("ancm_no").strip(),
                "ancm_date": m.group("ancm_date").strip(),
                "status": m.group("status").strip(),
                "type": m.group("type").strip(),
            }
        )
    return items


def scrape():
    all_items = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")

        for tab in TABS:
            try:
                page.get_by_text(tab, exact=True).first.click()
                page.wait_for_timeout(1000)
            except Exception as e:
                print(f"[warn] 탭 클릭 실패: {tab} ({e})", file=sys.stderr)
                continue

            set_departments(page)
            click_search(page)

            page_num = 1
            while True:
                body_text = page.inner_text("body")
                total_pages = get_total_pages(body_text)
                all_items.extend(parse_items(body_text, tab))

                if page_num >= total_pages:
                    break

                page_num += 1
                try:
                    page.get_by_text(str(page_num), exact=True).first.click()
                    page.wait_for_timeout(1200)
                except Exception as e:
                    print(f"[warn] 페이지 이동 실패: {page_num} ({e})", file=sys.stderr)
                    break

        browser.close()

    return all_items


def render_markdown(items):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"# IRIS 공고 현황 ({now})", ""]
    lines.append(f"필터 부처: {', '.join(DEPARTMENTS)}  ")
    lines.append(f"조회 탭: {', '.join(TABS)}")
    lines.append("")

    if not items:
        lines.append("조회된 공고가 없습니다. (선택자 오류 가능성 있음 - 로그 확인 필요)")
        return "\n".join(lines)

    for tab in TABS:
        tab_items = [i for i in items if i["tab"] == tab]
        lines.append(f"## {tab} ({len(tab_items)}건)")
        lines.append("")
        for i in tab_items:
            lines.append(f"- **{i['title']}**")
            lines.append(f"  - 부처/전문기관: {i['org']} > {i['agency']}")
            lines.append(f"  - 공고번호: {i['ancm_no']}")
            lines.append(f"  - 공고일자: {i['ancm_date']}")
            lines.append(f"  - 상태: {i['status']} / 공모유형: {i['type']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import json

    items = scrape()
    md = render_markdown(items)

    os.makedirs("results", exist_ok=True)
    with open("results/latest.md", "w", encoding="utf-8") as f:
        f.write(md)

    payload = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "departments": DEPARTMENTS,
        "tabs": TABS,
        "items": items,
    }
    with open("results/latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"총 {len(items)}건 저장 완료")
