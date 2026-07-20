"""
IRIS(범부처통합연구지원시스템) 사업공고 스크래퍼

- https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do 에서
  전체 소관부처 기준으로 "접수예정" / "접수중" 탭의 공고 목록을 가져와
  results/latest.md, results/latest.json 파일로 저장한다.
- 부처 필터링은 여기서 하지 않는다. 전체 부처를 다 가져온 뒤, 어떤 부처를
  볼지는 대시보드(Streamlit)에서 사용자가 직접 선택한다.
- 각 공고의 상세페이지 링크와 첨부파일 링크도 함께 수집한다.
- 이전 결과와 비교하는 로직은 없다 (매번 전체 현재 목록을 그대로 저장).

주의:
  상세페이지/첨부파일 추출 부분은 사이트의 실제 상세페이지 구조를 직접
  확인하지 못한 상태에서 최선으로 작성한 부분입니다. 목록 파싱은 이미
  검증됐지만, 상세페이지 쪽에서 오류가 나거나 링크가 비어있을 수 있습니다.
  그런 경우 Actions 로그를 공유해주시면 바로 고치겠습니다.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright

URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"

# 조회할 탭 ("접수예정", "접수중", "마감" 중 선택)
TABS = ["접수예정", "접수중"]

# 참고용 (부처 필터링에는 더 이상 쓰이지 않음 - 대시보드에서 선택)
DEPARTMENTS_HINT = ["산업통상부", "중소벤처기업부", "과학기술정보통신부"]

# 첨부파일로 볼 만한 링크를 찾기 위한 확장자/키워드
ATTACHMENT_HINTS = (".hwp", ".hwpx", ".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
ATTACHMENT_KEYWORDS = ("첨부", "다운로드", "download")

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


def click_search(page):
    try:
        page.get_by_text("검색", exact=True).first.click()
    except Exception as e:
        print(f"[warn] 검색 버튼 클릭 실패: {e}", file=sys.stderr)
    page.wait_for_timeout(1500)


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
                "attachments": [],
            }
        )
    return items


def collect_list(page):
    """전체 탭 x 전체 페이지를 돌면서 목록 항목(부처/제목/날짜 등)만 먼저 수집한다."""
    all_items = []

    for tab in TABS:
        try:
            page.get_by_text(tab, exact=True).first.click()
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[warn] 탭 클릭 실패: {tab} ({e})", file=sys.stderr)
            continue

        click_search(page)

        page_num = 1
        while True:
            body_text = page.inner_text("body")
            total_pages = get_total_pages(body_text)
            all_items.extend(parse_items(body_text, tab, page_num))

            if page_num >= total_pages:
                break

            page_num += 1
            try:
                page.get_by_text(str(page_num), exact=True).first.click()
                page.wait_for_timeout(1200)
            except Exception as e:
                print(f"[warn] 페이지 이동 실패: {page_num} ({e})", file=sys.stderr)
                break

    return all_items


def extract_attachments(page):
    """현재(상세) 페이지에서 첨부파일로 보이는 링크들을 뽑아낸다."""
    attachments = []
    try:
        links = page.query_selector_all("a")
        for link in links:
            try:
                text = (link.inner_text() or "").strip()
                href = link.get_attribute("href") or ""
            except Exception:
                continue
            haystack = f"{text} {href}".lower()
            if any(ext in haystack for ext in ATTACHMENT_HINTS) or any(
                kw in text for kw in ATTACHMENT_KEYWORDS
            ):
                if href and not href.startswith("javascript"):
                    attachments.append({"name": text or href, "url": href})
    except Exception as e:
        print(f"[warn] 첨부파일 추출 실패: {e}", file=sys.stderr)
    return attachments


def fetch_detail(browser, tab, page_num, title):
    """목록에서 특정 공고를 다시 찾아가 상세페이지 링크와 첨부파일을 가져온다."""
    detail_url = None
    attachments = []
    page = browser.new_page()
    try:
        page.goto(URL, wait_until="networkidle")
        page.get_by_text(tab, exact=True).first.click()
        page.wait_for_timeout(800)
        click_search(page)

        for p in range(2, page_num + 1):
            page.get_by_text(str(p), exact=True).first.click()
            page.wait_for_timeout(1000)

        link = page.get_by_text(title, exact=True).first

        try:
            with page.context.expect_page(timeout=4000) as popup_info:
                link.click()
            detail_page = popup_info.value
            detail_page.wait_for_load_state("networkidle")
            detail_url = detail_page.url
            attachments = extract_attachments(detail_page)
            detail_page.close()
        except Exception:
            # 새 탭이 안 열렸다면 같은 탭에서 이동했다고 가정
            link.click()
            page.wait_for_timeout(1500)
            detail_url = page.url
            attachments = extract_attachments(page)
    except Exception as e:
        print(f"[warn] 상세페이지 조회 실패: {title[:20]}... ({e})", file=sys.stderr)
    finally:
        page.close()

    return detail_url, attachments


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")
        items = collect_list(page)
        page.close()

        for item in items:
            detail_url, attachments = fetch_detail(
                browser, item["tab"], item["page_num"], item["title"]
            )
            item["detail_url"] = detail_url
            item["attachments"] = attachments

        browser.close()

    return items


def render_markdown(items):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"# IRIS 공고 현황 ({now})", ""]
    lines.append(f"조회 탭: {', '.join(TABS)} (전체 부처)")
    lines.append("")

    if not items:
        lines.append("조회된 공고가 없습니다. (선택자 오류 가능성 있음 - 로그 확인 필요)")
        return "\n".join(lines)

    for tab in TABS:
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
            if i.get("attachments"):
                for a in i["attachments"]:
                    lines.append(f"    - 첨부: [{a['name']}]({a['url']})")
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
        "tabs": TABS,
        "items": items,
    }
    with open("results/latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"총 {len(items)}건 저장 완료")
