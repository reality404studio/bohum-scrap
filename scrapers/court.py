"""
대법원 판례 스크래퍼
URL: https://portal.scourt.go.kr/pgp/index.on?m=PGP1011M01&l=N&c=900
검색어: 보험금
정확도 최우선: 원문 전체 내용 수집
"""

import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def scrape_court(year: int, quarter: int, max_retries: int = 3) -> list[dict]:
    """
    대법원 판례 수집
    반환: [{"title": str, "date": str, "url": str, "content": str, "case_number": str}, ...]
    """
    quarter_ranges = {
        1: ("01", "01", "03", "31"),
        2: ("04", "01", "06", "30"),
        3: ("07", "01", "09", "30"),
        4: ("10", "01", "12", "31"),
    }
    sm, sd, em, ed = quarter_ranges[quarter]
    full_start = f"{year}{sm}{sd}"   # YYYYMMDD
    full_end = f"{year}{em}{ed}"
    date_display_start = f"{year}.{sm}.{sd}"
    date_display_end = f"{year}.{em}.{ed}"

    print(f"[대법원] 수집 시작: {date_display_start} ~ {date_display_end}")

    for attempt in range(1, max_retries + 1):
        try:
            return _scrape_court_attempt(year, quarter, full_start, full_end, date_display_start, date_display_end)
        except Exception as e:
            if attempt < max_retries:
                print(f"[대법원] 접속 실패 - 재시도 중 ({attempt}/{max_retries})... 오류: {e}")
                time.sleep(3)
            else:
                print(f"[대법원] {max_retries}회 시도 후 실패: {e}")
                return []


def _scrape_court_attempt(
    year: int, quarter: int,
    full_start: str, full_end: str,
    date_display_start: str, date_display_end: str
) -> list[dict]:
    cases = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        url = "https://portal.scourt.go.kr/pgp/index.on?m=PGP1011M01&l=N&c=900"
        print("[대법원] 검색 페이지 접속 중...")
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(2)

        # 검색어 입력 및 날짜 필터
        _perform_search(page, full_start, full_end)

        # 페이지별 수집
        page_num = 1
        while True:
            items = _extract_list_items(page, date_display_start, date_display_end)
            if not items:
                break

            in_range = [i for i in items if _in_date_range(i["date"], date_display_start, date_display_end)]
            cases.extend(in_range)

            out_of_range = [i for i in items if not _in_date_range(i["date"], date_display_start, date_display_end)]
            if out_of_range:
                break

            page_num += 1
            if not _go_to_next_page(page, page_num):
                break
            time.sleep(1.5)

        print(f"[대법원] 목록 수집 완료: {len(cases)}건")

        # 상세 내용 수집 (원문 전체 - 정확도 최우선)
        for i, case in enumerate(cases, 1):
            print(f"[대법원] 원문 수집 중 ({i}/{len(cases)}): {case['title'][:40]}...")
            content = _scrape_full_text(context, case["url"])
            case["content"] = content
            time.sleep(1)  # 대법원 서버 부하 방지

        browser.close()

    print(f"[대법원] 수집 완료: {len(cases)}건")
    return cases


def _perform_search(page, full_start: str, full_end: str):
    """검색어 입력 및 날짜 범위 설정"""
    try:
        # 검색어 입력
        search_inputs = page.query_selector_all("input[type='text'], input[name*='search'], input[id*='search'], input[placeholder]")
        keyword_input = None
        for inp in search_inputs:
            placeholder = inp.get_attribute("placeholder") or ""
            name = inp.get_attribute("name") or ""
            id_ = inp.get_attribute("id") or ""
            if any(k in (placeholder + name + id_).lower() for k in ["검색", "keyword", "search", "query"]):
                keyword_input = inp
                break

        if not keyword_input and search_inputs:
            keyword_input = search_inputs[0]

        if keyword_input:
            keyword_input.fill("보험금")

        # 날짜 범위 입력
        start_selectors = [
            "input[name='startDt']", "input[name='fromDate']", "input[id*='startDt']",
            "input[id*='fromDt']", "#startDate"
        ]
        end_selectors = [
            "input[name='endDt']", "input[name='toDate']", "input[id*='endDt']",
            "input[id*='toDt']", "#endDate"
        ]

        for sel in start_selectors:
            sdate = page.query_selector(sel)
            if sdate:
                sdate.fill(full_start)
                break

        for sel in end_selectors:
            edate = page.query_selector(sel)
            if edate:
                edate.fill(full_end)
                break

        # 검색 실행
        search_btn = page.query_selector("button[type='submit'], input[type='submit'], .btn-search, button.search-btn")
        if search_btn:
            search_btn.click()
        else:
            if keyword_input:
                keyword_input.press("Enter")

        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(2)
        print(f"[대법원] 검색 완료: '보험금' {full_start}~{full_end}")

    except Exception as e:
        print(f"[대법원] 검색 실행 실패: {e}")


