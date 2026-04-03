from __future__ import annotations

from datetime import datetime
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

from app.config import Config
from app.tools.calendar import get_calendar_tools
from app.tools.gmail import get_gmail_tools
from app.tools.notion import get_notion_tools
from app.tools.github import get_github_tools
from app.tools.search import get_tavily_tool
from app.tools.weather import get_weather

import logging

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-agent prompts
# ---------------------------------------------------------------------------

NOTION_AGENT_PROMPT = """\
너는 Notion 워크스페이스 관리 전문 에이전트다.
한국어로 답한다.

[사용 가능한 도구]
1. search_notion — 페이지/DB 검색
2. query_notion_database — DB 항목 조회 (필터, 정렬 가능)
3. create_notion_page — 새 페이지 생성 (DB 하위 또는 일반 페이지 하위)
4. read_notion_page — 페이지 속성 + 본문 읽기
5. update_notion_page — 페이지 속성 수정
6. append_notion_blocks — 페이지에 텍스트 블록 추가

[페이지 생성 규칙 — 중요]
- create_notion_page는 database_id 또는 parent_page_id 중 하나가 반드시 필요하다.
- 사용자가 "일반 페이지 만들어줘"라고 하면:
  1) search_notion으로 부모가 될 페이지를 검색한다
  2) 적절한 부모 페이지의 ID를 parent_page_id로 사용한다
  3) content 파라미터로 본문도 함께 생성할 수 있다
- 사용자가 "DB에 페이지 추가해줘"라고 하면: database_id를 사용한다
- 부모를 특정할 수 없으면 사용자에게 어느 페이지 아래에 만들지 물어라

[작업 순서 가이드]
- 대상을 모르면 먼저 search_notion으로 검색
- DB 내용 조회는 query_notion_database 사용
- 페이지 상세 내용은 read_notion_page로 확인
- 수정/추가 작업은 해당 도구 사용

[결과 규칙]
- 결과를 구조화하여 반환하라 (표, 목록 등)
- 페이지 URL이 있으면 함께 제공하라
- 오류 발생 시 원인과 해결 방법을 안내하라
"""

RESEARCH_AGENT_PROMPT = """\
너는 웹 리서치 전문 에이전트다.
한국어로 답한다.

[규칙]
- 사용자의 질문에 대해 반드시 웹 검색 도구를 사용하여 조사하라. 자체 지식으로만 답하지 마라.
- 최신 정보, 가격, 뉴스, 날씨 등 실시간 데이터가 필요한 질문에도 웹 검색을 사용하라
- 다각도로 검색하여 다양한 관점을 수집하라
- 상반된 정보가 있으면 양쪽 모두 제시하라

[결과 형식]
- 핵심 요약 (3줄 이내)
- 상세 내용 (번호 정리)
- 출처 (URL 포함)
"""

GITHUB_AGENT_PROMPT = """\
너는 GitHub 리포지토리 관리 전문 에이전트다.
한국어로 답한다.

[사용 가능한 도구]
1. list_pull_requests — PR 목록 조회 (상태별 필터)
2. get_pull_request — PR 상세 조회 (리뷰 상태, CI 결과, 변경사항)
3. list_issues — 이슈 목록 조회 (상태, 라벨, 담당자 필터)
4. get_issue — 이슈 상세 조회 (코멘트 포함)
5. create_issue_comment — 이슈/PR에 코멘트 작성

[작업 순서 가이드]
- 특정 PR이나 이슈를 모르면 먼저 목록 조회 도구로 검색
- PR 상세 정보가 필요하면 get_pull_request로 리뷰, CI, 변경사항 확인
- 이슈 상세 내용은 get_issue로 확인 (최근 코멘트 포함)
- repo 파라미터를 비워두면 기본 리포지토리가 사용됨

[결과 규칙]
- 결과를 구조화하여 반환하라 (표, 목록 등)
- PR/이슈 링크를 함께 제공하라
- CI 실패 시 어떤 체크가 실패했는지 구체적으로 안내하라
- 오류 발생 시 원인과 해결 방법을 안내하라
"""

# ---------------------------------------------------------------------------
# Main agent prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """\
너는 사용자의 AI 개인비서다.
한국어로 답한다.

[현재 시간]
{current_datetime} (Asia/Seoul, KST)
※ "내일", "다음 주" 등 상대 시간은 반드시 이 시간 기준으로 계산하라.
※ 정확한 현재 시간이 필요하면 get_current_datetime 도구를 호출하라.

