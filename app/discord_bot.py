from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta, timezone

import discord
from discord.ext import tasks
from langgraph.types import Command

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

_agent = None
_config_tpl: dict | None = None
_config = None  # Config instance for briefing settings
_interrupt_users: set[int] = set()  # interrupt 응답 대기 중인 user ID

KST = timezone(timedelta(hours=9))


@client.event
async def on_ready():
    logger.info("Discord 봇 로그인: %s (id=%s)", client.user, client.user.id)
    print(f"봇 로그인 완료: {client.user}")

    # 일일 브리핑 스케줄러 시작
    if _config and _config.briefing_enabled and _config.briefing_channel_id != 0:
        if not daily_briefing_task.is_running():
            daily_briefing_task.start()
            logger.info("[Briefing] 일일 브리핑 스케줄러 시작됨")

    # Heartbeat 스케줄러 시작
    if _config and _config.heartbeat_enabled and _config.heartbeat_channel_id != 0:
        if not heartbeat_task.is_running():
            heartbeat_task.start()
            logger.info("[Heartbeat] 스케줄러 시작됨 (간격: %d분)", _config.heartbeat_interval)


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    if message.author.id in _interrupt_users:
        return

    content = message.content.strip()
    if not content:
        return

    thread_id = f"discord-{message.author.id}"
    config = {"configurable": {"thread_id": thread_id}}

    logger.info("[Discord] 메시지 수신 (user=%s, thread=%s): %s", message.author, thread_id, content[:200])

    async with message.channel.typing():
        try:
            result = await asyncio.to_thread(
                _agent.invoke,
                {"messages": [{"role": "user", "content": content}]},
                config,
                version="v2",
            )
        except Exception as exc:
            logger.error("[Discord] agent.invoke 예외: %s", exc, exc_info=True)
            await message.channel.send(f"오류가 발생했습니다: {exc}")
            return

    # 디버그: 에이전트 메시지 로깅
    _log_agent_messages(result)

    # Interrupt 처리
    result = await _handle_interrupts(result, config, message)

    # 최종 응답 전송
    final = _extract_response(result)
    if final:
        await _send_long_message(message.channel, final)
    else:
        await message.channel.send("응답을 생성하지 못했습니다.")


async def _handle_interrupts(result, config: dict, message: discord.Message):
    """interrupt 발생 시 Discord 메시지로 승인/거절을 받는다."""
    while hasattr(result, "interrupts") and result.interrupts:
        interrupt_value = result.interrupts[0].value
        action_requests = interrupt_value.get("action_requests", [])

        if not action_requests:
            break

        # 도구 정보를 임베드로 표시
        embed = discord.Embed(
            title="확인 필요",
            color=discord.Color.yellow(),
        )
        for i, req in enumerate(action_requests):
            tool_name = req.get("name", "unknown")
            tool_args = req.get("args", {})
            args_text = "\n".join(f"  {k}: {v}" for k, v in tool_args.items()) or "(없음)"
            embed.add_field(
                name=f"{i + 1}. {tool_name}",
                value=f"```\n{args_text}\n```",
                inline=False,
            )
        embed.set_footer(text="승인: y  |  거절: 그 외 입력  (60초 내 응답)")

        await message.channel.send(embed=embed)

        # 같은 유저의 응답 대기 (on_message에서 무시하도록 등록)
        _interrupt_users.add(message.author.id)
        def check(m: discord.Message) -> bool:
            return m.author == message.author and m.channel == message.channel

        try:
            reply = await client.wait_for("message", check=check, timeout=60.0)
            answer = reply.content.strip().lower()
        except asyncio.TimeoutError:
            await message.channel.send("시간 초과로 거절 처리되었습니다.")
            answer = "n"
        finally:
            _interrupt_users.discard(message.author.id)

        if answer in ("y", "yes", "네", "ㅇ", "승인"):
            decisions = [{"type": "approve"} for _ in action_requests]
            logger.info("[Discord] interrupt 승인 (%d개)", len(action_requests))
        else:
            decisions = [{"type": "reject"} for _ in action_requests]
            logger.info("[Discord] interrupt 거절 (%d개)", len(action_requests))

        async with message.channel.typing():
            try:
                result = await asyncio.to_thread(
                    _agent.invoke,
                    Command(resume={"decisions": decisions}),
                    config,
                    version="v2",
                )
            except Exception as exc:
                logger.error("[Discord] Command(resume) 예외: %s", exc, exc_info=True)
                await message.channel.send(f"오류가 발생했습니다: {exc}")
                break

    return result


