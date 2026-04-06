from __future__ import annotations

import logging
import sys
import threading

from app.config import Config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.FileHandler("app.log", encoding="utf-8")],
    )

    config = Config()

    if not config.openai_api_key or config.openai_api_key == "your-api-key-here":
        print("오류: OPENAI_API_KEY가 설정되지 않았습니다.")
        print(".env 파일에 유효한 API 키를 설정해주세요.")
        sys.exit(1)

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.postgres import PostgresStore

    from app.agent import build_agent
    from app.tui import run_tui

    checkpointer = MemorySaver()

    with PostgresStore.from_conn_string(config.database_url) as store:
        store.setup()
        agent = build_agent(store=store, checkpointer=checkpointer)

        # WebChat 서버 (데몬 스레드)
        if config.webchat_enabled:
            _start_webchat_server(agent, config)

        run_tui(agent, config)


def _start_webchat_server(agent, config: Config) -> None:
    """FastAPI WebChat 서버를 데몬 스레드로 실행한다."""
    import uvicorn
    from app.web.server import app, set_agent, set_config

    set_agent(agent)
    set_config(config)
    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": config.webchat_host, "port": config.webchat_port, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
    logging.getLogger(__name__).info(
        "[WebChat] 서버 시작: http://%s:%d", config.webchat_host, config.webchat_port
    )


if __name__ == "__main__":
    main()
