"""
판례수집기 - 스크래핑 전용 GUI
API Key 불필요. 공개 정부 사이트(금감원/소비자원/대법원)만 접속.
출력: YYMMDD_NQ_원문링크.html + YYMMDD_NQ_raw.json
"""

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from scrapers.fss import scrape_fss
from scrapers.kca import scrape_kca
from scrapers.court import scrape_court
from writer import write_html


def get_desktop_path() -> str:
    """Desktop 경로 반환 (Windows/Mac 모두 동작)"""
    return str(Path.home() / "Desktop")


def run_scraper_safely(name: str, scraper_fn, *args) -> list:
    try:
        results = scraper_fn(*args)
        return results or []
    except Exception as e:
        raise RuntimeError(f"{name} 수집 실패: {e}") from e


class ScraperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("판례수집기")
        self.resizable(False, False)
        self._build_ui()
        self._scraping = False

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        # ── 입력 프레임 ──────────────────────────────────────
        input_frame = ttk.LabelFrame(self, text="수집 설정", padding=10)
        input_frame.grid(row=0, column=0, sticky="ew", **pad)

        ttk.Label(input_frame, text="연도:").grid(row=0, column=0, sticky="w")
        self.year_var = tk.StringVar(value=str(datetime.now().year))
        ttk.Entry(input_frame, textvariable=self.year_var, width=8).grid(row=0, column=1, sticky="w", padx=(4, 20))

        ttk.Label(input_frame, text="분기:").grid(row=0, column=2, sticky="w")
        self.quarter_var = tk.StringVar(value="1")
        ttk.Combobox(
            input_frame, textvariable=self.quarter_var,
            values=["1", "2", "3", "4"], width=4, state="readonly"
        ).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Label(input_frame, text="출력 폴더:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.output_var = tk.StringVar(value=get_desktop_path())
        ttk.Entry(input_frame, textvariable=self.output_var, width=40).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(4, 4), pady=(8, 0))
        ttk.Button(input_frame, text="찾아보기", command=self._browse_folder).grid(
            row=1, column=4, sticky="w", padx=(4, 0), pady=(8, 0))

        # ── 소스 선택 ─────────────────────────────────────────
        src_frame = ttk.LabelFrame(self, text="수집 대상", padding=10)
        src_frame.grid(row=1, column=0, sticky="ew", **pad)

        self.use_fss = tk.BooleanVar(value=True)
        self.use_kca = tk.BooleanVar(value=True)
        self.use_court = tk.BooleanVar(value=True)

        ttk.Checkbutton(src_frame, text="금융감독원 분쟁조정사례", variable=self.use_fss).grid(
            row=0, column=0, sticky="w", padx=10)
        ttk.Checkbutton(src_frame, text="한국소비자원 분쟁조정결정례", variable=self.use_kca).grid(
            row=0, column=1, sticky="w", padx=10)
        ttk.Checkbutton(src_frame, text="대법원 판례", variable=self.use_court).grid(
            row=0, column=2, sticky="w", padx=10)

        # ── 시작 버튼 ─────────────────────────────────────────
        self.start_btn = ttk.Button(self, text="수집 시작", command=self._start_scraping)
        self.start_btn.grid(row=2, column=0, pady=(0, 4))

        # ── 진행 로그 ─────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="진행 로그", padding=10)
        log_frame.grid(row=3, column=0, sticky="nsew", **pad)

        self.log_text = tk.Text(log_frame, width=70, height=16, state="disabled",
                                font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                                insertbackground="white")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # ── 완료 후 버튼 ──────────────────────────────────────
        self.open_btn = ttk.Button(self, text="폴더 열기", command=self._open_output_folder,
                                   state="disabled")
        self.open_btn.grid(row=4, column=0, pady=(0, 10))

        self.columnconfigure(0, weight=1)

    def _browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_var.get())
        if folder:
            self.output_var.set(folder)

    def _open_output_folder(self):
        folder = self.output_var.get()
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            os.system(f'open "{folder}"')
        else:
            os.system(f'xdg-open "{folder}"')

    def _log(self, msg: str):
        """스레드-안전 로그 출력"""
        def _append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _append)

    def _set_start_btn(self, enabled: bool):
        self.after(0, lambda: self.start_btn.config(state="normal" if enabled else "disabled"))

    def _set_open_btn(self, enabled: bool):
        self.after(0, lambda: self.open_btn.config(state="normal" if enabled else "disabled"))

    def _start_scraping(self):
        if self._scraping:
            return

        # 입력값 검증
        try:
            year = int(self.year_var.get())
            quarter = int(self.quarter_var.get())
            if not (2000 <= year <= 2100) or quarter not in (1, 2, 3, 4):
                raise ValueError
        except ValueError:
            messagebox.showerror("입력 오류", "연도와 분기를 올바르게 입력해 주세요.")
            return

        output_dir = self.output_var.get()
        if not os.path.isdir(output_dir):
            messagebox.showerror("입력 오류", "출력 폴더가 존재하지 않습니다.")
            return

        if not any([self.use_fss.get(), self.use_kca.get(), self.use_court.get()]):
            messagebox.showerror("입력 오류", "수집 대상을 하나 이상 선택해 주세요.")
            return

        self._scraping = True
        self._set_start_btn(False)
        self._set_open_btn(False)

        thread = threading.Thread(
            target=self._scrape_worker,
            args=(year, quarter, output_dir),
            daemon=True
        )
        thread.start()

    def _scrape_worker(self, year: int, quarter: int, output_dir: str):
        self._log("=" * 55)
        self._log(f"  판례수집기 시작")
        self._log(f"  대상: {year}년 {quarter}분기")
        self._log(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log("=" * 55)

        # 수집 태스크 구성
        scraper_tasks = []
        if self.use_fss.get():
            scraper_tasks.append(("금감원", scrape_fss, "금감원"))
        if self.use_kca.get():
            scraper_tasks.append(("소비자원", scrape_kca, "소비자원"))
        if self.use_court.get():
            scraper_tasks.append(("대법원", scrape_court, "대법원"))

        self._log(f"\n[수집] {len(scraper_tasks)}개 소스 병렬 수집 중...")

        def scrape_one(name, fn, label):
            try:
                results = run_scraper_safely(name, fn, year, quarter)
                for c in results:
                    c["source"] = label
                count = len(results)
                self._log(f"  [{'✓' if count else '!'}] {name}: {count}건")
                return label, results
            except Exception as e:
                self._log(f"  [✗] {name}: {e}")
                return label, []

        source_results = {"대법원": [], "금감원": [], "소비자원": []}
        with ThreadPoolExecutor(max_workers=len(scraper_tasks) or 1) as executor:
            futures = [executor.submit(scrape_one, name, fn, label)
                       for name, fn, label in scraper_tasks]
            for future in as_completed(futures):
                label, cases = future.result()
                source_results[label] = cases

        all_cases = []
        for label in ["대법원", "금감원", "소비자원"]:
            all_cases.extend(source_results[label])

        self._log(f"\n전체 수집 완료: {len(all_cases)}건")

        if not all_cases:
            self._log("\n[!] 수집된 케이스가 없습니다.")
            self._scraping = False
            self._set_start_btn(True)
            return

        # 파일 저장
        date_str = datetime.now().strftime("%y%m%d")
        self._log(f"\n[저장] 파일 생성 중...")

        try:
            html_path = write_html(all_cases, year, quarter, output_dir)
            self._log(f"  HTML: {html_path}")
        except Exception as e:
            self._log(f"  [!] HTML 저장 실패: {e}")
            html_path = None

        try:
            raw_path = os.path.join(output_dir, f"{date_str}_{quarter}Q_raw.json")
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(all_cases, f, ensure_ascii=False, indent=2)
            self._log(f"  JSON: {raw_path}")
        except Exception as e:
            self._log(f"  [!] JSON 저장 실패: {e}")
            raw_path = None

        self._log(f"\n완료! {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log("=" * 55)
        self._log("\nStep 2: 웹서비스에 JSON 파일을 업로드하면")
        self._log("        Claude API가 4섹션 요약 + .docx를 생성합니다.")

        self._scraping = False
        self._set_start_btn(True)
        self._set_open_btn(True)


def main():
    app = ScraperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