def _log_agent_messages(result):
    """에이전트의 모든 메시지와 도구 호출을 로깅한다."""
    msgs = None
    if isinstance(result, dict):
        msgs = result.get("messages", [])
    elif hasattr(result, "__getitem__"):
        try:
            msgs = result["messages"]
        except (KeyError, TypeError):
            return

    if not msgs:
        return

    for m in msgs:
        msg_type = getattr(m, "type", "unknown")
        if msg_type == "ai":
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    tool_name = tc.get("name", "?")
                    tool_args = tc.get("args", {})
                    logger.info("[DEBUG] AI 도구 호출: %s(%s)", tool_name, tool_args)
                    # 메모리 저장 감지
                    if tool_name in ("write_file", "edit_file"):
                        path = tool_args.get("path", "")
                        if "/memories/" in path:
                            logger.info("[MEMORY] 저장 감지: %s → %s", tool_name, path)
            if m.content:
                logger.info("[DEBUG] AI 응답: %s", m.content[:300])
        elif msg_type == "tool":
            tool_name = getattr(m, "name", "?")
            content_str = str(m.content)[:300]
            logger.info("[DEBUG] 도구 결과 [%s]: %s", tool_name, content_str)
            # 메모리 저장 결과 감지
            if tool_name in ("write_file", "edit_file"):
                logger.info("[MEMORY] 저장 결과: %s", content_str)


def _extract_response(result) -> str:
    """에이전트 결과에서 최종 AI 메시지를 추출한다."""
    msgs = None
    if isinstance(result, dict):
        msgs = result.get("messages", [])
    elif hasattr(result, "__getitem__"):
        try:
            msgs = result["messages"]
        except (KeyError, TypeError):
            return ""

    if not msgs:
        return ""

    for m in reversed(msgs):
        if hasattr(m, "content") and m.content and getattr(m, "type", "") == "ai":
            if not getattr(m, "tool_calls", None) or m.content.strip():
                return m.content
    return ""


