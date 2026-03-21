"""
한국소비자원 분쟁조정결정례 스크래퍼
URL: https://www.kca.go.kr/odr/cm/in/exmplBjItem.do
필터: 금융/보험 (코드: 00000007)
"""

import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def scrape_kca(year: int, quarter: int, max_retries: int = 3) -> list[dict]:
    """
    소비자원 분쟁조정결정례 수집
    반환: [{"title": str, "date": str, "url": str, "content": str}, ...]
    """
    quarter_ranges = {
        1: ("01", "01", "03", "31"),
        2: ("04", "01", "06", "30"),
        3: ("07", "01", "09", "30"),
        4: ("10", "01", "12", "31"),
    }
    sm, sd, em, ed = quarter_ranges[quarter]
    full_start = f"{year}.{sm}.{sd}"
    full_end = f"{year}.{em}.{ed}"

    print(f"[소비자원] 수집 시작: {full_start} ~ {full_end}")

    for attempt in range(1, max_retries + 1):
        try:
            return _scrape_kca_attempt(year, quarter, full_start, full_end)
        except Exception as e:
            if attempt < max_retries:
                print(f"[소비자원] 접속 실패 - 재시도 중 ({attempt}/{max_retries})... 오류: {e}")
                time.sleep(3)
            else:
                print(f"[소비자원] {max_retries}회 시도 후 실패: {e}")
                return []


def _scrape_kca_attempt(year: int, quarter: int, full_start: str, full_end: str) -> list[dict]:
    cases = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 금융/보험 카테고리 (코드: 00000007)
        url = "https://www.kca.go.kr/odr/cm/in/exmplBjItem.do"
        print("[소비자원] 목록 페이지 접속 중...")
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)

        # 금융/보험 카테고리 선택
        _select_finance_category(page)

        # 날짜 필터 적용
        _apply_date_filter(page, full_start, full_end)

        # 페이지별 수집
        page_num = 1
        while True:
            items = _extract_list_items(page, full_start, full_end)
            if not items:
                break

            in_range = [i for i in items if _in_date_range(i["date"], full_start, full_end)]
            cases.extend(in_range)

            out_of_range = [i for i in items if not _in_date_range(i["date"], full_start, full_end)]
            if out_of_range:
                break

            page_num += 1
            if not _go_to_next_page(page, page_num):
                break
            time.sleep(1)

        print(f"[소비자원] 목록 수집 완료: {len(cases)}건")

        # 상세 내용 수집
        for i, case in enumerate(cases, 1):
            print(f"[소비자원] 상세 내용 수집 중 ({i}/{len(cases)}): {case['title'][:30]}...")
            content = _scrape_detail(context, case["url"])
            case["content"] = content
            time.sleep(0.5)

        browser.close()

    print(f"[소비자원] 수집 완료: {len(cases)}건")
    return cases


def _select_finance_category(page):
    """금융/보험 카테고리 선택"""
    try:
        # 카테고리 링크 클릭 (코드: 00000007)
        finance_link = page.query_selector("a[onclick*='00000007']")
        if finance_link:
            finance_link.click()
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)
            print("[소비자원] 금융/보험 카테고리 선택 완료")
        else:
            # 직접 URL 파라미터로 접근 시도
            page.goto(
                "https://www.kca.go.kr/odr/cm/in/exmplBjItem.do?bjItmCd=00000007",
                timeout=30000
            )
            page.wait_for_load_state("networkidle", timeout=20000)
            print("[소비자원] 금융/보험 카테고리 URL 직접 접근")
    except Exception as e:
        print(f"[소비자원] 카테고리 선택 실패: {e}")


def _apply_date_filter(page, full_start: str, full_end: str):
    """날짜 필터 적용"""
    try:
        # 날짜 형식 변환 (YYYY.MM.DD → YYYYMMDD)
        start_compact = full_start.replace(".", "")
        end_compact = full_end.replace(".", "")

        sdate = page.query_selector("input[name='startDt'], input[name='fromDt'], input[id*='start'], input[id*='from']")
        edate = page.query_selector("input[name='endDt'], input[name='toDt'], input[id*='end'], input[id*='to']")

        if sdate:
            sdate.fill(full_start)
        if edate:
            edate.fill(full_end)

        if sdate or edate:
            search_btn = page.query_selector("button[type='submit'], .btn-search, input[type='submit']")
            if search_btn:
                search_btn.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(1)
    except Exception as e:
        print(f"[소비자원] 날짜 필터 적용 실패: {e}")


def _extract_list_items(page, full_start: str, full_end: str) -> list[dict]:
    """목록 항목 추출"""
    items = []
    try:
        rows = page.query_selector_all("table tbody tr, .list-item, ul.board-list > li")
        if not rows:
            page.wait_for_selector("table tbody tr, .list-item", timeout=10000)
            rows = page.query_selector_all("table tbody tr, .list-item, ul.board-list > li")

        for row in rows:
            try:
                link = row.query_selector("a")
                if not link:
                    continue

                title = link.inner_text().strip()
                href = link.get_attribute("href") or ""

                # 날짜 찾기
                tds = row.query_selector_all("td, span, .date")
                date_str = ""
                for td in tds:
                    text = td.inner_text().strip()
                    if re.match(r"\d{4}[-\.]\d{2}[-\.]\d{2}", text):
                        date_str = text.replace("-", ".")
                        break

                if not title or not date_str:
                    continue

                # URL 구성
                if href.startswith("/"):
                    url = f"https://www.kca.go.kr{href}"
                elif href.startswith("http"):
                    url = href
                else:
                    onclick = link.get_attribute("onclick") or ""
                    # fn_detail('12345') 같은 패턴에서 ID 추출
                    id_match = re.search(r"'(\d+)'", onclick)
                    if id_match:
                        url = f"https://www.kca.go.kr/odr/cm/in/exmplBjItemDtl.do?exmplNo={id_match.group(1)}"
                    else:
                        url = "https://www.kca.go.kr/odr/cm/in/exmplBjItem.do"

                items.append({"title": title, "date": date_str, "url": url, "content": ""})
            except Exception:
                continue

    except Exception as e:
        print(f"[소비자원] 목록 파싱 오류: {e}")

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
        next_btn = page.query_selector(f"a[href*='pageNo={page_num}'], .pagination a[data-page='{page_num}'], a.next")
        if next_btn:
            next_btn.click()
            page.wait_for_load_state("networkidle", timeout=15000)
            return True
        return False
    except Exception:
        return False


def _scrape_detail(context, url: str) -> str:
    if not url:
        return ""
    try:
        page = context.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        content_selectors = [
            ".view-content", ".board-view-content", ".detail-content",
            ".view-body", "article", "#contArea", ".cont-area"
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
    results = scrape_kca(2025, 2)
    for r in results:
        print(f"- {r['date']} | {r['title'][:50]}")
