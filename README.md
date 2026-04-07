# Jin비서

> **LLM 기반 멀티채널 AI 개인비서**
> 자연어 한 줄로 Google Calendar, Gmail, Notion, GitHub, 웹 검색을 한 번에.

---

## 한눈에 보기

```
┌────────────────────────────────────────────────────────────────┐
│                          Jin비서란?                             │
│                                                                │
│   "내일 오후 2시에 팀미팅 잡고, 관련 자료 노션에 정리해줘"        │
│                            │                                   │
│                            ▼                                   │
│   ① 현재 시간 확인  ② 일정 생성 (승인 UI)                       │
│   ③ 웹 검색        ④ 노션 페이지 작성  ⑤ 결과 요약             │
│                                                                │
│   → 하나의 자연어 명령이 여러 외부 서비스를 오케스트레이션      │
└────────────────────────────────────────────────────────────────┘
```

- **멀티채널**: TUI(터미널) + Discord + WebChat 동시 운영, 모두 같은 에이전트 공유
- **멀티에이전트**: Coordinator + Subagent 구조 (도메인별 격리)
- **Human-in-the-Loop**: 위험 작업(일정/이메일)은 사용자 승인 후 실행
- **영속 메모리**: PostgreSQL 기반 장기 기억 + 페르소나
- **자동 브리핑**: Heartbeat로 주기적 업무 알림

---

## 기술 스택

```
┌─────────────────┬──────────────────────────────────────────────┐
│  LLM / 에이전트  │  OpenAI GPT-4o-mini + deepagents (LangGraph) │
├─────────────────┼──────────────────────────────────────────────┤
│  백엔드          │  Python 3 · FastAPI · uvicorn                │
├─────────────────┼──────────────────────────────────────────────┤
│  실시간 통신     │  WebSocket (브라우저) · discord.py           │
├─────────────────┼──────────────────────────────────────────────┤
│  데이터베이스     │  PostgreSQL (langgraph-checkpoint-postgres)  │
├─────────────────┼──────────────────────────────────────────────┤
│  상태/체크포인트  │  MemorySaver (대화) · PostgresStore (메모리) │
├─────────────────┼──────────────────────────────────────────────┤
│  프론트엔드       │  Vanilla HTML/CSS/JS · marked.js (마크다운)  │
├─────────────────┼──────────────────────────────────────────────┤
│  외부 API        │  Google Calendar/Gmail · Notion · GitHub     │
│                 │  Tavily (웹검색) · wttr.in (날씨)             │
├─────────────────┼──────────────────────────────────────────────┤
│  인증            │  Google OAuth 2.0 · Bearer Token             │
└─────────────────┴──────────────────────────────────────────────┘
```

### 왜 이 스택인가?

| 선택 | 이유 |
|------|------|
| **deepagents + LangGraph** | 서브에이전트, 체크포인트, Human-in-the-Loop Interrupt를 기본 지원 |
| **FastAPI + WebSocket** | 스트리밍 응답/도구 호출 시각화/인터럽트 카드 UI를 양방향 실시간으로 전송 |
| **PostgreSQL** | 프로세스 재시작 후에도 사용자 프로필·페르소나 같은 장기 기억 유지 |
| **discord.py** | Embed + Button 기반의 네이티브 승인 UI 제공 |

---

## 시스템 아키텍처 (멀티채널)

```
                          ┌──────────────────┐
                          │     사용자        │
                          └──────────────────┘
            ┌───────────────────┼───────────────────┐
            │                   │                   │
            ▼                   ▼                   ▼
    ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
    │    TUI        │   │   Discord     │   │   WebChat     │
    │  (터미널)      │   │   (봇 UI)      │   │  (브라우저)    │
    │               │   │               │   │               │
    │  input()      │   │ discord.py    │   │ FastAPI       │
    │  동기 루프     │   │ Embed+Button  │   │ + WebSocket   │
    └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
            │                   │                   │
            └───────────────────┼───────────────────┘
                                │
                          thread_id 로 분리
                                │
                                ▼
                 ┌─────────────────────────────────┐
                 │       하나의 Agent 인스턴스       │
                 │      (deepagents CompiledGraph) │
                 └─────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
    ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
    │ PostgresStore │   │  MemorySaver  │   │ Filesystem    │
    │ (장기 메모리)   │   │  (대화 기록)   │   │ (/skills/)    │
    └───────────────┘   └───────────────┘   └───────────────┘
```

