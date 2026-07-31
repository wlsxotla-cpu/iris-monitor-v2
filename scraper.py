"""
IRIS(범부처통합연구지원시스템) 사업공고 스크래퍼 (requests 기반, 브라우저 미사용)

- https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do 에
  실제 브라우저가 보내는 것과 같은 POST 요청을 그대로 보내서 목록 HTML을
  받아와 파싱한다. (Playwright/헤드리스 브라우저를 쓰지 않아 훨씬 빠르다)
- 전체 소관부처 기준으로 "접수예정" / "접수중" 데이터를 모두 가져온다.
  부처 필터링은 여기서 하지 않고, 어떤 부처를 볼지는 대시보드(Streamlit)에서
  사용자가 직접 고른다.
- 이전 결과와 비교하는 로직은 없다 (매번 전체 현재 목록을 그대로 저장).
- 추가: 키워드 매칭 + 의미 유사도(임베딩) 기반으로 KOTERI 역량과 관련 있을
  법한 공고 후보를 추리고, 후보만 상세페이지+첨부파일을 자동 다운로드한다.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer, util

URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"
DETAIL_URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do"
FILE_CHECK_URL = "https://www.iris.go.kr/comm/file/retrieveCheckFileDownload.do"
FILE_DOWNLOAD_URL = "https://www.iris.go.kr/comm/file/fileDownload.do"

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

MAX_PAGES = 60  # 안전장치: 전체 부처를 다 가져오면 페이지가 많아지므로 넉넉하게 잡는다

CALL_PATTERN = re.compile(r"^(\w+)\(([^)]*)\)")
ATCH_PATTERN = re.compile(
    r"f_bsnsAncm_downloadAtchFile\('([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)'\)"
)

# ---------------------------------------------------------------------------
# KOTERI 역량 기반 키워드 (역량 프로파일 문서 3~5장 기준)
# ---------------------------------------------------------------------------
KEYWORDS = [
    # 보유 설비/공정
    "습식방사", "용융방사", "전기방사", "습식초지", "니들펀칭", "카딩", "볼밀", "동결건조",
    "액체암모니아가공", "스마트환편", "브레이딩", "텐터", "코팅", "라미네이팅", "함침",
    # 소재 분야 전반 (보유 여부 무관, 섬유·소재 도메인이면 다 포함)
    "섬유", "원단", "직물", "부직포", "복합소재", "복합재", "고분자", "필름",
    # 친환경·리사이클
    "리사이클", "재활용", "재생원료", "순환경제", "r-HDPE", "해중합", "바이오매스",
    # 기능성 가공
    "발수", "항균", "난연", "접촉냉감", "흡한속건", "방오", "플라즈마",
    # 나노·융복합
    "나노섬유", "나노소재", "그래핀", "에어로겔", "MOF",
    # 응용분야 (모빌리티/헬스케어/방호/필터)
    "도어트림", "헤드라이너", "배터리팩 소재", "웨어러블", "더마코스메틱",
    "방검", "방탄", "방화복", "방열텐트", "절연슈트", "구명조끼",
    "필터", "여과", "흡착", "수처리 분리막",
    # 시험인증·분석 (보유 역량 + 위탁 가능성 모두 포함)
    "견뢰도", "KOLAS", "아릴아민", "포름알데히드", "시험인증", "물성시험",
    "LC-MS", "GC-MS", "ICP", "FE-SEM", "미량 화학물질", "대사체",
    # 역할 기반 (제목만 봐선 소재 여부가 안 보이는 유형)
    "실증 지원기관", "위탁시험", "연구기획과제", "기획연구",
]

# ---------------------------------------------------------------------------
# 의미 유사도(임베딩) 매칭 — 키워드가 정확히 안 겹쳐도 "뜻이 비슷하면" 후보로 채택
# ---------------------------------------------------------------------------
CAPABILITY_SENTENCES = [
    "습식방사와 용융방사를 이용한 섬유 성형 공정 개발",
    "니들펀칭과 습식초지 기반 부직포 제조 기술",
    "리사이클 PET 및 나일론을 활용한 친환경 재생섬유 개발",
    "폐섬유 및 폐플라스틱의 화학적 리사이클(해중합) 기술",
    "발수, 항균, 난연 등 기능성 섬유 가공 기술",
    "접촉냉감 및 흡한속건 기능성 원단 개발",
    "나노섬유 및 그래핀·에어로겔 기반 융복합 소재 개발",
    "자동차 내장재용 경량 복합소재 부품 개발",
    "웨어러블 디바이스 및 헬스케어용 스마트 텍스타일 개발",
    "생분해성 섬유 기반 필터 및 여과 소재 개발",
    "방검, 방탄, 방화 등 산업안전 보호 섬유소재 개발",
    "섬유제품 물성 및 기능성 시험, KOLAS 공인 시험인증",
    "미량 화학물질 및 대사체 분석을 위한 LC-MS, GC-MS 기반 분석",
    "신규 R&D 사업 기획 및 산업 분석, 타당성 조사",
    "위탁 시험기관 및 실증 지원기관으로서의 역할 수행",
]

SEMANTIC_THRESHOLD = 0.35  # 이 이상이면 "의미상 유사"로 판단 (튜닝 필요할 수 있음)

_model = None


def get_model():
    """모델은 한 번만 로드 (다국어 지원, 가볍고 빠른 모델)."""
    global _model
    if _model is None:
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _model


def semantic_score(title: str, org: str) -> float:
    """공고 제목+부처 텍스트와 역량 프로파일 문장들 중 최댓값 유사도를 반환 (0~1)."""
    model = get_model()
    query = f"{title} {org}"
    query_emb = model.encode(query, convert_to_tensor=True)
    cap_embs = model.encode(CAPABILITY_SENTENCES, convert_to_tensor=True)
    scores = util.cos_sim(query_emb, cap_embs)[0]
    return float(scores.max())


# ---------------------------------------------------------------------------
# 목록 파싱
# ---------------------------------------------------------------------------

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
    """실제 li 구조에 맞춰 정확하게 파싱한다."""
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
    """onclick 인자(ancmId, ancmPrg)를 각 항목에 붙인다."""
    for item in items:
        func_name, args = parse_onclick_args(item.get("raw_link"))
        if len(args) >= 2:
            item["ancm_id"] = args[0]
            item["ancm_prg"] = args[1]
        else:
            item["ancm_id"] = None
            item["ancm_prg"] = None


YEARS_BACK = 1  # 최근 몇 년치 공고만 가져올지


def is_recent(ancm_date: str, cutoff_date) -> bool:
    try:
        d = datetime.strptime(ancm_date, "%Y-%m-%d").date()
        return d >= cutoff_date
    except Exception:
        return True


def scrape():
    all_items = []
    cutoff_date = (datetime.now(KST) - timedelta(days=365 * YEARS_BACK)).date()

    session = requests.Session()
    try:
        session.get(URL, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"[warn] 초기 접속 실패 (계속 진행): {e}", file=sys.stderr, flush=True)

    for tab, code in TAB_CODES.items():
        page_index = 1
        empty_streak = 0
        while True:
            try:
                html = fetch_page(session, code, page_index)
            except Exception as e:
                print(f"[warn] 요청 실패: {tab} 페이지 {page_index} ({e})", file=sys.stderr, flush=True)
                break

            soup = BeautifulSoup(html, "html.parser")
            page_text = soup.get_text("\n")

            total_pages = get_total_pages(page_text)
            page_items = parse_items(soup, tab, page_index)
            print(
                f"[info] {tab} 페이지 {page_index}/{total_pages} 처리 중 ({len(page_items)}건 파싱)",
                file=sys.stderr,
                flush=True,
            )

            recent_items = [i for i in page_items if is_recent(i["ancm_date"], cutoff_date)]
            has_old_item = len(recent_items) < len(page_items)
            all_items.extend(recent_items)

            if not page_items:
                empty_streak += 1
                print(f"[warn] {tab} 페이지 {page_index}: 파싱된 항목 0건", file=sys.stderr, flush=True)
            else:
                empty_streak = 0

            if has_old_item:
                print(
                    f"[info] {tab} 페이지 {page_index}: 최근 {YEARS_BACK}년 이전 공고 발견, 이후 페이지 생략",
                    file=sys.stderr,
                    flush=True,
                )

            if page_index >= total_pages or page_index >= MAX_PAGES or empty_streak >= 2 or has_old_item:
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


# ---------------------------------------------------------------------------
# 상세페이지 + 첨부파일 다운로드
# ---------------------------------------------------------------------------

def fetch_detail(session, ancm_id: str, ancm_prg: str) -> str:
    payload = dict(BASE_PAYLOAD)
    payload["ancmId"] = ancm_id
    payload["ancmPrg"] = ancm_prg
    resp = session.post(DETAIL_URL, data=payload, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def extract_attachments_from_detail(html: str):
    return [
        {"atchDocId": m.group(1), "atchFileId": m.group(2),
         "fileNm": m.group(3), "fileSz": m.group(4)}
        for m in ATCH_PATTERN.finditer(html)
    ]


def download_attachment(session, atch: dict, dest_dir: str):
    check_payload = {"atchDocId": atch["atchDocId"], "atchFileId": atch["atchFileId"]}
    session.post(FILE_CHECK_URL, data=check_payload, headers=HEADERS, timeout=20)

    params = {"atchDocId": atch["atchDocId"], "atchFileId": atch["atchFileId"]}
    resp = session.get(FILE_DOWNLOAD_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", atch["fileNm"])
    path = os.path.join(dest_dir, safe_name)
    with open(path, "wb") as f:
        f.write(resp.content)
    return path


def title_matches_keywords(item: dict) -> list:
    text = f"{item['title']} {item['org']} {item['agency']}"
    return [kw for kw in KEYWORDS if kw in text]


def is_candidate(item: dict):
    """키워드 매칭 또는 의미 유사도, 둘 중 하나라도 걸리면 후보로 채택."""
    matched_kw = title_matches_keywords(item)
    sim_score = semantic_score(item["title"], item["org"])
    is_cand = bool(matched_kw) or (sim_score >= SEMANTIC_THRESHOLD)
    return is_cand, matched_kw, sim_score


def screen_and_download(session, items: list, output_dir="results/attachments"):
    screening = []
    for item in items:
        is_cand, matched, sim_score = is_candidate(item)

        entry = {
            "ancm_id": item.get("ancm_id"),
            "title": item["title"],
            "org": item["org"],
            "tab": item["tab"],
            "ancm_date": item["ancm_date"],
            "keyword_matched": matched,
            "semantic_score": round(sim_score, 3),
            "candidate": is_cand,
            "attachments": [],
        }

        if is_cand and item.get("ancm_id") and item.get("ancm_prg"):
            try:
                html = fetch_detail(session, item["ancm_id"], item["ancm_prg"])
                atchs = extract_attachments_from_detail(html)
                print(f"[스크리닝] {item['title']}: 첨부파일 {len(atchs)}개 발견",
                      file=sys.stderr, flush=True)
                dest = os.path.join(output_dir, item["ancm_id"])
                for atch in atchs:
                    try:
                        path = download_attachment(session, atch, dest)
                        entry["attachments"].append(path)
                    except Exception as e:
                        print(f"[warn] 첨부파일 다운로드 실패 {item['title']}: {e}",
                              file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[warn] 상세페이지 조회 실패 {item['title']}: {e}",
                      file=sys.stderr, flush=True)

        screening.append(entry)

    return screening


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

    print(f"총 {len(items)}건 저장 완료")  # 기존 동작은 여기서 이미 100% 완료됨

    # --- 신규 추가: 키워드+의미유사도 스크리닝 + 후보 첨부파일 다운로드 ---
    # 이 블록에서 무슨 에러가 나도 위의 기존 저장 결과에는 전혀 영향 없음
    try:
        session2 = requests.Session()
        session2.get(URL, headers=HEADERS, timeout=20)
        screening = screen_and_download(session2, items)
        with open("results/screening.json", "w", encoding="utf-8") as f:
            json.dump(
                {"updated_at": payload["updated_at"], "items": screening},
                f, ensure_ascii=False, indent=2,
            )
        n_candidates = sum(1 for s in screening if s["candidate"])
        n_downloaded = sum(1 for s in screening if s["attachments"])
        print(f"[스크리닝] 후보 {n_candidates}건, 첨부파일 다운로드 성공 {n_downloaded}건")
    except Exception as e:
        print(f"[warn] 스크리닝 단계 실패 (기존 결과에는 영향 없음): {e}", file=sys.stderr, flush=True)
