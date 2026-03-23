"""
Claude API를 사용하여 판례/조정례를 4섹션 구조로 변환
섹션: 사건 요약 / 사실 관계 / 판시 사항 / 활용 방안

대법원: 2단계 (1단계 구조 추출 → 2단계 섹션 생성)
금감원/소비자원: 단일 개선 프롬프트
"""

import anthropic
import threading
import time
import re


CLIENT = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> anthropic.Anthropic:
    global CLIENT
    if CLIENT is None:
        with _CLIENT_LOCK:
            if CLIENT is None:
                CLIENT = anthropic.Anthropic()
    return CLIENT


# ── 금감원/소비자원용 단일 프롬프트 ────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 한국 보험업계 전문 법률 분석가입니다.
판례 또는 분쟁조정사례 원문을 읽고, 보험 위원회 임원들이 회의 전 5분 안에 핵심을 파악할 수 있도록
아래 형식으로 구조화해 주세요.

출력 형식 (반드시 이 형식 그대로):
제목: [기관명 핵심쟁점 한줄]

1. 사건 요약
[2문장. 법률 절차가 아닌 실무적 결론 중심. "~한 경우 ~해야 한다고 판단한 사례" 형식 권장]

2. 사실 관계
[서술형 3~5문장. 수치(금액, 날짜)는 꼭 필요한 것만. 당사자 관계, 사고 경위, 쟁점이 된 행위만 포함]

3. 판시 사항
[핵심 법리 3~5문장. "~라고 판시함", "~라고 명확히 밝힘" 형식으로 법원/위원회의 판단 논리를 구조화]

4. 활용 방안
[보험사·공단 등 실무 주체가 어떤 상황에서 어떻게 활용할 수 있는지 구체적으로. 3~5문장. 일반계약자 관점 제외]