- `python -m app.main` **한 번 실행**으로 3채널이 동시에 살아있음
- **메인 스레드**: TUI · **데몬 스레드**: Discord · **데몬 스레드**: WebChat
- 각 채널은 고유 `thread_id`로 대화 컨텍스트를 분리하지만, **에이전트 인스턴스와 장기 메모리는 공유**

| 채널 | thread_id 형식 | 핵심 특징 |
|------|---------------|-----------|
| TUI | `tui-local` | 가장 단순한 입출력, 디버깅에 유리 |
| Discord | `discord-{user_id}` | 유저별 스레드, Button UI 승인 |
| WebChat | `webchat-{session_id}` | 브라우저, 도구 호출 실시간 시각화 |
| Heartbeat | `heartbeat`, `heartbeat-web` | 주기적 브리핑 전용 채널 |

---

## 에이전트 구조 (Coordinator + Subagent)

```
                    ┌──────────────────────────────────────┐
                    │          Main Agent (Coordinator)     │
                    │            openai:gpt-4o-mini         │
                    │                                       │
                    │  직접 보유 도구 (9개):                 │
                    │  ├─ Calendar   (4) — 생성/조회/수정/삭제 │
                    │  ├─ Gmail      (3) — 전송/목록/읽기    │
                    │  ├─ Weather    (1) — wttr.in          │
                    │  └─ DateTime   (1) — 현재 시간         │
                    │                                       │
                    │  위임 도구: task(...)                  │
                    └────────┬─────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┬──────────────┐
          ▼                  ▼                  ▼              ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐
  │ notion-agent │  │research-agent│  │ github-agent │  │  general-  │
  │              │  │              │  │              │  │  purpose   │
  │ Notion 도구 6│  │ Tavily 검색  │  │ GitHub 도구 5│  │ (빌트인)   │
  │ gpt-4o-mini  │  │ gpt-4o-mini  │  │ gpt-4o-mini  │  │            │
  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘
```

### 핵심 아이디어

- **Coordinator**: 요청을 분석 → 직접 처리하거나 적절한 서브에이전트에 `task` 도구로 위임
- **Subagent 격리**: 각 에이전트는 자신의 도메인 도구만 접근 가능 → 컨텍스트 오염 방지
- **상태 격리**: 서브에이전트는 단일 결과만 반환 후 소멸 → 메인 에이전트 컨텍스트 절약
- **Graceful Degradation**: 토큰 미설정 시 해당 서브에이전트만 비활성화, 나머지는 정상 동작

### 도구 상세

| 에이전트 | 도구 개수 | 주요 도구 |
|---------|-----------|-----------|
| **Main (직접)** | 9 | `create/list/update/delete_calendar_event`, `send/list/read_email`, `get_weather`, `get_current_datetime` |
| **notion-agent** | 6 | `search_notion`, `query_notion_database`, `create/read/update_notion_page`, `append_notion_blocks` |
| **github-agent** | 5 | `list/get_pull_requests`, `list/get_issues`, `create_issue_comment` |
| **research-agent** | 1 | `TavilySearch` (advanced depth, max 5) |
| **deepagents 빌트인** | 8 | `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `write_todos`, `task` |

---

## 핵심 기능

### 1. Human-in-the-Loop Interrupt

**위험한 작업은 사용자가 승인해야 실행됨.**

```
       에이전트                             사용자
          │                                  │
          │─ create_calendar_event(...) ─┐    │
          │                              │    │
          │         interrupt 발생        │    │
          │◀─────────────────────────────┘    │
          │                                  │
          │── "이 일정 생성할까요?" (카드) ────▶│
          │                                  │
          │◀──────── 승인/거절 ───────────────│
          │                                  │
          │─ Command(resume=...) ─▶ 도구 실행 │
          │                                  │
          │── "일정을 생성했습니다." ─────────▶│
