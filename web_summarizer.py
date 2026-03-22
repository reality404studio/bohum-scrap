"""
판례요약 웹서비스 - 요약 전용 Flask 앱
JSON 파일 업로드 → Claude API 4섹션 요약 → .docx 다운로드
API Key는 서버 환경변수에만 존재 (클라이언트에 노출 없음)
"""

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from flask import Flask, request, send_file, render_template_string

from summarizer import summarize_case
from writer import write_docx


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB


# ── HTML 템플릿 ────────────────────────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>판례요약 서비스</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Malgun Gothic', '맑은 고딕', sans-serif;
      background: #f0f2f5;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      min-height: 100vh;
      padding: 40px 16px;
    }
    .card {
      background: white;
      border-radius: 12px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.08);
      padding: 40px;
      width: 100%;
      max-width: 560px;
    }
    h1 {
      font-size: 22px;
      color: #1a3a6b;
      margin-bottom: 6px;
    }
    .subtitle {
      color: #666;
      font-size: 14px;
      margin-bottom: 32px;
    }
    .step {
      display: flex;
      gap: 16px;
      margin-bottom: 28px;
    }
    .step-num {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: #4472C4;
      color: white;
      font-size: 14px;
      font-weight: bold;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      margin-top: 2px;
    }
    .step-body label {
      display: block;
      font-weight: bold;
      margin-bottom: 6px;
      color: #333;
    }
    .step-body p {
      font-size: 13px;
      color: #666;
      margin-bottom: 8px;
    }
    .row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    input[type="number"] {
      width: 80px;
      padding: 8px 10px;
      border: 1px solid #ddd;
      border-radius: 6px;
      font-size: 14px;
    }
    select {
      padding: 8px 10px;
      border: 1px solid #ddd;
      border-radius: 6px;
      font-size: 14px;
    }
    .drop-zone {
      border: 2px dashed #aac4e8;
      border-radius: 8px;
      padding: 24px;
      text-align: center;
      cursor: pointer;
      transition: background 0.2s;
      color: #666;
      font-size: 14px;
    }
    .drop-zone.dragover {
      background: #eaf0fb;
      border-color: #4472C4;
    }
    .drop-zone input[type="file"] { display: none; }
    .file-name {
      margin-top: 8px;
      font-size: 13px;
      color: #4472C4;
    }
    .btn {
      width: 100%;
      padding: 14px;
      background: #4472C4;
      color: white;
      border: none;
      border-radius: 8px;
      font-size: 16px;
      font-family: inherit;
      cursor: pointer;
      margin-top: 24px;
      transition: background 0.2s;
    }
    .btn:hover { background: #2f58a8; }
    .btn:disabled { background: #aaa; cursor: not-allowed; }
    .status {
      margin-top: 20px;
      padding: 14px;
      border-radius: 8px;
      font-size: 14px;
      display: none;
    }
    .status.info { background: #e8f0fe; color: #1a3a6b; }
    .status.error { background: #fce8e8; color: #c0392b; }
    .progress {
      margin-top: 10px;
      width: 100%;
      height: 6px;
      background: #dde4ef;
      border-radius: 3px;
      overflow: hidden;
      display: none;
    }
    .progress-bar {
      height: 100%;
      background: #4472C4;
      width: 0%;
      transition: width 0.4s;
      animation: indeterminate 1.5s infinite;
    }
    @keyframes indeterminate {
      0% { transform: translateX(-100%); width: 60%; }
      100% { transform: translateX(200%); width: 60%; }
    }
    .note {
      margin-top: 28px;
      padding: 12px;
      background: #f8f9fa;
      border-radius: 6px;
      font-size: 12px;
      color: #888;
    }
  </style>
</head>
<body>
<div class="card">
  <h1>판례요약 서비스</h1>
  <p class="subtitle">판례수집기(.exe)에서 생성한 JSON 파일을 업로드하면<br>Claude AI가 4섹션 구조로 요약한 Word 파일을 생성합니다.</p>

  <form id="uploadForm">
    <div class="step">
      <div class="step-num">1</div>
      <div class="step-body">
        <label>연도 / 분기 입력</label>
        <div class="row">
          <input type="number" id="year" name="year" min="2020" max="2100"
                 value="{{ current_year }}" required>
          <span style="color:#666">년</span>
          <select id="quarter" name="quarter">
            <option value="1">1분기</option>
            <option value="2">2분기</option>
            <option value="3">3분기</option>
            <option value="4">4분기</option>
          </select>
        </div>
      </div>
    </div>

    <div class="step">
      <div class="step-num">2</div>
      <div class="step-body">
        <label>JSON 파일 업로드</label>
        <p>판례수집기가 생성한 <code>*_raw.json</code> 파일을 선택하세요.</p>
        <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
          <input type="file" id="fileInput" name="file" accept=".json" required>
          📂 클릭하거나 파일을 여기에 끌어다 놓으세요
        </div>
        <div class="file-name" id="fileName"></div>
      </div>
    </div>

    <button type="submit" class="btn" id="submitBtn">요약 생성 (Word 다운로드)</button>
  </form>

  <div class="progress" id="progress"><div class="progress-bar"></div></div>
  <div class="status" id="status"></div>

  <div class="note">
    ⚠️ 요약 생성에 1~3분이 소요됩니다. 완료되면 자동으로 .docx 파일이 다운로드됩니다.
  </div>
</div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileName = document.getElementById('fileName');

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) fileName.textContent = '선택된 파일: ' + fileInput.files[0].name;
});
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file && file.name.endsWith('.json')) {
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    fileName.textContent = '선택된 파일: ' + file.name;
  }
});

