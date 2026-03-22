"""
대법원 판례 스크래퍼
목록 API: POST https://portal.scourt.go.kr/pgp/pgp1011/selectJdcpctSrchRsltLst.on
상세 API: POST https://portal.scourt.go.kr/pgp/pgp1011/selectJdcpctCtxt.on
검색어: 보험금, 날짜 필터: 해당 분기
방식: requests JSON API (Playwright/법제처 API 불필요)
"""

import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://portal.scourt.go.kr"
LIST_URL = f"{BASE_URL}/pgp/pgp1011/selectJdcpctSrchRsltLst.on"
DETAIL_URL = f"{BASE_URL}/pgp/pgp1011/selectJdcpctCtxt.on"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/pgp/index.on?m=PGP1011M01&l=N&c=900",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}

QUARTER_RANGES = {
    1: ("0101", "0331"),
    2: ("0401", "0630"),
    3: ("0701", "0930"),
    4: ("1001", "1231"),
}

PAGE_SIZE = 20
JDCPCT_GR_CD = "111|112|130|141|180|182|232|235|201"  # 판례 그룹 코드


def scrape_court(year: int, quarter: int) -> list[dict]:
    """
    대법원 판례 수집 (검색어: 보험금, 해당 분기)
    반환: [{"title": str, "date": str, "url": str, "content": str, "source": str}, ...]
    """
    sm, em = QUARTER_RANGES[quarter]
    date_from = f"{year}{sm}"
    date_to = f"{year}{em}"

    print(f"[대법원] 수집 시작: {date_from} ~ {date_to} (검색어: 보험금)")

    session = requests.Session()
    session.headers.update(HEADERS)

    cases = []
    page_no = 1
    total_count = None

    while True:
        payload = {
            "dma_searchParam": {
                "srchwd": "보험금",
                "sort": "jis_jdcpc_instn_dvs_cd_s asc, $relevance desc, prnjdg_ymd_o desc, jdcpct_gr_cd_s asc",
                "sortType": "정확도",
                "searchRange": "",
                "tpcJdcpctCsAlsYn": "",
                "csNoLstCtt": "",
                "csNmLstCtt": "",
                "prvsRefcCtt": "",
                "searchScope": "",
                "jisJdcpcInstnDvsCd": "",
                "jdcpctCdcsCd": "",
                "prnjdgYmdFrom": date_from,
                "prnjdgYmdTo": date_to,
                "grpJdcpctGrCd": "",
                "cortNm": "",
                "pageNo": str(page_no),
                "jisJdcpcInstnDvsCdGrp": "",
                "grpJdcpctGrCdGrp": "",
                "jdcpctCdcsCdGrp": "",
                "adjdTypCdGrp": "",
                "pageSize": str(PAGE_SIZE),
                "reSrchFlag": "",
                "befSrchwd": "보험금",
                "preSrchConditions": "",
                "initYn": "N",
                "totalCount": str(total_count) if total_count else "",
                "jdcpctGrCd": JDCPCT_GR_CD,
                "category": "jdcpct",
                "isKwdSearch": "N",
            }
        }

        try:
            resp = session.post(LIST_URL, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[대법원] 목록 API 오류 (page={page_no}): {e}")
            break

        if data.get("status") != 200:
            print(f"[대법원] API 오류 응답: {data.get('message')}")
            break

        result_list = data.get("data", {}).get("dlt_jdcpctRslt", [])
        if isinstance(result_list, dict):
            result_list = [result_list]

        if total_count is None:
            # 첫 응답에서 총 건수 파악
            try:
                total_count = int(data.get("data", {}).get("totalCount", 0))
            except (TypeError, ValueError):
                total_count = len(result_list)
            print(f"[대법원] 총 {total_count}건 검색됨")

        if not result_list:
            break

        for item in result_list:
            jis_srno = item.get("jisCntntsSrno", "")
            case_no = item.get("csNmLstCtt", "")
            case_alias = item.get("jdcpctCsAlsNm", "")
            court = item.get("cortNm", "")
            date_raw = item.get("prnjdgYmd", "")  # YYYYMMDD

            # 날짜 파싱
            date_str = ""
            if date_raw and len(date_raw) == 8:
                try:
                    date_str = datetime.strptime(date_raw, "%Y%m%d").strftime("%Y.%m.%d")
                except ValueError:
                    pass

            title_parts = [p for p in [court, case_no, case_alias] if p]
            title = " ".join(title_parts) if title_parts else f"판례 {jis_srno}"

            url = f"{BASE_URL}/pgp/main.on?w2xPath=PGP1011M04&jisCntntsSrno={jis_srno}&c=900&srchwd=보험금"

            cases.append({
                "title": title,
                "date": date_str,
                "url": url,
                "jis_srno": str(jis_srno),
                "content": "",
                "source": "대법원",
            })

        fetched = (page_no - 1) * PAGE_SIZE + len(result_list)
        if total_count and fetched >= total_count:
            break
        if len(result_list) < PAGE_SIZE:
            break

        page_no += 1
        time.sleep(0.5)

    print(f"[대법원] 목록 수집 완료: {len(cases)}건")

    # 본문(전문) 병렬 수집
    completed = 0
    total = len(cases)
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_case = {
            executor.submit(_fetch_full_text_parallel, case["jis_srno"]): case
            for case in cases
        }
        for future in as_completed(future_to_case):
            case = future_to_case[future]
            completed += 1
            print(f"[대법원] 원문 수집 중 ({completed}/{total}): {case['title'][:45]}...")
            case["content"] = future.result()

    print(f"[대법원] 수집 완료: {len(cases)}건")
    return cases


def _fetch_full_text_parallel(jis_srno: str) -> str:
    """병렬 실행용 — 독립 세션 생성"""
    s = requests.Session()
    s.headers.update(HEADERS)
    return _fetch_full_text(s, jis_srno)


def _fetch_full_text(session: requests.Session, jis_srno: str) -> str:
    """판례 전문 API 조회"""
    payload = {
        "dma_searchParam": {
            "jisCntntsSrno": jis_srno,
            "srchwd": "보험금",
            "csNoLstCtt": "",
            "cortNm": "",
            "adjdTypNm": "",
            "jdcpctBrncNo": "",
            "jdcpctGrCd": "A1|A2|C|D3|H|H2|W2|W5|J1",
            "chnchrYn": "N",
            "systmNm": "PGP",
        }
    }

    try:
        resp = session.post(DETAIL_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"[원문 수집 실패: {e}]"

    if data.get("status") != 200:
        return f"[원문 수집 실패: {data.get('message')}]"

    ctxt = data.get("data", {}).get("dma_jdcpctCtxt", {})

    # 전문 HTML → 텍스트 변환
    html_content = ctxt.get("orgdocXmlCtt", "")
    if html_content:
        soup = BeautifulSoup(html_content, "lxml")
        return soup.get_text(separator="\n", strip=True)

    # 전문이 없으면 요약본 사용
    summary = ctxt.get("jdcpctSumrCtt", "") or ctxt.get("jdcpctXmlCtt", "")
    if summary:
        soup = BeautifulSoup(summary, "lxml")
        return soup.get_text(separator="\n", strip=True)

    return "[원문 없음]"


if __name__ == "__main__":
    results = scrape_court(2025, 4)
    for r in results:
        print(f"- {r['date']} | {r['title'][:50]}")
    print(f"총 {len(results)}건")
