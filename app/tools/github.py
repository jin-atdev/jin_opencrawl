from __future__ import annotations

import logging

import requests
from langchain_core.tools import tool

from app.config import Config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_github_session() -> requests.Session | None:
    """인증된 requests.Session을 반환한다. 토큰이 없으면 None."""
    config = Config()
    if not config.github_token:
        return None
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return session


def _resolve_repo(repo: str) -> str:
    """repo가 비어있으면 Config의 기본 리포를 반환한다."""
    if repo:
        return repo
    config = Config()
    return config.github_default_repo


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def list_pull_requests(
    repo: str = "",
    state: str = "open",
    max_results: int = 10,
) -> list[dict]:
    """GitHub 리포지토리의 Pull Request 목록을 조회합니다.

    Args:
        repo: 리포지토리 (owner/name 형식, 예: "octocat/Hello-World"). 비어있으면 기본 리포 사용.
        state: PR 상태 필터 ("open", "closed", "all")
        max_results: 최대 결과 수 (1~100)

    Returns:
        PR 목록 (number, title, state, user, created_at, updated_at, html_url, draft, labels)
    """
    logger.info("[TOOL] list_pull_requests 호출: repo=%s, state=%s, max=%d", repo, state, max_results)

    session = _get_github_session()
    if session is None:
        return [{"error": "GitHub이 연결되지 않았습니다. GITHUB_TOKEN을 설정해주세요."}]

    repo = _resolve_repo(repo)
    if not repo:
        return [{"error": "리포지토리를 지정해주세요. (예: owner/repo)"}]

    try:
        resp = session.get(
            f"{BASE_URL}/repos/{repo}/pulls",
            params={
                "state": state,
                "per_page": min(max_results, 100),
                "sort": "updated",
                "direction": "desc",
            },
        )
        resp.raise_for_status()
        pulls = resp.json()

        results = []
        for pr in pulls:
            results.append({
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "draft": pr.get("draft", False),
                "user": pr["user"]["login"],
                "created_at": pr["created_at"],
                "updated_at": pr["updated_at"],
                "html_url": pr["html_url"],
                "labels": [lb["name"] for lb in pr.get("labels", [])],
            })
        logger.info("[TOOL] list_pull_requests: %d개 PR 반환", len(results))
        return results
    except requests.HTTPError as exc:
        logger.error("[TOOL] list_pull_requests 실패: %s", exc, exc_info=True)
        return [{"error": f"PR 목록 조회 실패: {exc.response.status_code} {exc.response.reason}"}]
    except Exception as exc:
        logger.error("[TOOL] list_pull_requests 실패: %s", exc, exc_info=True)
        return [{"error": f"PR 목록 조회 실패: {exc}"}]


@tool
def get_pull_request(
    repo: str = "",
    pr_number: int = 0,
) -> dict:
    """GitHub Pull Request의 상세 정보를 조회합니다. 리뷰 상태, CI 체크, 변경 파일 수 등을 포함합니다.

    Args:
        repo: 리포지토리 (owner/name 형식). 비어있으면 기본 리포 사용.
        pr_number: PR 번호

    Returns:
        PR 상세 정보 (number, title, body, state, user, reviews, ci_status, check_runs, html_url 등)
    """
    logger.info("[TOOL] get_pull_request 호출: repo=%s, pr_number=%d", repo, pr_number)

    session = _get_github_session()
    if session is None:
        return {"error": "GitHub이 연결되지 않았습니다. GITHUB_TOKEN을 설정해주세요."}

    repo = _resolve_repo(repo)
    if not repo:
        return {"error": "리포지토리를 지정해주세요. (예: owner/repo)"}

    try:
        # PR 상세
        resp = session.get(f"{BASE_URL}/repos/{repo}/pulls/{pr_number}")
        resp.raise_for_status()
        pr = resp.json()

        # 리뷰 목록
        reviews_resp = session.get(f"{BASE_URL}/repos/{repo}/pulls/{pr_number}/reviews")
        reviews = reviews_resp.json() if reviews_resp.ok else []

        review_summary: dict[str, str] = {}
        for r in reviews:
            review_summary[r["user"]["login"]] = r["state"]

        # CI 상태 (commit status)
        head_sha = pr["head"]["sha"]
        ci_resp = session.get(f"{BASE_URL}/repos/{repo}/commits/{head_sha}/status")
        ci_status = ci_resp.json().get("state", "unknown") if ci_resp.ok else "unknown"

        # Check Runs (GitHub Actions)
        checks_resp = session.get(f"{BASE_URL}/repos/{repo}/commits/{head_sha}/check-runs")
        check_runs = []
        if checks_resp.ok:
            for cr in checks_resp.json().get("check_runs", []):
                check_runs.append({
                    "name": cr["name"],
                    "status": cr["status"],
                    "conclusion": cr.get("conclusion"),
                })

        result = {
            "number": pr["number"],
            "title": pr["title"],
            "body": (pr.get("body") or "")[:1000],
            "state": pr["state"],
            "draft": pr.get("draft", False),
            "user": pr["user"]["login"],
            "created_at": pr["created_at"],
            "updated_at": pr["updated_at"],
            "html_url": pr["html_url"],
            "mergeable": pr.get("mergeable"),
            "labels": [lb["name"] for lb in pr.get("labels", [])],
            "changed_files": pr.get("changed_files", 0),
            "additions": pr.get("additions", 0),
            "deletions": pr.get("deletions", 0),
            "reviews": review_summary,
            "ci_status": ci_status,
            "check_runs": check_runs,
            "base": pr["base"]["ref"],
            "head": pr["head"]["ref"],
        }
        logger.info("[TOOL] get_pull_request: 조회 성공 (PR #%d)", pr_number)
        return result
    except requests.HTTPError as exc:
        logger.error("[TOOL] get_pull_request 실패: %s", exc, exc_info=True)
        return {"error": f"PR 조회 실패: {exc.response.status_code} {exc.response.reason}"}
    except Exception as exc:
        logger.error("[TOOL] get_pull_request 실패: %s", exc, exc_info=True)
        return {"error": f"PR 조회 실패: {exc}"}


