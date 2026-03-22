"""
한국소비자원 분쟁조정결정례 스크래퍼
목록 API: POST https://www.kca.go.kr/odr/api/cm/in/exmplBjItem.do/
상세 API: POST https://www.kca.go.kr/odr/cm/cm/boardsDtl.do
카테고리: 금융/보험 (brdId=00000007)
방식: requests + BeautifulSoup (Playwright 제거)
"""

import time
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.kca.go.kr"
LIST_API = f"{BASE_URL}/odr/api/cm/in/exmplBjItem.do/"
DETAIL_API = f"{BASE_URL}/odr/cm/cm/boardsDtl.do"
BRD_ID = "00000007"
MULTI_ITM_SEQ = "128"  # 금융/보험 서브카테고리

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/odr/cm/in/exmplBjItem.do",
    "Content-Type": "application/x-www-form-urlencoded",
}

QUARTER_RANGES = {
    1: ("01-01", "03-31"),
    2: ("04-01", "06-30"),
    3: ("07-01", "09-30"),
    4: ("10-01", "12-31"),
}

PAGE_SIZE = 20


def scrape_kca(year: int, quarter: int) -> list[dict]:
    """
    소비자원 분쟁조정결정례 수집
    반환: [{"title": str, "date": str, "url": str, "content": str, "source": str}, ...]
    """
    sm, em = QUARTER_RANGES[quarter]
    full_start = datetime.strptime(f"{year}-{sm}", "%Y-%m-%d")
    full_end = datetime.strptime(f"{year}-{em}", "%Y-%m-%d")

    print(f"[소비자원] 수집 시작: {year}-{sm} ~ {year}-{em}")

    session = requests.Session()
    session.headers.update(HEADERS)

    cases = []
    page_index = 1

    while True:
        data = {
            "brdId": BRD_ID,
            "dataStts": "Y",
            "pageSize": str(PAGE_SIZE),
            "pageIndex": str(page_index),
            "multiItmSeq": MULTI_ITM_SEQ,
            "searchKeyword": "보험",
            "searchCondition": "1",  # 제목 검색
        }

        try:
            resp = session.post(LIST_API, data=data, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"[소비자원] 목록 API 페이지 {page_index} 요청 실패: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        seq_list = _parse_list_page(soup)

        if not seq_list:
            break

        stop = False
        # 상세 수집 및 날짜/범위 필터
        for seq in seq_list:
            print(f"[소비자원] 상세 수집 중 (seq={seq})...")
            detail = _fetch_detail(session, seq)
            if detail is None:
                continue
            date_obj = detail.get("_date_obj")
            if date_obj and date_obj < full_start:
                print(f"[소비자원] 날짜 범위 이전 도달 ({detail.get('date','')}) — 수집 중단")
                stop = True
                break
            if date_obj and date_obj > full_end:
                print(f"[소비자원] 날짜 범위 이후 건 제외: {detail.get('title','')[:40]}")
                continue
            print(f"[소비자원] 수집: {detail.get('date','')} | {detail.get('title','')[:40]}")
            cases.append(detail)
            time.sleep(0.3)

        if stop:
            break

        # 다음 페이지 존재 여부 (pageSize보다 적으면 마지막)
        if len(seq_list) < PAGE_SIZE:
            break

        page_index += 1
        time.sleep(0.5)

    print(f"[소비자원] 수집 완료: {len(cases)}건")
    return cases


def _parse_list_page(soup: BeautifulSoup) -> list[str]:
    """목록 페이지에서 seq 번호 추출."""
    seq_list = []
    # onclick="fn_view_bbd("123456", "00000007")" 또는 홑따옴표 모두 처리
    for elem in soup.select("[onclick*='fn_view_bbd']"):
        onclick = elem.get("onclick", "")
        m = re.search(r'fn_view_bbd\(["\'](\d+)["\']', onclick)
        if m:
            seq_list.append(m.group(1))
    return seq_list


def _fetch_detail(session: requests.Session, seq: str) -> dict | None:
    """상세 API 호출 및 파싱"""
    data = {
        "brdId": BRD_ID,
        "dataStts": "Y",
        "seq": seq,
        "pageSize": str(PAGE_SIZE),
        "pageIndex": "1",
    }

    try:
        resp = session.post(DETAIL_API, data=data, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"[소비자원] 상세 조회 실패 (seq={seq}): {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # 제목
    title_elem = soup.select_one("div.board_view div.board_v_tit h4, h4.tit, .view-tit h4")
    title = title_elem.get_text(strip=True) if title_elem else f"소비자원 분쟁조정 seq={seq}"

    # 날짜 (수정일)
    date_str = ""
    date_obj = None
    for row in soup.select("table.v_tbl tr, table tr"):
        th = row.select_one("th")
        td = row.select_one("td")
        if not (th and td):
            continue
        if "수정일" in th.get_text() or "등록일" in th.get_text():
            raw = td.get_text(strip=True)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
            if m:
                date_str = m.group(1).replace("-", ".")
                try:
                    date_obj = datetime.strptime(m.group(1), "%Y-%m-%d")
                except ValueError:
                    pass
            break

    # 본문 섹션 수집
    sections = {}
    section_keys = ["사건개요", "당사자주장", "판단", "결정사항", "관련법률"]
    for row in soup.select("table.v_tbl tr, table tr"):
        th = row.select_one("th")
        td = row.select_one("td")
        if not (th and td):
            continue
        th_text = th.get_text(strip=True)
        for key in section_keys:
            if key in th_text:
                # div > span 구조 또는 직접 td 텍스트
                span = td.select_one("div span, span")
                text = span.get_text(separator="\n", strip=True) if span else td.get_text(separator="\n", strip=True)
                sections[key] = text
                break

    # 본문 조합
    content_parts = []
    for key in section_keys:
        if key in sections:
            content_parts.append(f"[{key}]\n{sections[key]}")
    content = "\n\n".join(content_parts) if content_parts else soup.get_text(separator="\n", strip=True)

    url = f"{BASE_URL}/odr/cm/in/exmplBjItem.do?brdId={BRD_ID}&seq={seq}"

    return {
        "title": title,
        "date": date_str,
        "url": url,
        "content": content,
        "source": "소비자원",
        "_date_obj": date_obj,
    }


if __name__ == "__main__":
    results = scrape_kca(2025, 4)
    for r in results:
        print(f"- {r['date']} | {r['title'][:50]}")
    print(f"총 {len(results)}건")