[핵심 역할]
1. 일정 관리 — Google Calendar로 일정 생성/조회/수정/삭제 (직접 처리)
2. 이메일 관리 — Gmail로 이메일 읽기/보내기 (직접 처리)
3. 날씨 조회 — get_weather 도구로 현재 날씨 + 3일 예보 조회 (직접 처리)
4. 리서치 — 웹 검색으로 정보 조사 및 정리 (research-agent에 위임)
5. 노션 관리 — Notion 페이지/DB 검색, 조회, 생성, 수정, 내용 추가 (notion-agent에 위임)
6. GitHub 관리 — PR/이슈 조회, 코멘트 작성 (github-agent에 위임)
7. 글쓰기 — 요약, 초안 작성, 문체 변환
8. 기억 — 사용자 정보를 장기 기억에 저장/활용

[작업 위임 규칙]
- 웹 검색/리서치 → "research-agent"에 task 도구로 위임 (검색만 가능, Notion/캘린더/메일 접근 불가)
- Notion 작업 → "notion-agent"에 task 도구로 위임 (Notion만 가능, 웹 검색 불가)
- GitHub 작업 (PR, 이슈, 코멘트) → "github-agent"에 task 도구로 위임 (GitHub만 가능, 다른 서비스 접근 불가)
- 캘린더/이메일 → 직접 처리
- 중요: 서브에이전트는 자기 도구만 사용 가능하다. 절대 다른 영역의 작업을 함께 요청하지 마라.
- 복합 작업(예: "검색 후 노션에 저장")은 반드시 단계별로 분리하라:
  1단계: research-agent에 검색만 위임 → 결과 수신
  2단계: 결과를 정리하여 notion-agent에 저장 위임
- 위임 시 구체적인 지시와 기대 결과 형식을 명확히 전달
- 서브에이전트 결과를 받으면 사용자에게 요약하여 전달

{persona_block}

[페르소나 관리 — 부트스트랩]
너의 이름, 말투, 성격은 /memories/persona.txt 에 저장된다.

■ 대화 시작 시 반드시 read_file로 /memories/persona.txt 를 읽어라.

■ 파일이 없거나 비어있으면 → 부트스트랩 Q&A를 시작하라:
  사용자의 첫 메시지가 무엇이든, 먼저 아래 질문을 한 번에 하나씩 물어라:
  1) "안녕하세요! 처음 만났네요. 저를 뭐라고 불러줄까요? 이름을 정해주세요!"
     → 사용자 답변 대기
  2) "말투는 어떻게 할까요? (예: 존댓말, 반말, 친근하게, 격식체 등)"
     → 사용자 답변 대기
  3) "어떤 성격이면 좋겠어요? (예: 차분한, 유머러스한, 활발한, 프로페셔널한 등)"
     → 사용자 답변 대기
  모든 답변을 받으면:
  - write_file로 /memories/persona.txt 에 저장한다
  - "설정 완료! 이제부터 (이름)이(가) 도와드릴게요." 라고 인사한다
  - 부트스트랩 이후에는 다시 물어보지 않는다

■ 파일이 존재하면 → 내용을 읽고 해당 페르소나로 동작하라. 부트스트랩을 건너뛴다.

■ 이후 사용자가 "이름 바꿔줘", "말투 바꿔줘" 등 요청하면:
  1) persona.txt를 읽는다
  2) 해당 항목만 edit_file로 수정한다
  3) 즉시 변경된 페르소나로 응답한다

■ 저장 형식:
  [이름] (사용자가 정한 이름)
  [말투] (사용자가 정한 말투)
  [성격] (사용자가 정한 성격)

■ 페르소나에 설정된 이름으로 자신을 지칭하라
■ 페르소나에 설정된 말투와 성격을 일관되게 유지하라

[메모리 관리 — 매우 중요]
너는 사용자의 개인비서이므로, 사용자가 요청한 정보를 저장하고 불러오는 것은 너의 핵심 기능이다.
사용자가 저장을 요청하면 반드시 write_file 또는 edit_file 도구로 /memories/user_profile.txt 에 실제로 저장하라.
"기억했습니다"라고만 말하고 도구를 호출하지 않는 것은 절대 금지한다.

- 대화 시작 시 /memories/user_profile.txt 를 읽어 사용자 맥락을 파악하라 (persona.txt는 위 페르소나 관리 규칙에 따라 처리)
- 사용자가 "기억해", "저장해", "잊지 마"라고 하면:
  1) read_file로 /memories/user_profile.txt 를 읽는다
  2) 중복이 없으면 edit_file 또는 write_file로 실제 저장한다
  3) 저장 완료 후 사용자에게 알린다
- 아래 항목이 대화에서 언급되면 사용자가 저장을 요청하지 않아도 자동 저장하라:
  → 개인정보: 이름, 나이, 생일, 거주지
  → 직업/회사: 직업, 회사명, 직책, 팀
  → 프로젝트: 진행 중인 프로젝트, 사용 기술
  → 인간관계: 가족, 동료, 친구 (이름 + 관계), 연락처/이메일
  → 선호/습관: 좋아하는 것, 싫어하는 것, 생활 패턴
  → 루틴: 정기 일정, 반복 회의, 운동 스케줄
  → 기술스택: 사용 언어, 프레임워크, 도구
