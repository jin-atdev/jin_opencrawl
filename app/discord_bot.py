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
_config = None  # Config instance

KST = timezone(timedelta(hours=9))


class InterruptView(discord.ui.View):
    """승인/거절 버튼 UI. 요청한 유저만 클릭 가능."""

    def __init__(self, author_id: int):
        super().__init__(timeout=60.0)
        self.author_id = author_id
        self.result: bool | None = None  # True=승인, False=거절, None=타임아웃

    @discord.ui.button(label="승인", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("본인만 응답할 수 있습니다.", ephemeral=True)
            return
        self.result = True
        self.stop()
        await interaction.response.edit_message(view=None)

    @discord.ui.button(label="거절", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("본인만 응답할 수 있습니다.", ephemeral=True)
            return
        self.result = False
        self.stop()
        await interaction.response.edit_message(view=None)

    async def on_timeout(self):
        self.result = None
        self.stop()


@client.event
async def on_ready():
    logger.info("Discord 봇 로그인: %s (id=%s)", client.user, client.user.id)

    # Heartbeat 스케줄러 시작
    if _config and _config.heartbeat_enabled and _config.heartbeat_channel_id != 0:
        if not heartbeat_task.is_running():
            heartbeat_task.start()
            logger.info("[Heartbeat] 스케줄러 시작됨 (간격: %d분)", _config.heartbeat_interval)


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
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
    """interrupt 발생 시 Discord 버튼으로 승인/거절을 받는다."""
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
        embed.set_footer(text="아래 버튼을 눌러주세요 (60초 내 응답)")

        # 버튼 UI로 승인/거절 대기
        view = InterruptView(message.author.id)
        await message.channel.send(embed=embed, view=view)
        await view.wait()

        if view.result is True:
            decisions = [{"type": "approve"} for _ in action_requests]
            logger.info("[Discord] interrupt 승인 (%d개)", len(action_requests))
        elif view.result is False:
            decisions = [{"type": "reject"} for _ in action_requests]
            logger.info("[Discord] interrupt 거절 (%d개)", len(action_requests))
        else:
            # 타임아웃
            await message.channel.send("시간 초과로 거절 처리되었습니다.")
            decisions = [{"type": "reject"} for _ in action_requests]
            logger.info("[Discord] interrupt 타임아웃 → 거절 (%d개)", len(action_requests))

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


HEARTBEAT_PROMPT = (
    "Heartbeat 체크를 실행하라. 다음 항목들을 확인하라:\n"
    "1. 다가오는 일정 (오늘 남은 일정, 임박한 일정)\n"
    "2. 읽지 않은 메일\n"
    "3. GitHub: 리뷰 요청된 PR, 내가 만든 열린 PR, 할당된 이슈\n"
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


def run_bot(agent, config, token: str):
    """Discord 봇을 실행한다."""
    global _agent, _config
    _agent = agent
    _config = config

    # Heartbeat 스케줄러 간격 설정
    if config.heartbeat_enabled and config.heartbeat_channel_id != 0:
        heartbeat_task.change_interval(minutes=config.heartbeat_interval)
        logger.info("[Heartbeat] 스케줄 설정: %d분 간격, 활성 시간 %s~%s KST",
                     config.heartbeat_interval, config.heartbeat_active_start, config.heartbeat_active_end)

    logger.info("[Discord] 봇 시작")
    client.run(token, log_handler=None)
