from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from langgraph.types import Command

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI()
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_agent = None


def set_agent(agent) -> None:
    global _agent
    _agent = agent


def _extract_response(result) -> str:
    """에이전트 결과에서 최종 AI 메시지를 추출한다."""
    msgs = _get_messages(result)
    if not msgs:
        return ""

    for m in reversed(msgs):
        if hasattr(m, "content") and m.content and getattr(m, "type", "") == "ai":
            if not getattr(m, "tool_calls", None) or m.content.strip():
                return m.content
    return ""


def _extract_tool_calls(result) -> list[dict]:
    """에이전트 결과에서 실행된 도구 호출 목록을 추출한다."""
    msgs = _get_messages(result)
    if not msgs:
        return []

    calls = []
    for m in msgs:
        if getattr(m, "type", "") == "ai":
            for tc in getattr(m, "tool_calls", []) or []:
                calls.append({
                    "name": tc.get("name", "unknown"),
                    "args": tc.get("args", {}),
                })
    return calls


def _get_messages(result) -> list:
    if isinstance(result, dict):
        return result.get("messages", [])
    if hasattr(result, "__getitem__"):
        try:
            return result["messages"]
        except (KeyError, TypeError):
            pass
    return []


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, session_id: str | None = None):
    await websocket.accept()
    if not session_id:
        session_id = uuid4().hex[:8]
    thread_id = f"webchat-{session_id}"
    config = {"configurable": {"thread_id": thread_id}}

    logger.info("[WebChat] 연결됨 (session=%s, thread=%s)", session_id, thread_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "message":
                content = data.get("content", "").strip()
                if not content:
                    continue

                logger.info("[WebChat] 메시지 수신 (session=%s): %s", session_id, content[:200])

                # 생각 시작 알림
                start_time = time.time()
                await websocket.send_json({"type": "thinking_start"})

                try:
                    result = await asyncio.to_thread(
                        _agent.invoke,
                        {"messages": [{"role": "user", "content": content}]},
                        config,
                        version="v2",
                    )
                except Exception as exc:
                    logger.error("[WebChat] agent.invoke 예외: %s", exc, exc_info=True)
                    await websocket.send_json({"type": "thinking_end"})
                    await websocket.send_json({"type": "error", "content": f"오류가 발생했습니다: {exc}"})
                    continue

                elapsed = round(time.time() - start_time, 1)

                # Interrupt 처리 루프
                result = await _handle_interrupts(websocket, result, config, session_id)

                # 도구 호출 정보 전송
                tool_calls = _extract_tool_calls(result)
                if tool_calls:
                    await websocket.send_json({"type": "tool_calls", "calls": tool_calls, "elapsed": elapsed})

                # 생각 종료
                await websocket.send_json({"type": "thinking_end"})

                # 최종 응답
                response = _extract_response(result)
                if response:
                    await websocket.send_json({"type": "response", "content": response})
                else:
                    await websocket.send_json({"type": "response", "content": "(응답을 생성하지 못했습니다)"})

    except WebSocketDisconnect:
        logger.info("[WebChat] 연결 종료 (session=%s)", session_id)


async def _handle_interrupts(websocket: WebSocket, result, config: dict, session_id: str):
    """interrupt 발생 시 WebSocket으로 승인/거절을 받는다."""
    while hasattr(result, "interrupts") and result.interrupts:
        interrupt_value = result.interrupts[0].value
        action_requests = interrupt_value.get("action_requests", [])

        if not action_requests:
            break

        # interrupt 정보를 클라이언트에 전송
        actions = []
        for req in action_requests:
            actions.append({
                "name": req.get("name", "unknown"),
                "args": req.get("args", {}),
            })

        await websocket.send_json({"type": "thinking_end"})
        await websocket.send_json({"type": "interrupt", "actions": actions})
        logger.info("[WebChat] interrupt 전송 (session=%s, %d개 액션)", session_id, len(actions))

        # 클라이언트의 승인/거절 대기
        try:
            response_data = await asyncio.wait_for(websocket.receive_json(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.info("[WebChat] interrupt 타임아웃 → 거절 (session=%s)", session_id)
            response_data = {"type": "interrupt_response", "approved": False}

        approved = response_data.get("approved", False)
        if approved:
            decisions = [{"type": "approve"} for _ in action_requests]
            logger.info("[WebChat] interrupt 승인 (session=%s)", session_id)
        else:
            decisions = [{"type": "reject"} for _ in action_requests]
            logger.info("[WebChat] interrupt 거절 (session=%s)", session_id)

        await websocket.send_json({"type": "thinking_start"})

        try:
            result = await asyncio.to_thread(
                _agent.invoke,
                Command(resume={"decisions": decisions}),
                config,
                version="v2",
            )
        except Exception as exc:
            logger.error("[WebChat] Command(resume) 예외: %s", exc, exc_info=True)
            await websocket.send_json({"type": "thinking_end"})
            await websocket.send_json({"type": "error", "content": f"오류가 발생했습니다: {exc}"})
            break

    return result