@tool
def list_issues(
    repo: str = "",
    state: str = "open",
    labels: str = "",
    assignee: str = "",
    max_results: int = 10,
) -> list[dict]:
    """GitHub 리포지토리의 이슈 목록을 조회합니다 (PR 제외).

    Args:
        repo: 리포지토리 (owner/name 형식). 비어있으면 기본 리포 사용.
        state: 이슈 상태 ("open", "closed", "all")
        labels: 라벨 필터 (쉼표로 구분, 예: "bug,enhancement")
        assignee: 담당자 GitHub 유저네임 필터
        max_results: 최대 결과 수 (1~100)

    Returns:
        이슈 목록 (number, title, state, user, assignees, labels, created_at, html_url)
    """
    logger.info("[TOOL] list_issues 호출: repo=%s, state=%s, labels=%s, assignee=%s", repo, state, labels, assignee)

    session = _get_github_session()
    if session is None:
        return [{"error": "GitHub이 연결되지 않았습니다. GITHUB_TOKEN을 설정해주세요."}]

    repo = _resolve_repo(repo)
    if not repo:
        return [{"error": "리포지토리를 지정해주세요. (예: owner/repo)"}]

    try:
        params: dict = {
            "state": state,
            "per_page": min(max_results, 100),
            "sort": "updated",
            "direction": "desc",
        }
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee

        resp = session.get(f"{BASE_URL}/repos/{repo}/issues", params=params)
        resp.raise_for_status()
        issues_raw = resp.json()

        results = []
        for issue in issues_raw:
            if "pull_request" in issue:
                continue
            results.append({
                "number": issue["number"],
                "title": issue["title"],
                "state": issue["state"],
                "user": issue["user"]["login"],
                "assignees": [a["login"] for a in issue.get("assignees", [])],
                "labels": [lb["name"] for lb in issue.get("labels", [])],
                "created_at": issue["created_at"],
                "updated_at": issue["updated_at"],
                "html_url": issue["html_url"],
                "comments": issue.get("comments", 0),
            })
        logger.info("[TOOL] list_issues: %d개 이슈 반환", len(results))
        return results
    except requests.HTTPError as exc:
        logger.error("[TOOL] list_issues 실패: %s", exc, exc_info=True)
        return [{"error": f"이슈 목록 조회 실패: {exc.response.status_code} {exc.response.reason}"}]
    except Exception as exc:
        logger.error("[TOOL] list_issues 실패: %s", exc, exc_info=True)
        return [{"error": f"이슈 목록 조회 실패: {exc}"}]


