"""
Claude API를 사용하여 판례/조정례를 4섹션 구조로 변환
섹션: 사건 요약 / 사실 관계 / 판시 사항 / 활용 방안
"""

import anthropic
import time
import re


CLIENT = None


def get_client() -> anthropic.Anthropic:
    global CLIENT
    if CLIENT is None:
        CLIENT = anthropic.Anthropic()
    return CLIENT


SYSTEM_PROMPT = """당신은 한국 보험업계 전문 법률 분석가입니다.
판례 또는 분쟁조정사례 원문을 읽고, 보험 위원회 임원들이 빠르게 핵심을 파악할 수 있도록
아래 4개 섹션으로 구조화해 주세요.

출력 형식 (반드시 이 형식 그대로):
1. 사건 요약
[2~3문장으로 사건의 핵심을 요약]

2. 사실 관계
[사건의 배경과 주요 사실 관계를 서술]

3. 판시 사항
[법원/위원회의 판단 내용. 대법원 판례의 경우 법률 원문 표현 최대한 유지]

4. 활용 방안
[보험업계 실무자가 이 판례/사례를 어떻게 활용할 수 있는지 구체적으로 설명]

주의사항:
- 대법원 판례의 경우 '판시 사항' 섹션에서 법률 용어와 원문 표현을 최대한 유지할 것
- 임의 요약이나 과도한 축약 금지 (특히 판시 사항)
- 보험 분쟁조정사례의 경우 금융분쟁조정위원회의 판단 논리를 명확히 서술
- 각 섹션은 번호와 제목으로 시작할 것"""


def summarize_case(case: dict) -> dict:
    """
    단일 케이스를 4섹션으로 구조화
    입력: {"title": str, "date": str, "url": str, "content": str, "source": str, ...}
    출력: 입력 dict에 "summary" 키 추가 (4섹션 텍스트)
    """
    client = get_client()
    source = case.get("source", "")
    title = case.get("title", "")
    content = case.get("content", "")

    if not content or len(content.strip()) < 50:
        case["summary"] = _empty_summary(title)
        return case

    # 대법원 판례는 정확도 최우선 프롬프트 사용
    is_court = source == "대법원"
    user_message = _build_user_message(title, content, source, is_court)

    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}]
            )
            summary_text = response.content[0].text.strip()
            case["summary"] = summary_text
            return case
        except anthropic.RateLimitError:
            wait = 10 * attempt
            print(f"  [요약] API 한도 초과 - {wait}초 대기 후 재시도 ({attempt}/3)...")
            time.sleep(wait)
        except Exception as e:
            if attempt < 3:
                print(f"  [요약] API 오류 - 재시도 중 ({attempt}/3)... {e}")
                time.sleep(3)
            else:
                print(f"  [요약] API 최종 실패: {e}")
                case["summary"] = _empty_summary(title)
                return case

    case["summary"] = _empty_summary(title)
    return case


def _build_user_message(title: str, content: str, source: str, is_court: bool) -> str:
    # 내용이 너무 길면 앞부분 우선 (Claude context 절약, 대법원은 더 많이 포함)
    max_content_len = 8000 if is_court else 5000
    if len(content) > max_content_len:
        content = content[:max_content_len] + "\n\n[이하 원문 생략]"

    accuracy_note = ""
    if is_court:
        accuracy_note = "\n⚠️ 대법원 판례입니다. '판시 사항' 섹션에서 법률 원문 표현을 최대한 유지해 주세요."

    return f"""다음 {source} 사례를 4섹션으로 구조화해 주세요.{accuracy_note}

제목: {title}

원문 내용:
{content}"""


def _empty_summary(title: str) -> str:
    return f"""1. 사건 요약
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
        "사건_요약": "",
        "사실_관계": "",
        "판시_사항": "",
        "활용_방안": "",
    }

    patterns = [
        (r"1\.\s*사건\s*요약\s*\n(.*?)(?=2\.\s*사실|$)", "사건_요약"),
        (r"2\.\s*사실\s*관계\s*\n(.*?)(?=3\.\s*판시|$)", "사실_관계"),
        (r"3\.\s*판시\s*사항\s*\n(.*?)(?=4\.\s*활용|$)", "판시_사항"),
        (r"4\.\s*활용\s*방안\s*\n(.*?)$", "활용_방안"),
    ]

    for pattern, key in patterns:
        match = re.search(pattern, summary_text, re.DOTALL)
        if match:
            sections[key] = match.group(1).strip()

    return sections
