import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from openai import OpenAI

from .config import get_settings

# ==========================
# Settings & Logging
# ==========================

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("pr-guardian")

# OpenAI client
client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
    base_url="https://api.z.ai/api/coding/paas/v4"   # Z.AI GLM Coding Plan endpoint
)

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

    """
    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header - skipping verification in DEV mode")
        return

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


def generate_app_jwt() -> str:
    """
    Generate JWT for GitHub App using RS256.
    """
    app_id = settings.github_app_id
    private_key_path = settings.github_private_key_path

    with open(private_key_path, "r", encoding="utf-8") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 9 * 60,
        "iss": app_id,
    }

    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token


async def create_installation_token(installation_id: int) -> str:
    """
    Create an installation access token for a given installation_id.
    """
    app_jwt = generate_app_jwt()

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "PR-Guardian-AI",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers)
        logger.info(f"Installation token status: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        return data["token"]


async def fetch_pr_diff(diff_url: str, token: str) -> str:
    """
    Fetch PR diff text.
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "PR-Guardian-AI",
    }

    logger.info(f"Fetching diff from: {diff_url}")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(diff_url, headers=headers)
        logger.info(f"First diff fetch status: {resp.status_code}")

        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get("Location")
            logger.info(f"Redirecting to: {redirect_url}")
            if not redirect_url:
                resp.raise_for_status()

            resp = await client.get(redirect_url, headers=headers)
            logger.info(f"Second diff fetch status: {resp.status_code}")

        resp.raise_for_status()
        return resp.text


async def post_pr_comment(comments_url: str, token: str, body: str) -> None:
    """
    Post a comment on the PR using the issue comments URL.
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "PR-Guardian-AI",
    }

    payload = {"body": body}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(comments_url, headers=headers, json=payload)
        logger.info(f"Post comment status: {resp.status_code}")
        resp.raise_for_status()


async def review_diff_with_ai(diff_text: str, pr_title: str, pr_body: str | None) -> str:
    """
    Send the diff to OpenAI and get a review comment.
    """
    max_chars = 16000
    short_diff = diff_text[:max_chars]

    system_prompt = (
        "You are an expert senior code reviewer. "
        "Given a Git diff, you will provide a concise review:\n"
        "- Point out potential bugs, security risks, and performance issues.\n"
        "- Suggest improvements and best practices.\n"
        "- If everything looks good, say that explicitly.\n"
        "- Answer in English and use Markdown with bullet points."
    )

    user_prompt = f"""
Pull Request Title: {pr_title}

Pull Request Description:
{pr_body or "(no description)"}

Git Diff:
{short_diff}
"""

    def _call_openai() -> str:
        resp = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=700,
        )
        return resp.choices[0].message.content.strip()

    review_text = await asyncio.to_thread(_call_openai)
    return review_text


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

        logger.info(f">>> PR comments_url: {comments_url}")
        logger.info(f">>> diff_url: {diff_url}")

        installation = payload.get("installation") or {}
        installation_id = installation.get("id")
        if not installation_id:
            logger.error("No installation id in payload")
            raise HTTPException(status_code=400, detail="Missing installation id")

        logger.info(f">>> Installation ID: {installation_id}")
        logger.info(">>> Creating installation access token...")

        try:
            gh_token = await create_installation_token(installation_id)
            logger.info(">>> Installation token created.")
        except Exception as e:
            logger.exception("Failed to create installation token")
            raise HTTPException(status_code=500, detail="Failed to create installation token") from e

        # Fetch diff
        try:
            diff_text = await fetch_pr_diff(diff_url, gh_token)
        except Exception as e:
            logger.exception("Failed to fetch PR diff")
            raise HTTPException(status_code=500, detail="Failed to fetch PR diff") from e

        # Review with AI
        try:
            review = await review_diff_with_ai(diff_text, pr_title, pr_body)
        except Exception as e:
            logger.exception("Failed to generate AI review")
            raise HTTPException(status_code=500, detail="Failed to generate AI review") from e

        comment_body = (
            "🤖 **PR Guardian AI Review**\n\n"
            f"{review}\n\n"
            "---\n"
            "_Automated review powered by OpenAI_"
        )

        # Post comment
        try:
            await post_pr_comment(comments_url, gh_token, comment_body)
        except Exception as e:
            logger.exception("Failed to post PR comment")
            raise HTTPException(status_code=500, detail="Failed to post PR comment") from e

        return JSONResponse({"msg": "AI review posted"})

    logger.info(f"Unhandled event: {x_github_event}")
    return JSONResponse({"msg": f"unhandled event {x_github_event}"})
