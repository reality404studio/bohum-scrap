"""
금융감독원 분쟁조정사례 스크래퍼
URL: https://www.fss.or.kr/fss/job/fncCnflCase/list.do?menuNo=201195
필터: 유형=보험, 날짜=해당 분기
"""

import sys
import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def get_quarter_dates(year: int, quarter: int) -> tuple[str, str]:
    """분기에 해당하는 날짜 범위 반환 (YY.MM.DD 형식)"""
    ranges = {
        1: ("01.01", "03.31"),
        2: ("04.01", "06.30"),
        3: ("07.01", "09.30"),
        4: ("10.01", "12.31"),
    }
    start_m, end_m = ranges[quarter]
    yy = str(year)[-2:]
    return f"{yy}.{start_m}", f"{yy}.{end_m}"


def scrape_fss(year: int, quarter: int, max_retries: int = 3) -> list[dict]:
    """
    금감원 분쟁조정사례 수집
    반환: [{"title": str, "date": str, "url": str, "content": str}, ...]
    """
    start_date, end_date = get_quarter_dates(year, quarter)
    yy = str(year)[-2:]
    full_start = f"20{yy}.{start_date[3:]}"  # 예: 2025.01.01
    full_end = f"20{yy}.{end_date[3:]}"

    print(f"[금감원] 수집 시작: {full_start} ~ {full_end}")

    for attempt in range(1, max_retries + 1):
        try:
            return _scrape_fss_attempt(year, quarter, full_start, full_end)
        except Exception as e:
            if attempt < max_retries:
                print(f"[금감원] 접속 실패 - 재시도 중 ({attempt}/{max_retries})... 오류: {e}")
                time.sleep(3)
            else:
                print(f"[금감원] {max_retries}회 시도 후 실패: {e}")
                return []


def _scrape_fss_attempt(year: int, quarter: int, full_start: str, full_end: str) -> list[dict]:
    cases = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        base_url = "https://www.fss.or.kr/fss/job/fncCnflCase/list.do"
        params = "menuNo=201195&pageIndex=1&searchCnd=1&sdate=&edate=&rgnlCode=B&cvplCode=&searchWrd="

        print("[금감원] 목록 페이지 접속 중...")
        page.goto(f"{base_url}?{params}", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)

        # 날짜 필터 적용
        _apply_date_filter(page, full_start, full_end)

        # 페이지별 목록 수집
        page_index = 1
        while True:
            items = _extract_list_items(page, full_start, full_end)
            if not items:
                break

            in_range = [i for i in items if _in_date_range(i["date"], full_start, full_end)]
            cases.extend(in_range)

            # 날짜 범위를 벗어난 항목이 있으면 더 이상 탐색 불필요
            out_of_range = [i for i in items if not _in_date_range(i["date"], full_start, full_end)]
            if out_of_range:
                break

            # 다음 페이지
            page_index += 1
            if not _go_to_next_page(page, page_index):
                break
            time.sleep(1)

        print(f"[금감원] 목록 수집 완료: {len(cases)}건")

        # 각 케이스 상세 내용 수집
        for i, case in enumerate(cases, 1):
            print(f"[금감원] 상세 내용 수집 중 ({i}/{len(cases)}): {case['title'][:30]}...")
            content = _scrape_detail(context, case["url"])
            case["content"] = content
            time.sleep(0.5)

        browser.close()

    print(f"[금감원] 수집 완료: {len(cases)}건")
    return cases


def _apply_date_filter(page, full_start: str, full_end: str):
    """날짜 필터 입력 및 검색"""
    try:
        # 날짜 형식: YYYY.MM.DD → YYYY-MM-DD (사이트에 따라 다를 수 있음)
        start_input = full_start.replace(".", "")  # 20250101
        end_input = full_end.replace(".", "")

        # 날짜 입력 필드 찾기 (sdate, edate)
        sdate = page.query_selector("input[name='sdate'], #sdate, input[id='sdate']")
        edate = page.query_selector("input[name='edate'], #edate, input[id='edate']")

        if sdate and edate:
            sdate.fill(full_start)
            edate.fill(full_end)
            # 검색 버튼 클릭
            search_btn = page.query_selector("button[type='submit'], input[type='submit'], .btn-search, a.btn")
            if search_btn:
                search_btn.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(1)
        else:
            print("[금감원] 날짜 필터 필드를 찾지 못함 - 전체 목록에서 날짜 필터링")
    except Exception as e:
        print(f"[금감원] 날짜 필터 적용 실패: {e} - 전체 목록에서 날짜 필터링")