@tool
def get_issue(
    repo: str = "",
    issue_number: int = 0,
) -> dict:
    """GitHub 이슈의 상세 정보를 조회합니다.

    Args:
        repo: 리포지토리 (owner/name 형식). 비어있으면 기본 리포 사용.
        issue_number: 이슈 번호

    Returns:
        이슈 상세 정보 (number, title, body, state, user, assignees, labels, recent_comments, html_url)
    """
    logger.info("[TOOL] get_issue 호출: repo=%s, issue_number=%d", repo, issue_number)

    session = _get_github_session()
    if session is None:
        return {"error": "GitHub이 연결되지 않았습니다. GITHUB_TOKEN을 설정해주세요."}

    repo = _resolve_repo(repo)
    if not repo:
        return {"error": "리포지토리를 지정해주세요. (예: owner/repo)"}

    try:
        resp = session.get(f"{BASE_URL}/repos/{repo}/issues/{issue_number}")
        resp.raise_for_status()
        issue = resp.json()

        # 최근 코멘트 5개
        comments = []
        if issue.get("comments", 0) > 0:
            comments_resp = session.get(
                f"{BASE_URL}/repos/{repo}/issues/{issue_number}/comments",
                params={"per_page": 5},
            )
            if comments_resp.ok:
                for c in comments_resp.json()[-5:]:
                    comments.append({
                        "user": c["user"]["login"],
                        "body": c["body"][:500],
                        "created_at": c["created_at"],
                    })

        result = {
            "number": issue["number"],
            "title": issue["title"],
            "body": (issue.get("body") or "")[:1000],
            "state": issue["state"],
            "user": issue["user"]["login"],
            "assignees": [a["login"] for a in issue.get("assignees", [])],
            "labels": [lb["name"] for lb in issue.get("labels", [])],
            "created_at": issue["created_at"],
            "updated_at": issue["updated_at"],
            "html_url": issue["html_url"],
            "comments_count": issue.get("comments", 0),
            "recent_comments": comments,
        }
        logger.info("[TOOL] get_issue: 조회 성공 (Issue #%d)", issue_number)
        return result
    except requests.HTTPError as exc:
        logger.error("[TOOL] get_issue 실패: %s", exc, exc_info=True)
        return {"error": f"이슈 조회 실패: {exc.response.status_code} {exc.response.reason}"}
    except Exception as exc:
        logger.error("[TOOL] get_issue 실패: %s", exc, exc_info=True)
        return {"error": f"이슈 조회 실패: {exc}"}


@tool
def create_issue_comment(
    repo: str = "",
    issue_number: int = 0,
    body: str = "",
) -> dict:
    """GitHub 이슈 또는 PR에 코멘트를 작성합니다.

    Args:
        repo: 리포지토리 (owner/name 형식). 비어있으면 기본 리포 사용.
        issue_number: 이슈/PR 번호
        body: 코멘트 내용

    Returns:
        작성 결과 (status, id, html_url)
    """
    logger.info("[TOOL] create_issue_comment 호출: repo=%s, issue_number=%d", repo, issue_number)

    session = _get_github_session()
    if session is None:
        return {"error": "GitHub이 연결되지 않았습니다. GITHUB_TOKEN을 설정해주세요."}

    repo = _resolve_repo(repo)
    if not repo:
        return {"error": "리포지토리를 지정해주세요. (예: owner/repo)"}

    if not body.strip():
        return {"error": "코멘트 내용을 입력해주세요."}

    try:
        resp = session.post(
            f"{BASE_URL}/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        comment = resp.json()

        logger.info("[TOOL] create_issue_comment: 작성 성공 (id=%s)", comment["id"])
        return {
            "status": "success",
            "id": comment["id"],
            "html_url": comment["html_url"],
        }
    except requests.HTTPError as exc:
        logger.error("[TOOL] create_issue_comment 실패: %s", exc, exc_info=True)
        return {"error": f"코멘트 작성 실패: {exc.response.status_code} {exc.response.reason}"}
    except Exception as exc:
        logger.error("[TOOL] create_issue_comment 실패: %s", exc, exc_info=True)
        return {"error": f"코멘트 작성 실패: {exc}"}


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def get_github_tools() -> list:
    """GITHUB_TOKEN이 설정되어 있으면 GitHub 도구 목록을 반환한다. 없으면 빈 리스트."""
    config = Config()
    if not config.github_token:
        logger.warning("[TOOL] GitHub 토큰 없음 → GitHub 도구 비활성화")
        return []

    tools = [
        list_pull_requests,
        get_pull_request,
        list_issues,
        get_issue,
        create_issue_comment,
    ]
    logger.info("[TOOL] get_github_tools: %d개 도구 반환", len(tools))
    return tools
