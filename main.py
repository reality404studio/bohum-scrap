"""
분기별 판례/분쟁조정사례 자동 수집 및 보고서 생성
사용법: python main.py --year 2025 --quarter 2
"""

import argparse
import sys
import os
import time
from datetime import datetime

from scrapers.fss import scrape_fss
from scrapers.kca import scrape_kca
from scrapers.court import scrape_court
from summarizer import summarize_case
from writer import write_docx, write_html


def parse_args():
    parser = argparse.ArgumentParser(
        description="분기별 보험 판례/분쟁조정사례 자동 수집 보고서 생성"
    )
    parser.add_argument("--year", type=int, required=True, help="연도 (예: 2025)")
    parser.add_argument("--quarter", type=int, required=True, choices=[1, 2, 3, 4], help="분기 (1~4)")
    parser.add_argument("--output", type=str, default=".", help="출력 디렉토리 (기본값: 현재 디렉토리)")
    parser.add_argument("--skip-fss", action="store_true", help="금감원 수집 건너뜀")
    parser.add_argument("--skip-kca", action="store_true", help="소비자원 수집 건너뜀")
    parser.add_argument("--skip-court", action="store_true", help="대법원 수집 건너뜀")
    return parser.parse_args()


def run_scraper_safely(name: str, scraper_fn, *args) -> list[dict]:
    """스크래퍼 실행, 실패 시 빈 리스트 반환"""
    try:
        results = scraper_fn(*args)
        if results:
            print(f"[✓] {name} 수집 완료: {len(results)}건")
        else:
            print(f"[!] {name} 수집 결과 없음 (날짜 범위 내 데이터 없거나 접속 실패)")
        return results or []
    except Exception as e:
        print(f"[✗] {name} 수집 실패: {e}")
        return []


def main():
    args = parse_args()
    year = args.year
    quarter = args.quarter
    output_dir = args.output

    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print(f"  분기별 판례정리 자동화 시작")
    print(f"  대상: {year}년 {quarter}분기")
    print(f"  시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_cases: list[dict] = []

    # ── Step 1: 대법원 수집 ──────────────────────────────
    if not args.skip_court:
        print("\n[Step 1/3] 대법원 판례 수집 중...")
        court_cases = run_scraper_safely("대법원", scrape_court, year, quarter)
        for c in court_cases:
            c["source"] = "대법원"
        all_cases.extend(court_cases)
    else:
        print("\n[Step 1/3] 대법원 수집 건너뜀")

    # ── Step 2: 금감원 수집 ──────────────────────────────
    if not args.skip_fss:
        print("\n[Step 2/3] 금감원 분쟁조정사례 수집 중...")
        fss_cases = run_scraper_safely("금감원", scrape_fss, year, quarter)
        for c in fss_cases:
            c["source"] = "금감원"
        all_cases.extend(fss_cases)
    else:
        print("\n[Step 2/3] 금감원 수집 건너뜀")

    # ── Step 3: 소비자원 수집 ────────────────────────────
    if not args.skip_kca:
        print("\n[Step 3/3] 소비자원 분쟁조정결정례 수집 중...")
        kca_cases = run_scraper_safely("소비자원", scrape_kca, year, quarter)
        for c in kca_cases:
            c["source"] = "소비자원"
        all_cases.extend(kca_cases)
    else:
        print("\n[Step 3/3] 소비자원 수집 건너뜀")

    print(f"\n{'=' * 60}")
    print(f"  전체 수집 완료: {len(all_cases)}건")
    print(f"{'=' * 60}")

    if not all_cases:
        print("\n[!] 수집된 케이스가 없습니다. ANTHROPIC_API_KEY 확인 및 사이트 접속 상태를 점검하세요.")
        print("    GitHub Actions 로그에서 각 사이트별 오류 메시지를 확인하세요.")
        sys.exit(1)

    # ── Step 4: Claude API 요약 생성 ─────────────────────
    print(f"\n[Step 4] Claude API로 4섹션 구조화 중... ({len(all_cases)}건)")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[!] ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        print("    GitHub Secrets에 ANTHROPIC_API_KEY를 등록해 주세요.")
        sys.exit(1)

    for i, case in enumerate(all_cases, 1):
        source = case.get("source", "")
        title = case.get("title", "")[:40]
        print(f"  [{i}/{len(all_cases)}] {source} | {title}...")
        summarize_case(case)
        # API 호출 간격 (Rate Limit 방지)
        if i < len(all_cases):
            time.sleep(0.5)

    print(f"\n[✓] 요약 완료: {len(all_cases)}건")

    # ── Step 5: 파일 출력 ────────────────────────────────
    print(f"\n[Step 5] 출력 파일 생성 중...")
    docx_path = write_docx(all_cases, year, quarter, output_dir)
    html_path = write_html(all_cases, year, quarter, output_dir)

    print(f"\n{'=' * 60}")
    print(f"  완료!")
    print(f"  Word 파일: {docx_path}")
    print(f"  링크 파일: {html_path}")
    print(f"  종료 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
