"""
분기별 판례/분쟁조정사례 자동 수집 및 보고서 생성
사용법: python main.py --year 2025 --quarter 2
"""

import argparse
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    # ── Step 1: 3개 스크래퍼 병렬 수집 ──────────────────
    scraper_tasks = []
    if not args.skip_court:
        scraper_tasks.append(("대법원", scrape_court, "대법원"))
    if not args.skip_fss:
        scraper_tasks.append(("금감원", scrape_fss, "금감원"))
    if not args.skip_kca:
        scraper_tasks.append(("소비자원", scrape_kca, "소비자원"))

    print(f"\n[Step 1] {len(scraper_tasks)}개 소스 병렬 수집 중...")

    def scrape_one(name, fn, label):
        cases = run_scraper_safely(name, fn, year, quarter)
        for c in cases:
            c["source"] = label
        return label, cases

    source_results = {"대법원": [], "금감원": [], "소비자원": []}
    with ThreadPoolExecutor(max_workers=len(scraper_tasks) or 1) as executor:
        futures = [executor.submit(scrape_one, name, fn, label) for name, fn, label in scraper_tasks]
        for future in as_completed(futures):
            label, cases = future.result()
            source_results[label] = cases

    all_cases: list[dict] = []
    for label in ["대법원", "금감원", "소비자원"]:
        all_cases.extend(source_results[label])

    print(f"\n{'=' * 60}")
    print(f"  전체 수집 완료: {len(all_cases)}건")
    print(f"{'=' * 60}")

    if not all_cases:
        print("\n[!] 수집된 케이스가 없습니다. ANTHROPIC_API_KEY 확인 및 사이트 접속 상태를 점검하세요.")
        print("    GitHub Actions 로그에서 각 사이트별 오류 메시지를 확인하세요.")
        sys.exit(1)

    # ── Step 2: Claude API 요약 병렬 생성 ────────────────
    print(f"\n[Step 2] Claude API로 4섹션 구조화 중... ({len(all_cases)}건)")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[!] ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        print("    GitHub Secrets에 ANTHROPIC_API_KEY를 등록해 주세요.")
        sys.exit(1)

    completed = 0
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_case = {executor.submit(summarize_case, case): case for case in all_cases}
        for future in as_completed(future_to_case):
            case = future_to_case[future]
            completed += 1
            source = case.get("source", "")
            title = case.get("title", "")[:40]
            print(f"  [{completed}/{len(all_cases)}] {source} | {title}... 완료")
            future.result()

    print(f"\n[✓] 요약 완료: {len(all_cases)}건")

    # ── Step 3: 파일 출력 ────────────────────────────────
    print(f"\n[Step 3] 출력 파일 생성 중...")
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