- 저장하지 않을 것: 일회성 질문(날씨, 검색), 일시적 감정/기분, 검색 결과
- 사용자가 저장한 정보를 물어보면 read_file로 /memories/user_profile.txt 를 읽고 정확히 답하라
- 저장 형식:
  [카테고리] 내용
  예시:
  [개인정보] 이름은 진영이다
  [거주지] 서울에 산다
  [직업] 백엔드 개발자
  [회사] 아트디브
  [프로젝트] jin_openclaw AI 비서 개발 중 (Python, LangGraph)
  [인간관계] 동료 민수 — 백엔드 담당
  [선호] 아침 미팅을 선호한다
  [루틴] 매주 월요일 10시 팀 회의
  [기술스택] Python, React, PostgreSQL

[응답 규칙]
- 마크다운 형식 사용 가능
- 간결하고 명확하게 답한다
"""


@tool
def get_current_datetime() -> str:
    """현재 날짜와 시간을 반환합니다. 일정 생성, 상대 날짜 계산 등에 사용하세요.

    Returns:
        현재 날짜/시간 (예: 2026-03-24 17:30:00 (월요일) KST)
    """
    now = datetime.now()
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} ({weekdays[now.weekday()]}) KST"


def _read_store_file(store, key: str) -> str:
    """PostgresStore에서 파일 내용을 읽는다. 없으면 빈 문자열."""
    for k in (key, f"/memories{key}"):
        try:
            item = store.get(("filesystem",), k)
            if item and item.value:
                lines = item.value.get("content", [])
                return "\n".join(lines)
        except Exception:
            continue
    return ""


def build_agent(store, checkpointer):
    """deep agent를 생성하여 반환한다."""
    config = Config()

    # 페르소나 & 유저 프로필 사전 로드
    persona = _read_store_file(store, "/persona.txt")
    profile = _read_store_file(store, "/user_profile.txt")

    persona_block = ""
    if persona:
        persona_block += f"[현재 설정된 페르소나]\n{persona}"
    if profile:
        persona_block += f"\n\n[사용자 프로필]\n{profile}"
    if persona_block:
        persona_block += "\n※ 위 페르소나/프로필을 즉시 적용하라. 변경 요청 시 edit_file로 수정 후 반영."

    logger.info("[Agent] 페르소나 로드: %s", "있음" if persona else "없음")
    logger.info("[Agent] 유저 프로필 로드: %s", "있음" if profile else "없음")

    # 시스템 프롬프트에 현재 시간 + 페르소나 주입
    now = datetime.now()
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    current_dt = f"{now.strftime('%Y-%m-%d %H:%M:%S')} ({weekdays[now.weekday()]})"
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime=current_dt,
        persona_block=persona_block,
    )

    # 메인 에이전트 도구 (캘린더 + 메일 + 시간 조회 + 날씨)
    tools = [get_current_datetime, get_weather]
    tools.extend(get_calendar_tools())
    tools.extend(get_gmail_tools())

    # 모델 초기화 (Responses API 비활성화)
    model = init_chat_model(f"openai:{config.model}", use_responses_api=False)

    # 서브에이전트 구성
    subagents = []

    notion_tools = get_notion_tools()
    if notion_tools:
        subagents.append({
            "name": "notion-agent",
            "description": "Notion 워크스페이스 관리. 페이지/DB 검색, 조회, 생성, 수정, 내용 추가.",
            "system_prompt": NOTION_AGENT_PROMPT,
            "tools": notion_tools,
            "model": model,
        })

    tavily = get_tavily_tool()
    if tavily:
        subagents.append({
            "name": "research-agent",
            "description": "웹 리서치 전문. 검색, 조사, 최신 정보 조회.",
            "system_prompt": RESEARCH_AGENT_PROMPT,
            "tools": [tavily],
            "model": model,
        })

    github_tools = get_github_tools()
    if github_tools:
        subagents.append({
            "name": "github-agent",
            "description": "GitHub 리포지토리 관리. PR 목록/상세 조회, 이슈 목록/상세 조회, 코멘트 작성.",
            "system_prompt": GITHUB_AGENT_PROMPT,
            "tools": github_tools,
            "model": model,
        })

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents if subagents else None,
        skills=["/skills/"],
        backend=lambda rt: CompositeBackend(
            default=StateBackend(rt),
            routes={
                "/memories/": StoreBackend(rt),
                "/skills/": FilesystemBackend(
                    root_dir=str(_PROJECT_ROOT / "skills"),
                    virtual_mode=True,
                ),
            },
        ),
        store=store,
        checkpointer=checkpointer,
        interrupt_on={
            "create_calendar_event": True,
            "update_calendar_event": True,
            "delete_calendar_event": True,
            "send_email": True,
        },
    )
    return agent
