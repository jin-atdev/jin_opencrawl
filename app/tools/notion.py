from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from app.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_notion_client():
    """Notion Client를 반환한다. 토큰이 없으면 None."""
    config = Config()
    if not config.notion_token:
        return None
    from notion_client import Client

    return Client(auth=config.notion_token)


def _extract_title(page: dict) -> str:
    """페이지 객체에서 제목 문자열을 추출한다."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in title_parts)
    return "(제목 없음)"


def _flatten_properties(props: dict) -> dict:
    """중첩된 Notion 속성을 읽기 쉬운 dict로 변환한다."""
    result = {}
    for key, prop in props.items():
        prop_type = prop.get("type", "")
        if prop_type == "title":
            result[key] = "".join(t.get("plain_text", "") for t in prop.get("title", []))
        elif prop_type == "rich_text":
            result[key] = "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
        elif prop_type == "number":
            result[key] = prop.get("number")
        elif prop_type == "select":
            sel = prop.get("select")
            result[key] = sel.get("name", "") if sel else ""
        elif prop_type == "multi_select":
            result[key] = [s.get("name", "") for s in prop.get("multi_select", [])]
        elif prop_type == "date":
            d = prop.get("date")
            if d:
                result[key] = d.get("start", "")
                if d.get("end"):
                    result[key] += f" ~ {d['end']}"
            else:
                result[key] = ""
        elif prop_type == "checkbox":
            result[key] = prop.get("checkbox", False)
        elif prop_type == "url":
            result[key] = prop.get("url", "")
        elif prop_type == "email":
            result[key] = prop.get("email", "")
        elif prop_type == "phone_number":
            result[key] = prop.get("phone_number", "")
        elif prop_type == "status":
            st = prop.get("status")
            result[key] = st.get("name", "") if st else ""
        elif prop_type == "people":
            result[key] = [p.get("name", p.get("id", "")) for p in prop.get("people", [])]
        elif prop_type == "relation":
            result[key] = [r.get("id", "") for r in prop.get("relation", [])]
        elif prop_type == "formula":
            f = prop.get("formula", {})
            result[key] = f.get(f.get("type", ""), "")
        elif prop_type == "rollup":
            r = prop.get("rollup", {})
            result[key] = r.get(r.get("type", ""), "")
        else:
            result[key] = str(prop.get(prop_type, ""))
    return result


def _extract_block_text(block: dict) -> str:
    """블록에서 텍스트를 추출한다."""
    btype = block.get("type", "")
    content = block.get(btype, {})
    rich_text = content.get("rich_text", [])
    text = "".join(t.get("plain_text", "") for t in rich_text)

    if btype in ("heading_1", "heading_2", "heading_3"):
        level = btype[-1]
        return f"{'#' * int(level)} {text}"
    if btype == "bulleted_list_item":
        return f"- {text}"
    if btype == "numbered_list_item":
        return f"1. {text}"
    if btype == "to_do":
        checked = "x" if content.get("checked") else " "
        return f"[{checked}] {text}"
    if btype == "code":
        lang = content.get("language", "")
        return f"```{lang}\n{text}\n```"
    if btype == "divider":
        return "---"
    return text


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def search_notion(query: str, filter_type: str = "") -> list[dict]:
    """Notion 워크스페이스에서 페이지나 데이터베이스를 검색합니다.

    Args:
        query: 검색어
        filter_type: 필터 타입 ("page", "database", 또는 빈 문자열로 전체 검색)

    Returns:
        검색 결과 목록 (id, title, type, url)
    """
    logger.info("[TOOL] search_notion 호출: query=%s, filter_type=%s", query, filter_type)

    client = _get_notion_client()
    if client is None:
        return [{"error": "Notion이 연결되지 않았습니다. NOTION_TOKEN을 설정해주세요."}]

    try:
        params: dict = {"query": query, "page_size": 10}
        if filter_type in ("page", "database"):
            params["filter"] = {"value": filter_type, "property": "object"}

        response = client.search(**params)
        results = []
        for item in response.get("results", []):
            results.append({
                "id": item["id"],
                "type": item["object"],
                "title": _extract_title(item),
                "url": item.get("url", ""),
            })
        logger.info("[TOOL] search_notion: %d개 결과", len(results))
        return results
    except Exception as exc:
        logger.error("[TOOL] search_notion 실패: %s", exc, exc_info=True)
        return [{"error": f"Notion 검색 실패: {exc}"}]


@tool
def query_notion_database(
    database_id: str,
    filter_json: str = "",
    sort_json: str = "",
    max_results: int = 20,
) -> list[dict]:
    """Notion 데이터베이스의 항목을 조회합니다.

    Args:
        database_id: 데이터베이스 ID
        filter_json: Notion API 필터 (JSON 문자열, 선택)
        sort_json: Notion API 정렬 (JSON 문자열, 선택)
        max_results: 최대 결과 수

    Returns:
        항목 목록 (id, title, properties, url)
    """
    logger.info("[TOOL] query_notion_database 호출: db=%s, max=%d", database_id, max_results)

    client = _get_notion_client()
    if client is None:
        return [{"error": "Notion이 연결되지 않았습니다. NOTION_TOKEN을 설정해주세요."}]

    try:
        params: dict = {"database_id": database_id, "page_size": min(max_results, 100)}
        if filter_json:
            params["filter"] = json.loads(filter_json)
        if sort_json:
            params["sorts"] = json.loads(sort_json)

        response = client.databases.query(**params)
        results = []
        for page in response.get("results", []):
            results.append({
                "id": page["id"],
                "title": _extract_title(page),
                "properties": _flatten_properties(page.get("properties", {})),
                "url": page.get("url", ""),
            })
        logger.info("[TOOL] query_notion_database: %d개 항목", len(results))
        return results
    except json.JSONDecodeError as exc:
        logger.error("[TOOL] query_notion_database: JSON 파싱 실패: %s", exc)
        return [{"error": f"filter_json 또는 sort_json 파싱 실패: {exc}"}]
    except Exception as exc:
        logger.error("[TOOL] query_notion_database 실패: %s", exc, exc_info=True)
        return [{"error": f"데이터베이스 조회 실패: {exc}"}]


@tool
def create_notion_page(
    database_id: str,
    title: str,
    properties_json: str = "",
) -> dict:
    """Notion 데이터베이스에 새 페이지를 생성합니다.

    Args:
        database_id: 대상 데이터베이스 ID
        title: 페이지 제목
        properties_json: 추가 속성 (JSON 문자열, 선택). 예: {"상태": {"select": {"name": "진행중"}}}

    Returns:
        생성된 페이지 정보 (id, url)
    """
    logger.info("[TOOL] create_notion_page 호출: db=%s, title=%s", database_id, title)

    client = _get_notion_client()
    if client is None:
        return {"error": "Notion이 연결되지 않았습니다. NOTION_TOKEN을 설정해주세요."}

    try:
        properties: dict = {}
        if properties_json:
            properties = json.loads(properties_json)

        # 제목 속성 추가 — Notion DB는 보통 "이름" 또는 "Name"이 title 속성
        # title 키가 properties에 없으면 기본적으로 추가
        has_title = any(
            isinstance(v, dict) and v.get("title") is not None
            for v in properties.values()
        )
        if not has_title:
            properties["이름"] = {
                "title": [{"text": {"content": title}}]
            }

        page = client.pages.create(
            parent={"database_id": database_id},
            properties=properties,
        )
        logger.info("[TOOL] create_notion_page: 생성 성공 (id=%s)", page["id"])
        return {
            "status": "success",
            "id": page["id"],
            "url": page.get("url", ""),
        }
    except json.JSONDecodeError as exc:
        logger.error("[TOOL] create_notion_page: JSON 파싱 실패: %s", exc)
        return {"error": f"properties_json 파싱 실패: {exc}"}
    except Exception as exc:
        logger.error("[TOOL] create_notion_page 실패: %s", exc, exc_info=True)
        return {"error": f"페이지 생성 실패: {exc}"}


@tool
def read_notion_page(page_id: str) -> dict:
    """Notion 페이지의 속성과 본문을 읽습니다.

    Args:
        page_id: 페이지 ID

    Returns:
        페이지 정보 (id, title, properties, content, url)
    """
    logger.info("[TOOL] read_notion_page 호출: page_id=%s", page_id)

    client = _get_notion_client()
    if client is None:
        return {"error": "Notion이 연결되지 않았습니다. NOTION_TOKEN을 설정해주세요."}

    try:
        page = client.pages.retrieve(page_id=page_id)
        title = _extract_title(page)
        properties = _flatten_properties(page.get("properties", {}))

        # 본문 블록 읽기
        blocks_response = client.blocks.children.list(block_id=page_id, page_size=100)
        blocks = blocks_response.get("results", [])
        content_lines = [_extract_block_text(b) for b in blocks if _extract_block_text(b)]

        result = {
            "id": page["id"],
            "title": title,
            "properties": properties,
            "content": "\n".join(content_lines),
            "url": page.get("url", ""),
        }
        logger.info("[TOOL] read_notion_page: 읽기 성공 (title=%s, blocks=%d)", title, len(blocks))
        return result
    except Exception as exc:
        logger.error("[TOOL] read_notion_page 실패: %s", exc, exc_info=True)
        return {"error": f"페이지 읽기 실패: {exc}"}


@tool
def update_notion_page(page_id: str, properties_json: str) -> dict:
    """Notion 페이지의 속성을 수정합니다.

    Args:
        page_id: 수정할 페이지 ID
        properties_json: 수정할 속성 (JSON 문자열). 예: {"상태": {"select": {"name": "완료"}}}

    Returns:
        수정 결과 (id, url)
    """
    logger.info("[TOOL] update_notion_page 호출: page_id=%s", page_id)

    client = _get_notion_client()
    if client is None:
        return {"error": "Notion이 연결되지 않았습니다. NOTION_TOKEN을 설정해주세요."}

    try:
        properties = json.loads(properties_json)
        page = client.pages.update(page_id=page_id, properties=properties)
        logger.info("[TOOL] update_notion_page: 수정 성공 (id=%s)", page["id"])
        return {
            "status": "success",
            "id": page["id"],
            "url": page.get("url", ""),
        }
    except json.JSONDecodeError as exc:
        logger.error("[TOOL] update_notion_page: JSON 파싱 실패: %s", exc)
        return {"error": f"properties_json 파싱 실패: {exc}"}
    except Exception as exc:
        logger.error("[TOOL] update_notion_page 실패: %s", exc, exc_info=True)
        return {"error": f"페이지 수정 실패: {exc}"}


@tool
def append_notion_blocks(
    page_id: str,
    content: str,
    block_type: str = "paragraph",
) -> dict:
    """Notion 페이지에 텍스트 블록을 추가합니다.

    Args:
        page_id: 대상 페이지 ID
        content: 추가할 텍스트 내용
        block_type: 블록 타입 (paragraph, heading_1, heading_2, heading_3, bulleted_list_item, numbered_list_item, to_do, code)

    Returns:
        추가 결과
    """
    logger.info("[TOOL] append_notion_blocks 호출: page_id=%s, type=%s", page_id, block_type)

    client = _get_notion_client()
    if client is None:
        return {"error": "Notion이 연결되지 않았습니다. NOTION_TOKEN을 설정해주세요."}

    valid_types = {
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item", "to_do", "code",
    }
    if block_type not in valid_types:
        return {"error": f"지원하지 않는 블록 타입: {block_type}. 가능: {', '.join(sorted(valid_types))}"}

    try:
        block: dict = {
            "object": "block",
            "type": block_type,
            block_type: {
                "rich_text": [{"type": "text", "text": {"content": content}}],
            },
        }
        if block_type == "to_do":
            block[block_type]["checked"] = False
        if block_type == "code":
            block[block_type]["language"] = "plain text"

        response = client.blocks.children.append(
            block_id=page_id,
            children=[block],
        )
        logger.info("[TOOL] append_notion_blocks: 추가 성공")
        return {
            "status": "success",
            "block_count": len(response.get("results", [])),
        }
    except Exception as exc:
        logger.error("[TOOL] append_notion_blocks 실패: %s", exc, exc_info=True)
        return {"error": f"블록 추가 실패: {exc}"}


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def get_notion_tools() -> list:
    """NOTION_TOKEN이 없으면 빈 리스트를 반환한다."""
    config = Config()
    if not config.notion_token:
        logger.warning("[TOOL] Notion 토큰 없음 → Notion 도구 비활성화")
        return []

    tools = [
        search_notion,
        query_notion_database,
        create_notion_page,
        read_notion_page,
        update_notion_page,
        append_notion_blocks,
    ]
    logger.info("[TOOL] get_notion_tools: %d개 도구 반환 (%s)", len(tools), [t.name for t in tools])
    return tools