```

```python
interrupt_on = {
    "create_calendar_event": True,
    "update_calendar_event": True,
    "delete_calendar_event": True,
    "send_email":            True,
}
```

- **TUI**: y/n 입력
- **Discord**: `discord.ui.Button` (60초 타임아웃)
- **WebChat**: 승인/거절 카드 UI

### 2. 3계층 메모리 시스템 (CompositeBackend)

```
    ┌─────────────────────────────────────────┐
    │           CompositeBackend              │
    ├─────────────────────────────────────────┤
    │                                         │
    │  경로 없음 (기본) ──▶ StateBackend       │
    │                      (메모리, per-thread)│
    │                      임시 스크래치패드    │
    │                                         │
    │  /memories/      ──▶ StoreBackend        │
    │                      (PostgreSQL)        │
    │                      영속 메모리·페르소나  │
    │                                         │
    │  /skills/        ──▶ FilesystemBackend   │
    │                      (로컬 디스크, RO)   │
    │                      도메인 규칙 파일     │
    │                                         │
    └─────────────────────────────────────────┘
```

| 파일 | 용도 |
|------|------|
| `/memories/persona.txt` | 에이전트 이름, 말투, 성격 |
| `/memories/user_profile.txt` | 사용자 정보 (자동 + 수동 저장) |

- 매 대화 시작 시 페르소나 + 프로필 자동 로드
- 파일이 없으면 부트스트랩 Q&A (이름 → 말투 → 성격)
- 사용자 정보(직업, 프로젝트, 선호 등) 감지 시 자동 저장

### 3. Skills 시스템 (도메인 규칙)

`/skills/*/SKILL.md` 파일로 에이전트의 행동 규칙을 외부화.

| 스킬 | 역할 |
|------|------|
| `calendar-rules` | 시간 형식, 기본값(1시간), 타임존 처리 |
| `email-rules` | 전송 전 요약·확인 형식 |
| `writing-rules` | 요약·초안·문체 변환 |
| `heartbeat` | 주기적 브리핑 시 확인 항목 |

### 4. Heartbeat (주기적 브리핑)

```
  09:00 ─────────────────────────────────── 22:00
   │           활성 시간 (KST)                │
   │                                         │
   ├─ 30분마다 ──▶ agent.invoke("상황 체크")  │
   │                    │                     │
   │                    ├─ 일정 확인           │
   │                    ├─ 메일 확인           │
   │                    └─ GitHub PR/이슈 확인 │
   │                                         │
   │  알릴 내용 있음 ──▶ Discord/WebChat 알림 │
   │  알릴 내용 없음 ──▶ HEARTBEAT_OK 무시    │
```

- Discord: `discord.ext.tasks` 스케줄러
- WebChat: `asyncio` 기반, 연결된 클라이언트가 있을 때만 브로드캐스트

---

## 실제 동작 예시

### 예시 1. 일정 생성 (Interrupt 동반)

**사용자:** "내일 오후 2시에 팀미팅 잡아줘"

```
① agent.invoke()
     │
② get_current_datetime() ─────────▶ "2026-04-07 12:00 KST"
     │
③ create_calendar_event(
       title="팀미팅",
       start="2026-04-08T14:00:00+09:00",
       end="2026-04-08T15:00:00+09:00"
   )
     │
④ interrupt_on 설정 → 보류
     │
⑤ 채널별 승인 UI 표시
     │  (TUI: y/n / Discord: Button / WebChat: 카드)
     │
⑥ 사용자 승인 ──▶ Command(resume={"decisions":[{"type":"approve"}]})
     │
⑦ Google Calendar API 호출 → 실제 생성
     │
⑧ "내일 오후 2시에 팀미팅 일정을 생성했습니다." 응답
```

### 예시 2. 복합 작업 (서브에이전트 협업)

**사용자:** "최근 리액트 트렌드 조사해서 노션에 정리해줘"

```
① Main Agent: 작업 분석
        │
        ├─ task("research-agent", "최근 리액트 트렌드 조사")
        │       │
        │       └─ research-agent:
        │             TavilySearch(query="React trends 2026")
        │             ──▶ 검색 결과 5개 반환
        │
        ├─ Main Agent: 결과 정리·요약
        │
        └─ task("notion-agent", "노션에 정리된 페이지 생성")
                │
                └─ notion-agent:
                      search_notion ──▶ 부모 페이지 탐색
                      create_notion_page ──▶ 새 페이지 생성
                      ──▶ page_url 반환

② Main Agent: "리액트 트렌드를 조사하여 노션에 정리했습니다. 🔗"
```

---

## 프로젝트 구조

```
jin_openclaw/
├── app/
│   ├── main.py              ← 엔트리포인트 (3채널 기동)
│   ├── config.py            ← 환경변수 로딩
│   ├── agent.py             ← 에이전트 빌드 (프롬프트·도구·서브에이전트)
│   ├── tui.py               ← TUI 채팅 루프
│   ├── discord_bot.py       ← Discord 봇 (interrupt + heartbeat)
│   ├── services/
│   │   └── google_auth.py   ← Google OAuth (Calendar + Gmail 통합)
│   ├── tools/
│   │   ├── calendar.py      ← Google Calendar CRUD
│   │   ├── gmail.py         ← Gmail 전송/조회/읽기
│   │   ├── notion.py        ← Notion 페이지/DB
│   │   ├── github.py        ← GitHub PR/이슈
│   │   ├── search.py        ← Tavily 웹 검색
│   │   └── weather.py       ← wttr.in
│   └── web/
│       ├── server.py        ← FastAPI + WebSocket + Heartbeat
│       └── templates/chat.html ← 채팅 UI (사이드바·마크다운·브리핑)
├── skills/                  ← 도메인 규칙 SKILL.md
│   ├── calendar-rules/
│   ├── email-rules/
│   ├── writing-rules/
│   └── heartbeat/
├── credentials/             ← OAuth 파일 (gitignore)
├── requirements.txt
└── .env                     ← API 키, 토큰 (gitignore)
```

---

## 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. PostgreSQL 준비
createdb jin_db

# 3. 환경변수 설정
cp .env.example .env  # → API 키, 토큰 입력

# 4. 실행
python -m app.main
```

실행 화면:

```
=== Jin비서 ===

  WebChat: http://127.0.0.1:8080

채팅을 시작합니다. (종료: exit)

you > _
```

→ TUI가 뜨는 동시에 **Discord 봇 연결**, **WebChat 서버 기동**이 백그라운드에서 자동 수행.

---

## 핵심 환경변수 요약

| 카테고리 | 변수 |
|---------|------|
| **LLM** | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| **Google** | `GOOGLE_CLIENT_SECRET_PATH`, `GOOGLE_TOKEN_PATH` |
| **Notion** | `NOTION_TOKEN` |
| **GitHub** | `GITHUB_TOKEN`, `GITHUB_USERNAME`, `GITHUB_DEFAULT_REPO` |
| **검색** | `TAVILY_API_KEY` |
| **Discord** | `DISCORD_BOT_TOKEN` |
| **Heartbeat** | `HEARTBEAT_ENABLED`, `HEARTBEAT_INTERVAL`, `HEARTBEAT_CHANNEL_ID`, `HEARTBEAT_ACTIVE_START/END` |
| **WebChat** | `WEARCHAT_ENABLED`, `WEBCHAT_HOST`, `WEBCHAT_PORT` |
| **DB** | `DATABASE_URL` |

---

## 발표용 핵심 메시지 3가지

1. **"하나의 에이전트, 세 개의 얼굴"**
   TUI · Discord · WebChat이 같은 에이전트 인스턴스와 장기 메모리를 공유합니다.

2. **"멀티에이전트 오케스트레이션"**
   Coordinator + 도메인 Subagent로 컨텍스트를 격리하여, 복잡한 복합 작업을 안전하게 분할 수행합니다.

3. **"Human-in-the-Loop이 기본"**
   일정·이메일 같은 위험 작업은 반드시 사용자가 승인한 뒤에만 실행됩니다. LLM을 믿되, 검증합니다.