주의사항:
- 각 섹션의 분량 기준을 반드시 지킬 것
- 사건 요약은 법률 용어보다 실무적 의미 중심으로
- 모든 문장 어미는 반드시 명사구형(~함/~임/~됨)으로 종결할 것. "~합니다", "~입니다", "~했습니다" 등 서술형 종결어미 사용 금지
- 활용 방안은 "보험사 입장에서는", "공단 입장에서는" 같은 주체 레이블 없이 내용만 서술
- 보험 분쟁조정사례의 경우 금융분쟁조정위원회의 판단 논리를 명확히 서술
- 각 섹션은 번호와 제목으로 시작할 것
- 마크다운 강조 기호(**, __, ## 등) 사용 금지
- <strong>, <br>, <p> 등 HTML 태그 사용 금지. 순수 텍스트만 출력할 것"""


# ── 대법원용 2단계 프롬프트 ────────────────────────────────────────────────────

COURT_EXTRACT_SYSTEM = """당신은 한국 대법원 판례를 분석하는 법률 전문가입니다.
판례 원문에서 핵심 항목을 정확하게 추출하세요. 원문 법률 용어를 그대로 사용하되 항목당 1~3문장으로 제한합니다."""

COURT_SUMMARIZE_SYSTEM = """당신은 한국 보험업계 전문 법률 분석가입니다.
제공된 판례 추출 내용을 바탕으로 보험 위원회 임원용 4섹션 요약을 작성하세요.
원문을 다시 참조하지 말고, 추출된 내용만 사용합니다.

출력 형식 (반드시 이 형식 그대로):
제목: [대법원 판결일(예: 2025. 5. 15.) 선고 사건번호 판결 핵심쟁점 한줄]

1. 사건 요약
[실무적 결론 중심 2문장. "~한 경우 ~해야 한다고 판단한 사례" 형식 권장]

2. 사실 관계
[서술형 3~4문장. 수치는 꼭 필요한 것만]

3. 판시 사항
[추출된 대법원 판단 기반, 핵심 법률 용어 유지, 3~5문장. 원문 덩어리 복붙 금지]

4. 활용 방안
[보험사·공단 등 실무 주체가 어떤 상황에서 어떻게 활용할 수 있는지 구체적으로. 3~5문장. 일반계약자 관점 제외]

주의사항:
- 각 섹션은 번호와 제목으로 시작할 것
- 모든 문장 어미는 반드시 명사구형(~함/~임/~됨)으로 종결할 것. "~합니다", "~입니다" 등 서술형 종결어미 사용 금지
- 활용 방안은 "보험사 입장에서는", "공단 입장에서는" 같은 주체 레이블 없이 내용만 서술
- 마크다운 강조 기호(**, __, ## 등) 사용 금지
- <strong>, <br>, <p> 등 HTML 태그 사용 금지. 순수 텍스트만 출력할 것
- 제목의 판결일은 판결일 필드에서 가져오되, YYYY. M. D. 형식으로 작성 (월/일 앞 0 불필요, 마침표 포함)"""


def summarize_case(case: dict) -> dict:
    """
    단일 케이스를 4섹션으로 구조화
    입력: {"title": str, "date": str, "url": str, "content": str, "source": str, ...}
    출력: 입력 dict에 "summary" 키 추가 (4섹션 텍스트)
    """
    source = case.get("source", "")
    title = case.get("title", "")
    content = case.get("content", "")

    if not content or len(content.strip()) < 50:
        case["summary"] = _empty_summary(title)
        return case

    if source == "대법원":
        return _summarize_court(case, title, content)
    else:
        return _summarize_single(case, title, content, source)


def _summarize_single(case: dict, title: str, content: str, source: str) -> dict:
    """금감원/소비자원: 단일 프롬프트"""
    if len(content) > 5000:
        content = content[:5000] + "\n\n[이하 원문 생략]"

    user_message = f"""다음 {source} 사례를 4섹션으로 구조화해 주세요.

제목: {title}

원문 내용:
{content}"""

    return _call_api(case, title, SYSTEM_PROMPT, user_message)


def _summarize_court(case: dict, title: str, content: str) -> dict:
    """대법원: 2단계 (추출 → 요약)"""
    client = get_client()

    if len(content) > 8000:
        content = content[:8000] + "\n\n[이하 원문 생략]"

    # 1단계: 구조 추출
    extract_message = f"""다음 대법원 판례에서 아래 항목을 추출하세요. 원문 표현을 그대로 사용하되 항목당 1~3문장으로 제한합니다.

제목: {title}

원문:
{content}

추출 항목:
- 핵심 법률 쟁점: (이 판례가 판단한 법적 질문 1문장)
- 당사자 관계: (원고/피고 각각의 역할)
- 핵심 사실: (쟁점과 직접 관련된 사실만 3개 이하)
- 원심 판단: (원심이 왜 틀렸는지)
- 대법원 판단: (대법원이 내린 결론과 핵심 법리, 원문 법률 용어 유지)
- 결과: (파기환송/인용/기각)"""

    extracted = None
    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=COURT_EXTRACT_SYSTEM,
                messages=[{"role": "user", "content": extract_message}]
            )
            extracted = response.content[0].text.strip()
            break
        except anthropic.RateLimitError:
            wait = 10 * attempt
            print(f"  [추출] API 한도 초과 - {wait}초 대기 ({attempt}/3)...")
            time.sleep(wait)
        except Exception as e:
            if attempt < 3:
                print(f"  [추출] API 오류 - 재시도 ({attempt}/3)... {e}")
                time.sleep(3)
            else:
                print(f"  [추출] 실패, 단일 프롬프트로 폴백: {e}")
                return _summarize_single(case, title, content, "대법원")

    if not extracted:
        return _summarize_single(case, title, content, "대법원")

    # 2단계: 섹션 생성
    summarize_message = f"""다음은 대법원 판례 추출 내용입니다. 이를 바탕으로 4섹션 요약을 작성하세요.

제목: {title}
판결일: {case.get("date", "")}

추출 내용:
{extracted}"""

    return _call_api(case, title, COURT_SUMMARIZE_SYSTEM, summarize_message)


def _call_api(case: dict, title: str, system: str, user_message: str) -> dict:
    """API 호출 공통 로직 (재시도 포함)"""
    client = get_client()

    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": user_message}]
            )
            case["summary"] = response.content[0].text.strip()
            return case
        except anthropic.RateLimitError:
            wait = 10 * attempt
            print(f"  [요약] API 한도 초과 - {wait}초 대기 ({attempt}/3)...")
            time.sleep(wait)
        except Exception as e:
            if attempt < 3:
                print(f"  [요약] API 오류 - 재시도 ({attempt}/3)... {e}")
                time.sleep(3)
            else:
                print(f"  [요약] API 최종 실패: {e}")
                case["summary"] = _empty_summary(title)
                return case

    case["summary"] = _empty_summary(title)
    return case


def _empty_summary(title: str) -> str:
    return """1. 사건 요약
[원문 내용 수집 실패로 요약 불가]

2. 사실 관계
[원문 내용 수집 실패로 서술 불가]

3. 판시 사항
[원문 내용 수집 실패로 서술 불가]

4. 활용 방안
[원문 내용 수집 실패로 서술 불가]"""


def parse_summary_sections(summary_text: str) -> dict:
    """
    4섹션 텍스트를 딕셔너리로 파싱
    반환: {"사건_요약": str, "사실_관계": str, "판시_사항": str, "활용_방안": str}
    """
    sections = {
        "제목": "",
        "사건_요약": "",
        "사실_관계": "",
        "판시_사항": "",
        "활용_방안": "",
    }

    patterns = [
        (r"^제목:\s*(.+?)(?=\n|$)", "제목"),
        (r"1\.\s*사건\s*요약\s*\n(.*?)(?=2\.\s*사실|$)", "사건_요약"),
        (r"2\.\s*사실\s*관계\s*\n(.*?)(?=3\.\s*판시|$)", "사실_관계"),
        (r"3\.\s*판시\s*사항\s*\n(.*?)(?=4\.\s*활용|$)", "판시_사항"),
        (r"4\.\s*활용\s*방안\s*\n(.*?)$", "활용_방안"),
    ]

    for pattern, key in patterns:
        flags = re.MULTILINE if key == "제목" else re.DOTALL
        match = re.search(pattern, summary_text, flags)
        if match:
            sections[key] = match.group(1).strip()

    return sections