async def _send_long_message(channel: discord.abc.Messageable, text: str):
    """2000자 초과 시 분할 전송한다."""
    while text:
        if len(text) <= 2000:
            await channel.send(text)
            break
        # 줄바꿈 기준으로 적절히 자르기
        split_at = text.rfind("\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        await channel.send(text[:split_at])
        text = text[split_at:].lstrip("\n")


def _format_daily_briefing() -> str:
    """오늘 일정 + 읽지 않은 메일을 조회하여 브리핑 마크다운을 생성한다."""
    from app.services.google_auth import get_calendar_service, get_gmail_service

    now = datetime.now(KST)
    today_str = now.strftime("%Y년 %m월 %d일")
    lines = [f"📅 **Daily Briefing - {today_str}**", ""]

    # ── 오늘 일정 ──
    try:
        cal_service = get_calendar_service()
        if cal_service is None:
            lines.append("## 오늘의 일정")
            lines.append("⚠️ Google Calendar가 연결되지 않았습니다.")
        else:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            day_end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
            result = (
                cal_service.events()
                .list(
                    calendarId="primary",
                    timeMin=day_start,
                    timeMax=day_end,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = result.get("items", [])
            lines.append(f"## 오늘의 일정 ({len(events)}건)")
            if not events:
                lines.append("오늘 예정된 일정이 없습니다.")
            else:
                for e in events:
                    start_raw = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))
                    end_raw = e.get("end", {}).get("dateTime", e.get("end", {}).get("date", ""))
                    summary = e.get("summary", "(제목 없음)")
                    location = e.get("location", "")

                    # 시간 포맷
                    try:
                        s = datetime.fromisoformat(start_raw).strftime("%H:%M")
                        en = datetime.fromisoformat(end_raw).strftime("%H:%M")
                        time_str = f"{s}-{en}"
                    except (ValueError, TypeError):
                        time_str = "종일"

                    entry = f"- {time_str} | {summary}"
                    if location:
                        entry += f" | {location}"
                    lines.append(entry)
    except Exception as exc:
        logger.error("[Briefing] 캘린더 조회 실패: %s", exc, exc_info=True)
        lines.append("## 오늘의 일정")
        lines.append(f"⚠️ 일정 조회 실패: {exc}")

    lines.append("")

    # ── 읽지 않은 이메일 ──
    try:
        gmail_service = get_gmail_service()
        if gmail_service is None:
            lines.append("## 읽지 않은 이메일")
            lines.append("⚠️ Gmail이 연결되지 않았습니다.")
        else:
            result = (
                gmail_service.users()
                .messages()
                .list(userId="me", q="is:unread", maxResults=10)
                .execute()
            )
            messages = result.get("messages", [])
            lines.append(f"## 읽지 않은 이메일 ({len(messages)}건)")
            if not messages:
                lines.append("읽지 않은 이메일이 없습니다.")
            else:
                for i, msg_info in enumerate(messages, 1):
                    msg = (
                        gmail_service.users()
                        .messages()
                        .get(
                            userId="me",
                            id=msg_info["id"],
                            format="metadata",
                            metadataHeaders=["Subject", "From"],
                        )
                        .execute()
                    )
                    headers = {
                        h["name"]: h["value"]
                        for h in msg.get("payload", {}).get("headers", [])
                    }
                    sender = headers.get("From", "")
                    # "이름 <email>" → "이름" 만 추출
                    if "<" in sender:
                        sender = sender.split("<")[0].strip().strip('"')
                    subject = headers.get("Subject", "(제목 없음)")
                    lines.append(f"{i}. **{sender}** {subject}")
    except Exception as exc:
        logger.error("[Briefing] 이메일 조회 실패: %s", exc, exc_info=True)
        lines.append("## 읽지 않은 이메일")
        lines.append(f"⚠️ 이메일 조회 실패: {exc}")

    lines.append("")
    lines.append("---")
    lines.append("자동 생성된 브리핑입니다.")
    return "\n".join(lines)


HEARTBEAT_PROMPT = (
    "Heartbeat 체크를 실행하라. 관련 스킬을 참고하여 다가오는 일정과 읽지 않은 메일을 확인하고, "
    "알릴 게 있으면 알림 메시지를, 없으면 정확히 'HEARTBEAT_OK'라고만 응답하라. "
    "확인만 하라. 일정 생성/수정/삭제, 메일 전송 등 변경 작업은 절대 하지 마라."
)


@tasks.loop(minutes=30)
async def heartbeat_task():
    """주기적으로 에이전트를 깨워 일정/메일 등을 확인하고 알림한다."""
    if _config is None or not _config.heartbeat_enabled or _config.heartbeat_channel_id == 0:
        return

    # 활성 시간 체크 (KST)
    now = datetime.now(KST)
    try:
        start_h, start_m = map(int, _config.heartbeat_active_start.split(":"))
        end_h, end_m = map(int, _config.heartbeat_active_end.split(":"))
        if not (dt_time(start_h, start_m) <= now.time() <= dt_time(end_h, end_m)):
            logger.info("[Heartbeat] 활성 시간 외 — 건너뜀")
            return
    except (ValueError, TypeError):
        pass  # 파싱 실패 시 시간 제한 없이 실행

    # 격리된 thread_id 사용 (사용자 대화와 분리)
    config = {"configurable": {"thread_id": "heartbeat"}}

    logger.info("[Heartbeat] 체크 시작")
    try:
        result = await asyncio.to_thread(
            _agent.invoke,
            {"messages": [{"role": "user", "content": HEARTBEAT_PROMPT}]},
            config,
            version="v2",
        )
    except Exception as exc:
        logger.error("[Heartbeat] agent.invoke 예외: %s", exc, exc_info=True)
        return

    response = _extract_response(result)
    if not response or "HEARTBEAT_OK" in response:
        logger.info("[Heartbeat] 알릴 내용 없음")
        return

    # 알림 전송
    channel = client.get_channel(_config.heartbeat_channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(_config.heartbeat_channel_id)
        except Exception as exc:
            logger.error("[Heartbeat] 채널 찾기 실패: %s", exc)
            return

    await _send_long_message(channel, response)
    logger.info("[Heartbeat] 알림 전송 완료")


@heartbeat_task.before_loop
async def _before_heartbeat():
    """봇이 준비될 때까지 대기한다."""
    await client.wait_until_ready()


@tasks.loop(hours=24)
async def daily_briefing_task():
    """매일 지정 시간에 일일 브리핑을 전송한다."""
    if _config is None or not _config.briefing_enabled or _config.briefing_channel_id == 0:
        return

    channel = client.get_channel(_config.briefing_channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(_config.briefing_channel_id)
        except Exception as exc:
            logger.error("[Briefing] 채널을 찾을 수 없습니다 (id=%d): %s", _config.briefing_channel_id, exc)
            return

    logger.info("[Briefing] 일일 브리핑 생성 시작")
    try:
        briefing = await asyncio.to_thread(_format_daily_briefing)
        await _send_long_message(channel, briefing)
        logger.info("[Briefing] 일일 브리핑 전송 완료")
    except Exception as exc:
        logger.error("[Briefing] 일일 브리핑 전송 실패: %s", exc, exc_info=True)


@daily_briefing_task.before_loop
async def _before_daily_briefing():
    """봇이 준비될 때까지 대기한다."""
    await client.wait_until_ready()


def run_bot(agent, config, token: str):
    """Discord 봇을 실행한다."""
    global _agent, _config
    _agent = agent
    _config = config

    # 브리핑 스케줄러 시간 설정
    if config.briefing_enabled and config.briefing_channel_id != 0:
        try:
            h, m = map(int, config.briefing_time.split(":"))
            briefing_time = dt_time(hour=h, minute=m, tzinfo=KST)
            daily_briefing_task.change_interval(time=briefing_time)
            logger.info("[Briefing] 스케줄 설정: %s KST", config.briefing_time)
        except (ValueError, TypeError) as exc:
            logger.error("[Briefing] 시간 파싱 실패: %s", exc)

    # Heartbeat 스케줄러 간격 설정
    if config.heartbeat_enabled and config.heartbeat_channel_id != 0:
        heartbeat_task.change_interval(minutes=config.heartbeat_interval)
        logger.info("[Heartbeat] 스케줄 설정: %d분 간격, 활성 시간 %s~%s KST",
                     config.heartbeat_interval, config.heartbeat_active_start, config.heartbeat_active_end)

    logger.info("[Discord] 봇 시작")
    client.run(token, log_handler=None)