document.getElementById('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const status = document.getElementById('status');
  const progress = document.getElementById('progress');

  btn.disabled = true;
  btn.textContent = '요약 생성 중...';
  status.style.display = 'block';
  status.className = 'status info';
  status.textContent = 'Claude AI가 4섹션 요약을 생성하고 있습니다. 잠시만 기다려 주세요...';
  progress.style.display = 'block';

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  formData.append('year', document.getElementById('year').value);
  formData.append('quarter', document.getElementById('quarter').value);

  try {
    const resp = await fetch('/summarize', { method: 'POST', body: formData });
    if (resp.ok) {
      const blob = await resp.blob();
      const cd = resp.headers.get('Content-Disposition') || '';
      const match = cd.match(/filename[^;=\\n]*=([^;\\n]*)/);
      const dlName = match ? match[1].replace(/['"]/g, '') : '판례정리.docx';
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = dlName; a.click();
      URL.revokeObjectURL(url);
      status.className = 'status info';
      status.textContent = '✅ 완료! Word 파일이 다운로드됩니다.';
    } else {
      const err = await resp.json().catch(() => ({error: '알 수 없는 오류'}));
      status.className = 'status error';
      status.textContent = '오류: ' + (err.error || resp.statusText);
    }
  } catch (err) {
    status.className = 'status error';
    status.textContent = '네트워크 오류: ' + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '요약 생성 (Word 다운로드)';
    progress.style.display = 'none';
  }
});
</script>
</body>
</html>"""


# ── 라우트 ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, current_year=datetime.now().year)


@app.route("/summarize", methods=["POST"])
def summarize():
    # 파일 검증
    if "file" not in request.files:
        return {"error": "JSON 파일이 없습니다."}, 400

    uploaded = request.files["file"]
    if not uploaded.filename or not uploaded.filename.endswith(".json"):
        return {"error": ".json 파일만 업로드 가능합니다."}, 400

    # API Key 확인
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "서버에 ANTHROPIC_API_KEY가 설정되지 않았습니다."}, 500

    # JSON 파싱
    try:
        cases = json.load(uploaded)
        if not isinstance(cases, list) or len(cases) == 0:
            return {"error": "JSON 파일에 케이스가 없습니다."}, 400
    except (json.JSONDecodeError, Exception) as e:
        return {"error": f"JSON 파싱 실패: {e}"}, 400

    # 연도/분기 파싱 (폼 입력 우선, 폼 없으면 파일명에서 추출)
    try:
        year = int(request.form.get("year", 0))
        quarter = int(request.form.get("quarter", 0))
    except (ValueError, TypeError):
        year, quarter = 0, 0

    if not year or not quarter:
        # 파일명에서 추출: 250101_4Q_raw.json → year=2025, quarter=4
        fname = uploaded.filename or ""
        m = re.search(r"(\d{2})(\d{2})(\d{2})_(\d)Q", fname)
        if m:
            year = 2000 + int(m.group(1))
            quarter = int(m.group(4))
        else:
            year = datetime.now().year
            quarter = ((datetime.now().month - 1) // 3) + 1

    # Claude API 병렬 요약
    completed = [0]
    total = len(cases)

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_case = {executor.submit(summarize_case, case): case for case in cases}
        for future in as_completed(future_to_case):
            future.result()
            completed[0] += 1

    # .docx 생성
    with tempfile.TemporaryDirectory() as tmp_dir:
        docx_path = write_docx(cases, year, quarter, tmp_dir)
        return send_file(
            docx_path,
            as_attachment=True,
            download_name=os.path.basename(docx_path),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