def _extract_list_items(page, date_display_start: str, date_display_end: str) -> list[dict]:
    """목록에서 판례 항목 추출"""
    items = []
    try:
        rows = page.query_selector_all("table tbody tr, .result-item, .search-result li, ul.case-list li")
        if not rows:
            page.wait_for_selector("table tbody tr, .result-item", timeout=10000)
            rows = page.query_selector_all("table tbody tr, .result-item, .search-result li")

        for row in rows:
            try:
                link = row.query_selector("a")
                if not link:
                    continue

                title = link.inner_text().strip()
                href = link.get_attribute("href") or ""

                # 사건번호 추출 (예: 2024다326398)
                case_number = ""
                case_match = re.search(r"\d{4}[가-힣]\w+\d+", title)
                if case_match:
                    case_number = case_match.group(0)

                # 날짜 찾기
                all_text = row.inner_text()
                date_match = re.search(r"(\d{4})[.\-/](\d{2})[.\-/](\d{2})", all_text)
                date_str = ""
                if date_match:
                    date_str = f"{date_match.group(1)}.{date_match.group(2)}.{date_match.group(3)}"

                if not title or not date_str:
                    continue

                # URL 구성
                if href.startswith("/"):
                    url = f"https://portal.scourt.go.kr{href}"
                elif href.startswith("http"):
                    url = href
                else:
                    onclick = link.get_attribute("onclick") or row.get_attribute("onclick") or ""
                    # 판례 ID 추출 시도
                    id_match = re.search(r"'([A-Z0-9]+)'", onclick)
                    if id_match:
                        url = f"https://portal.scourt.go.kr/pgp/index.on?m=PGP1011M02&l=N&c=900&seq={id_match.group(1)}"
                    else:
                        url = "https://portal.scourt.go.kr/pgp/index.on?m=PGP1011M01&l=N&c=900"

                items.append({
                    "title": title,
                    "date": date_str,
                    "url": url,
                    "content": "",
                    "case_number": case_number,
                    "source": "대법원"
                })
            except Exception:
                continue

    except Exception as e:
        print(f"[대법원] 목록 파싱 오류: {e}")

    return items


def _in_date_range(date_str: str, full_start: str, full_end: str) -> bool:
    try:
        d = datetime.strptime(date_str, "%Y.%m.%d")
        s = datetime.strptime(full_start, "%Y.%m.%d")
        e = datetime.strptime(full_end, "%Y.%m.%d")
        return s <= d <= e
    except Exception:
        return False


def _go_to_next_page(page, page_num: int) -> bool:
    try:
        next_btn = page.query_selector(
            f"a[href*='page={page_num}'], a[onclick*='page({page_num})'], "
            f".pagination a[data-page='{page_num}'], a.next-page"
        )
        if next_btn:
            next_btn.click()
            page.wait_for_load_state("networkidle", timeout=20000)
            return True

        # "다음" 버튼 시도
        next_text_btn = page.query_selector("a:text('다음'), button:text('다음'), .btn-next")
        if next_text_btn:
            next_text_btn.click()
            page.wait_for_load_state("networkidle", timeout=20000)
            return True

        return False
    except Exception:
        return False


def _scrape_full_text(context, url: str) -> str:
    """
    대법원 판례 원문 전체 수집 (정확도 최우선)
    법률 원문 표현 최대한 유지
    """
    if not url:
        return ""
    try:
        page = context.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(1)

        # 판례 본문 선택자 (대법원 특화)
        content_selectors = [
            "#viewPrintArea", ".case-content", ".judgment-content",
            ".view-content", "#prncContentNm", ".판례본문",
            ".prncContent", "#contArea", "article.case"
        ]
        content = ""
        for sel in content_selectors:
            elem = page.query_selector(sel)
            if elem:
                content = elem.inner_text().strip()
                if len(content) > 200:
                    break

        if not content or len(content) < 200:
            # 전체 페이지에서 본문 영역 추출
            content = page.inner_text("body")
            # 헤더/푸터 노이즈 제거
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines
                if len(line.strip()) > 5 and not any(
                    noise in line for noise in ["검색", "로그인", "메뉴", "Copyright", "이용약관"]
                )
            )

        page.close()
        return content
    except Exception as e:
        return f"[원문 수집 실패: {e}]"


if __name__ == "__main__":
    results = scrape_court(2025, 2)
    for r in results:
        print(f"- {r['date']} | {r['title'][:50]}")
