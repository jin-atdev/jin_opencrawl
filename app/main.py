from __future__ import annotations

import logging
import sys

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

    if not config.discord_bot_token:
        print("오류: DISCORD_BOT_TOKEN이 설정되지 않았습니다.")
        print(".env 파일에 Discord 봇 토큰을 설정해주세요.")
        sys.exit(1)

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.postgres import PostgresStore

    from app.agent import build_agent
    from app.discord_bot import run_bot

    checkpointer = MemorySaver()

    with PostgresStore.from_conn_string(config.database_url) as store:
        store.setup()
        agent = build_agent(store=store, checkpointer=checkpointer)
        run_bot(agent, config, config.discord_bot_token)


if __name__ == "__main__":
    main()
