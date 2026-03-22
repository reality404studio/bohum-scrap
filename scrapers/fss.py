"""
금융감독원 분쟁조정사례 스크래퍼
URL: https://www.fss.or.kr/fss/job/fncCnflCase/list.do
필터: 유형=보험(rgnlCode=B), 날짜=해당 분기
방식: requests + BeautifulSoup (Playwright 제거)
"""

import time
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.fss.or.kr"
LIST_URL = f"{BASE_URL}/fss/job/fncCnflCase/list.do"
VIEW_URL = f"{BASE_URL}/fss/job/fncCnflCase/view.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": LIST_URL,
}

QUARTER_RANGES = {
    1: ("01-01", "03-31"),
    2: ("04-01", "06-30"),
    3: ("07-01", "09-30"),
    4: ("10-01", "12-31"),
}


def scrape_fss(year: int, quarter: int) -> list[dict]:
    """
    금감원 분쟁조정사례 수집
    반환: [{"title": str, "date": str, "url": str, "content": str, "source": str}, ...]
    """
    sm, em = QUARTER_RANGES[quarter]
    sdate = f"{year}-{sm}"
    edate = f"{year}-{em}"
    full_start = datetime.strptime(sdate, "%Y-%m-%d")
    full_end = datetime.strptime(edate, "%Y-%m-%d")

    print(f"[금감원] 수집 시작: {sdate} ~ {edate}")

    session = requests.Session()
    session.headers.update(HEADERS)

    cases = []
    page_index = 1

    while True:
        params = {
            "menuNo": "201195",
            "pageIndex": str(page_index),
            "searchCnd": "1",
            "sdate": sdate,
            "edate": edate,
            "rgnlCode": "B",
            "cvplCode": "",
            "searchWrd": "",
        }

        try:
            resp = session.get(LIST_URL, params=params, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"[금감원] 목록 페이지 {page_index} 요청 실패: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        items, stop = _parse_list_page(soup, full_start, full_end)
        cases.extend(items)

        if stop or not items:
            break

        # 다음 페이지 존재 여부 확인
        if not _has_next_page(soup, page_index):
            break

        page_index += 1
        time.sleep(0.5)

    print(f"[금감원] 목록 수집 완료: {len(cases)}건")

    # 상세 내용 수집
    for i, case in enumerate(cases, 1):
        print(f"[금감원] 상세 수집 중 ({i}/{len(cases)}): {case['title'][:30]}...")
        case["content"] = _fetch_detail(session, case["case_slno"])
        time.sleep(0.5)

    print(f"[금감원] 수집 완료: {len(cases)}건")
    return cases


def _parse_list_page(
    soup: BeautifulSoup,
    full_start: datetime,
    full_end: datetime,
) -> tuple[list[dict], bool]:
    """목록 페이지 파싱. (items, stop_flag) 반환"""
    items = []
    stop = False

    tbody = soup.select_one("table tbody")
    if not tbody:
        return items, True

    rows = tbody.select("tr")
    for row in rows:
        tds = row.select("td")
        if len(tds) < 3:
            continue

        # 날짜 td 탐색 (YYYY-MM-DD 또는 YYYY.MM.DD 패턴)
        date_str = ""
        for td in tds:
            text = td.get_text(strip=True)
            m = re.match(r"(\d{4})[-.](\d{2})[-.](\d{2})", text)
            if m:
                date_str = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
                break

        if not date_str:
            continue

        try:
            date_obj = datetime.strptime(date_str, "%Y.%m.%d")
        except ValueError:
            continue

        if date_obj > full_end:
            continue
        if date_obj < full_start:
            stop = True
            continue

        # 제목 링크 탐색
        link = row.select_one("a[href*='caseSlno='], td a")
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link.get("href", "")

        # caseSlno 추출
        slno_match = re.search(r"caseSlno=(\d+)", href)
        if not slno_match:
            # onclick에서 추출 시도
            onclick = link.get("onclick", "") or row.get("onclick", "")
            slno_match = re.search(r"caseSlno[=,'\s]+(\d+)", onclick)

        if not slno_match:
            continue

        case_slno = slno_match.group(1)
        url = f"{VIEW_URL}?caseSlno={case_slno}&menuNo=201195"

        items.append({
            "title": title,
            "date": date_str,
            "url": url,
            "case_slno": case_slno,
            "content": "",
            "source": "금감원",
        })

    return items, stop


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    """다음 페이지 링크 존재 여부"""
    next_page = current_page + 1
    # 페이지네이션 링크에서 다음 페이지 번호 확인
    paging = soup.select("div.paging a, div.pagination a, .paging a")
    for a in paging:
        href = a.get("href", "")
        onclick = a.get("onclick", "")
        text = a.get_text(strip=True)
        if (
            str(next_page) in text
            or f"pageIndex={next_page}" in href
            or f"pageIndex={next_page}" in onclick
        ):
            return True
    return False


def _fetch_detail(session: requests.Session, case_slno: str) -> str:
    """상세 페이지 본문 수집"""
    try:
        params = {"caseSlno": case_slno, "menuNo": "201195"}
        resp = session.get(VIEW_URL, params=params, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        return f"[내용 수집 실패: {e}]"

    soup = BeautifulSoup(resp.text, "lxml")

    # 본문 영역 선택자 우선순위
    for sel in [".view-content", ".board-view", ".cont-area", "#contents article", ".bbs-view"]:
        elem = soup.select_one(sel)
        if elem:
            text = elem.get_text(separator="\n", strip=True)
            if len(text) > 100:
                return text

    # 테이블 형태 본문 (th-td 쌍)
    view_table = soup.select_one("table.view, table.v_tbl, .view-wrap table")
    if view_table:
        rows = view_table.select("tr")
        parts = []
        for row in rows:
            th = row.select_one("th")
            td = row.select_one("td")
            if th and td:
                parts.append(f"[{th.get_text(strip=True)}]\n{td.get_text(separator=' ', strip=True)}")
        if parts:
            return "\n\n".join(parts)

    # 최후 수단: body 전체
    body = soup.select_one("body")
    return body.get_text(separator="\n", strip=True) if body else ""


if __name__ == "__main__":
    results = scrape_fss(2025, 4)
    for r in results:
        print(f"- {r['date']} | {r['title'][:50]}")
    print(f"총 {len(results)}건")
