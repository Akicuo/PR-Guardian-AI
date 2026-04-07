import hashlib
import hmac
import json
import logging
from typing import Any, Dict

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .github_app import is_trusted_repository_user
from .review_engine import (
    build_review_chunks_from_diff,
    collect_file_contexts,
    render_review_markdown,
    run_review_chunks,
)

# ==========================
# Settings & Logging
# ==========================

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("pr-guardian")

# FastAPI app
app = FastAPI(title="PR Guardian AI Webhook")


# ==========================
# Helpers
# ==========================

def verify_github_signature(
    body: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    """
    Verify X-Hub-Signature-256 from GitHub webhook.
    Skips verification if secret is not configured (PAT mode).
    """
    if not secret:
        logger.info("No webhook secret configured - skipping signature verification")
        return

    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header")
        raise HTTPException(status_code=400, detail="Missing signature header")

    try:
        sha_name, signature = signature_header.split("=")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid signature format")

    if sha_name != "sha256":
        raise HTTPException(status_code=400, detail="Unsupported hash algorithm")

    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected = mac.hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")


def get_github_token() -> str:
    """
    Get the GitHub Personal Access Token.
    """
    return settings.github_token


async def fetch_pr_diff(diff_url: str) -> str:
    """
    Fetch PR diff text using GitHub PAT.
    """
    token = get_github_token()
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "PR-Guardian-AI/1.0",
    }

    logger.info(f"Fetching diff from: {diff_url}")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(diff_url, headers=headers)
        logger.info(f"Diff fetch status: {resp.status_code}")
        resp.raise_for_status()
        return resp.text


async def post_pr_comment(comments_url: str, body: str) -> None:
    """
    Post a comment on the PR using the issue comments URL with GitHub PAT.
    """
    token = get_github_token()
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PR-Guardian-AI/1.0 (+{settings.bot_name})",
    }

    payload = {"body": body}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(comments_url, headers=headers, json=payload)
        logger.info(f"Post comment status: {resp.status_code}")
        resp.raise_for_status()


async def review_diff_with_ai(
    diff_text: str,
    pr_title: str,
    pr_body: str | None,
    repo_full_name: str,
    pr_number: int | str,
    head_sha: str,
) -> str:
    """
    Send the diff to OpenAI and get a review comment.
    """
    logger.info(
        ">>> Sending diff to AI (model: %s, base_url: %s)",
        settings.openai_model_id,
        settings.openai_base_url,
    )
    logger.info(">>> Diff length: %s chars", len(diff_text))

    file_contexts = await collect_file_contexts(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        context_lines=settings.review_context_lines,
    )
    chunks = build_review_chunks_from_diff(diff_text, file_contexts)
    logger.info(">>> Built %s review chunk(s)", len(chunks))

    result = await run_review_chunks(pr_title, pr_body, chunks)
    return render_review_markdown(result)


# ==========================
# Routes
# ==========================

@app.get("/")
async def root():
    return {"status": "ok", "app": "PR Guardian AI"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    raw_body = await request.body()

    verify_github_signature(raw_body, x_hub_signature_256, settings.github_webhook_secret)

    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.exception("Invalid JSON payload")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("=" * 30)
    logger.info(">>> Webhook received")
    logger.info(f">>> Event: {x_github_event}")

    # 1) Ping
    if x_github_event == "ping":
        return JSONResponse({"msg": "pong"})

    # 2) Installation
    if x_github_event == "installation":
        logger.info(f"Installation payload: {payload.get('action')}")
        return JSONResponse({"msg": "installation event ok"})

    # 3) Pull Request
    if x_github_event == "pull_request":
        action = payload.get("action")
        logger.info(f">>> Action: {action}")

        if action not in {"opened", "synchronize", "reopened"}:
            logger.info("Ignoring PR action: %s", action)
            return JSONResponse({"msg": f"ignored action {action}"})

        pr = payload.get("pull_request", {})
        comments_url = pr.get("comments_url")
        diff_url = pr.get("diff_url")
        pr_title = pr.get("title", "")
        pr_body = pr.get("body", "")
        pr_number = pr.get("number", "")
        head_sha = pr.get("head", {}).get("sha", "")
        pr_author = pr.get("user", {}).get("login", "")
        pr_author_association = pr.get("author_association", "")
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        repo_owner = payload.get("repository", {}).get("owner", {}).get("login", "")
        repo_name = payload.get("repository", {}).get("name", "")

        logger.info(f">>> PR: {repo_full_name}#{pr_number}")
        logger.info(f">>> Title: {pr_title}")
        logger.info(f">>> comments_url: {comments_url}")
        logger.info(f">>> diff_url: {diff_url}")
        logger.info(
            ">>> PR author: %s (association=%s)",
            pr_author or "unknown",
            pr_author_association or "unknown",
        )

        try:
            trusted_author = await is_trusted_repository_user(
                repo_owner=repo_owner,
                repo_name=repo_name,
                username=pr_author,
                author_association=pr_author_association,
            )
        except Exception as e:
            logger.exception("Failed to determine PR author permissions")
            raise HTTPException(status_code=500, detail="Failed to determine PR author permissions") from e

        if not trusted_author:
            logger.info(
                "Skipping automated review for %s#%s because PR author %s is not an org member and lacks write access",
                repo_full_name,
                pr_number,
                pr_author or "unknown",
            )
            return JSONResponse({"msg": "ignored untrusted PR author"})

        # Fetch diff
        try:
            diff_text = await fetch_pr_diff(diff_url)
        except Exception as e:
            logger.exception("Failed to fetch PR diff")
            raise HTTPException(status_code=500, detail="Failed to fetch PR diff") from e

        # Review with AI
        try:
            review = await review_diff_with_ai(
                diff_text=diff_text,
                pr_title=pr_title,
                pr_body=pr_body,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
            )
            logger.info(f">>> AI review generated (length: {len(review)} chars)")
            logger.debug(f">>> Review content: {review[:200]}...")
        except Exception as e:
            logger.exception("Failed to generate AI review")
            raise HTTPException(status_code=500, detail="Failed to generate AI review") from e

        # Validate review content
        if not review or not review.strip():
            logger.error("AI returned empty review!")
            review = "_Unable to generate review. Please check the API configuration._"

        # Create bot comment with clear identification
        comment_body = f"""## Code Review by {settings.bot_name}

<div align="center">

**This is an automated code review**

</div>

***

{review}

***

*This comment was automatically generated by [{settings.bot_name}]({settings.openai_base_url}) using {settings.openai_model_id}*"""

        # Post comment
        try:
            await post_pr_comment(comments_url, comment_body)
        except Exception as e:
            logger.exception("Failed to post PR comment")
            raise HTTPException(status_code=500, detail="Failed to post PR comment") from e

        logger.info(f">>> Successfully posted AI review to {repo_full_name}#{pr_number}")
        return JSONResponse({"msg": "AI review posted"})

    logger.info(f"Unhandled event: {x_github_event}")
    return JSONResponse({"msg": f"unhandled event {x_github_event}"})