def _extract_list_items(page, full_start: str, full_end: str) -> list[dict]:
    """목록 페이지에서 항목 추출"""
    items = []
    try:
        # 테이블 행 탐색
        rows = page.query_selector_all("table tbody tr, .list-board li, ul.board-list li")
        if not rows:
            # JavaScript 렌더링 대기
            page.wait_for_selector("table tbody tr, .board-list", timeout=10000)
            rows = page.query_selector_all("table tbody tr, .list-board li, ul.board-list li")

        for row in rows:
            try:
                # 제목 링크
                link = row.query_selector("a")
                if not link:
                    continue

                title = link.inner_text().strip()
                href = link.get_attribute("href") or ""

                # 날짜 (td 중 날짜 패턴 찾기)
                tds = row.query_selector_all("td, span")
                date_str = ""
                for td in tds:
                    text = td.inner_text().strip()
                    if re.match(r"\d{4}\.\d{2}\.\d{2}", text):
                        date_str = text
                        break

                if not title or not date_str:
                    continue

                # 절대 URL 구성
                if href.startswith("/"):
                    url = f"https://www.fss.or.kr{href}"
                elif href.startswith("http"):
                    url = href
                else:
                    # onclick에서 URL 추출 시도
                    onclick = link.get_attribute("onclick") or ""
                    url_match = re.search(r"location\.href='([^']+)'", onclick)
                    url = f"https://www.fss.or.kr{url_match.group(1)}" if url_match else ""

                if title and date_str:
                    items.append({"title": title, "date": date_str, "url": url, "content": ""})
            except Exception:
                continue

    except Exception as e:
        print(f"[금감원] 목록 파싱 오류: {e}")

    return items


def _in_date_range(date_str: str, full_start: str, full_end: str) -> bool:
    """날짜가 범위 내에 있는지 확인"""
    try:
        d = datetime.strptime(date_str, "%Y.%m.%d")
        s = datetime.strptime(full_start, "%Y.%m.%d")
        e = datetime.strptime(full_end, "%Y.%m.%d")
        return s <= d <= e
    except Exception:
        return False


def _go_to_next_page(page, page_index: int) -> bool:
    """다음 페이지로 이동"""
    try:
        # pageIndex 파라미터로 이동
        next_link = page.query_selector(f"a[href*='pageIndex={page_index}'], .pagination a[data-page='{page_index}']")
        if next_link:
            next_link.click()
            page.wait_for_load_state("networkidle", timeout=15000)
            return True

        # JavaScript 페이징 함수 시도
        result = page.evaluate(f"typeof fn_search === 'function' ? (fn_search({page_index}), true) : false")
        if result:
            page.wait_for_load_state("networkidle", timeout=15000)
            return True

        return False
    except Exception:
        return False


def _scrape_detail(context, url: str) -> str:
    """상세 페이지 내용 수집"""
    if not url:
        return ""
    try:
        page = context.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        # 본문 컨텐츠 추출
        content_selectors = [
            ".view-content", ".board-view", ".content-area",
            "article", ".view-body", "#contents", ".bbs-view"
        ]
        content = ""
        for sel in content_selectors:
            elem = page.query_selector(sel)
            if elem:
                content = elem.inner_text().strip()
                if len(content) > 100:
                    break

        if not content:
            content = page.inner_text("body")

        page.close()
        return content
    except Exception as e:
        return f"[내용 수집 실패: {e}]"


if __name__ == "__main__":
    results = scrape_fss(2025, 2)
    for r in results:
        print(f"- {r['date']} | {r['title'][:50]}")
