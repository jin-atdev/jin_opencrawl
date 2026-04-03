from __future__ import annotations

import logging
import threading

from langgraph.types import Command

logger = logging.getLogger(__name__)


def run_tui(agent, config) -> None:
    """TUI 메인. Discord 토큰이 있으면 자동 연결 후 채팅 루프를 시작한다."""
    print("\n=== OpenClaw ===\n")

    # Discord 자동 연결
    _auto_start_discord(agent, config)

    # 채팅 루프
    thread_id = "tui-local"
    invoke_config = {"configurable": {"thread_id": thread_id}}

    print("채팅을 시작합니다. (종료: exit)\n")

    while True:
        try:
            user_input = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break

        if not user_input:
            continue
        if user_input in ("exit", "quit", "종료"):
            print("종료합니다.")
            break

        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                invoke_config,
                version="v2",
            )
        except Exception as exc:
            logger.error("[TUI] agent.invoke 예외: %s", exc, exc_info=True)
            print(f"\n오류가 발생했습니다: {exc}\n")
            continue

        # Interrupt 처리
        result = _handle_interrupts(agent, result, invoke_config)

        # 응답 출력
        response = _extract_response(result)
        if response:
            print(f"\nassistant > {response}\n")
        else:
            print("\nassistant > (응답을 생성하지 못했습니다)\n")


def _auto_start_discord(agent, config) -> None:
    """DISCORD_BOT_TOKEN이 .env에 있으면 자동으로 봇을 백그라운드 시작한다."""
    if not config.discord_bot_token:
        return

    from app.discord_bot import run_bot

    t = threading.Thread(target=run_bot, args=(agent, config, config.discord_bot_token), daemon=True)
    t.start()
    logger.info("[TUI] Discord 봇 자동 연결됨")


def _handle_interrupts(agent, result, config: dict):
    """Interrupt 발생 시 터미널에서 승인/거절을 받는다."""
    while hasattr(result, "interrupts") and result.interrupts:
        interrupt_value = result.interrupts[0].value
        action_requests = interrupt_value.get("action_requests", [])

        if not action_requests:
            break

        print("\n--- 확인 필요 ---")
        for i, req in enumerate(action_requests):
            tool_name = req.get("name", "unknown")
            tool_args = req.get("args", {})
            args_text = "\n".join(f"  {k}: {v}" for k, v in tool_args.items()) or "(없음)"
            print(f"{i + 1}. {tool_name}\n{args_text}")
        print("-----------------")

        try:
            answer = input("승인하시겠습니까? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer == "y":
            decisions = [{"type": "approve"} for _ in action_requests]
            logger.info("[TUI] interrupt 승인 (%d개)", len(action_requests))
        else:
            decisions = [{"type": "reject"} for _ in action_requests]
            logger.info("[TUI] interrupt 거절 (%d개)", len(action_requests))

        try:
            result = agent.invoke(
                Command(resume={"decisions": decisions}),
                config,
                version="v2",
            )
        except Exception as exc:
            logger.error("[TUI] Command(resume) 예외: %s", exc, exc_info=True)
            print(f"\n오류가 발생했습니다: {exc}\n")
            break

    return result


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
