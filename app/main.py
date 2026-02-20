import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any, Dict

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from openai import OpenAI

from .config import get_settings
from .core.database import get_db, init_db, close_db
from .api import auth, repositories, dashboard
from .models import Repository, ReviewHistory, User

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
openai_client = OpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url
)

# FastAPI app
app = FastAPI(title="PR Guardian AI")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="app/templates")

# Include API routers
app.include_router(auth.router)
app.include_router(repositories.router)
app.include_router(dashboard.router)


# ==========================
# Lifecycle Events
# ==========================

@app.on_event("startup")
async def startup_event():
    """Initialize database and run startup tasks"""
    await init_db()
    logger.info("Database initialized")

    # Optionally run migrations in development
    if settings.log_level == "debug":
        logger.info("Running database migrations in debug mode")
        try:
            from alembic import command
            from alembic.config import Config
            from pathlib import Path

            # Create Alembic config
            alembic_dir = Path(__file__).parent.parent / "alembic"
            if alembic_dir.exists():
                alembic_cfg = Config()
                alembic_cfg.set_main_option("script_location", str(alembic_dir))
                alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)

                # Run migrations
                command.upgrade(alembic_cfg, "head")
                logger.info("Database migrations completed")
        except Exception as e:
            logger.warning(f"Could not run migrations: {e}")

    logger.info("Application started")


@app.on_event("shutdown")
async def shutdown_event():
    """Close database connections on shutdown"""
    await close_db()
    logger.info("Application shutdown")


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


async def review_diff_with_ai(diff_text: str, pr_title: str, pr_body: str | None) -> str:
    """
    Send the diff to OpenAI and get a review comment.
    """
    max_chars = 16000
    short_diff = diff_text[:max_chars]

    logger.info(f">>> Sending diff to AI (model: {settings.openai_model_id}, base_url: {settings.openai_base_url})")
    logger.info(f">>> Diff length: {len(diff_text)} chars, truncated to: {len(short_diff)} chars")

    system_prompt = (
        "You are an expert senior code reviewer. "
        "Given a Git diff, you will provide a concise review:\n"
        "- Point out potential bugs, security risks, and performance issues.\n"
        "- Suggest improvements and best practices.\n"
        "- If everything looks good, say that explicitly.\n"
        "- Answer in German (Swiss style german but not schweizerdeutsch) and use Markdown with bullet points."
    )

    user_prompt = f"""
Pull Request Title: {pr_title}

Pull Request Description:
{pr_body or "(no description)"}

Git Diff:
{short_diff}
"""

    def _call_openai() -> str:
        try:
            # Build request with thinking mode disabled for Z.AI to get content in standard field
            request_params = {
                "model": settings.openai_model_id,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 16000,
            }

            # Disable thinking mode for Z.AI/GLM to get response in content field
            if "z.ai" in settings.openai_base_url.lower():
                request_params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
                logger.info(">>> Disabled thinking mode for Z.AI API")

            logger.info(">>> Calling AI API...")
            resp = openai_client.chat.completions.create(**request_params)

            # Log the full response structure for debugging
            logger.info(f">>> Full API response: {resp}")
            logger.info(f">>> Response choices: {resp.choices}")
            logger.info(f">>> First choice message: {resp.choices[0].message}")
            logger.info(f">>> Message content field: '{resp.choices[0].message.content}'")
            if hasattr(resp.choices[0].message, 'reasoning_content'):
                logger.info(f">>> Message reasoning_content field length: {len(resp.choices[0].message.reasoning_content or '')}")

            # Check if response has choices
            if not resp.choices:
                logger.error("AI returned empty choices array")
                return "_Error: AI returned no response._"

            message = resp.choices[0].message

            # Use the standard content field (should contain final answer with thinking disabled)
            content = message.content

            if not content:
                logger.error("AI returned empty content field (thinking mode may still be enabled)")
                # Last resort: check reasoning_content
                if hasattr(message, 'reasoning_content') and message.reasoning_content:
                    logger.warning("Falling back to reasoning_content - thinking mode may not be disabled properly")
                    logger.info(f">>> Using reasoning_content (first 200 chars): {message.reasoning_content[:200]}")
                    content = message.reasoning_content
                else:
                    return "_Error: AI returned empty content._"

            content = content.strip()
            logger.info(f">>> Final content length: {len(content)} chars")
            logger.info(f">>> Final content preview: {content[:200]}")
            return content
        except Exception as e:
            logger.error(f"AI API error: {e}")
            raise

    review_text = await asyncio.to_thread(_call_openai)
    return review_text


# ==========================
# Routes
# ==========================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Landing page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: AsyncSession = Depends(get_db)):
    """User dashboard"""
    try:
        # Try to get current user
        current_user = await auth.get_current_user(request, db)
    except HTTPException:
        # Not authenticated, redirect to login
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth/login", status_code=303)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "current_user": current_user
    })


@app.get("/repositories", response_class=HTMLResponse)
async def repositories_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Repository management page"""
    try:
        current_user = await auth.get_current_user(request, db)
    except HTTPException:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth/login", status_code=303)

    return templates.TemplateResponse("repositories.html", {
        "request": request,
        "current_user": current_user
    })


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    db: AsyncSession = Depends(get_db),
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
        repository_payload = payload.get("repository", {})
        comments_url = pr.get("comments_url")
        diff_url = pr.get("diff_url")
        pr_title = pr.get("title", "")
        pr_body = pr.get("body", "")
        pr_number = pr.get("number", "")
        repo_full_name = repository_payload.get("full_name", "")
        github_repo_id = repository_payload.get("id", "")
        pr_author = pr.get("user", {}).get("login", "")
        pr_url = pr.get("html_url", "")
        target_branch = pr.get("base", {}).get("ref", "")

        logger.info(f">>> PR: {repo_full_name}#{pr_number}")
        logger.info(f">>> Title: {pr_title}")
        logger.info(f">>> Target branch: {target_branch}")

        # Check if repository is monitored in database
        result = await db.execute(
            select(Repository).where(
                Repository.github_repo_id == github_repo_id,
                Repository.is_monitored == True
            )
        )
        repository = result.scalar_one_or_none()

        if not repository:
            logger.info(f">>> Repository {repo_full_name} not monitored, skipping")
            return JSONResponse({"msg": "repository not monitored"})

        logger.info(f">>> Repository found: user_id={repository.user_id}")

        # Check branch filtering
        if repository.branches_to_monitor:
            import json
            monitored_branches = json.loads(repository.branches_to_monitor)
            if target_branch not in monitored_branches:
                logger.info(f">>> Branch {target_branch} not in monitored branches {monitored_branches}, skipping")
                return JSONResponse({"msg": "branch not monitored"})

        # Fetch diff
        try:
            diff_text = await fetch_pr_diff(diff_url)
        except Exception as e:
            logger.exception("Failed to fetch PR diff")
            raise HTTPException(status_code=500, detail="Failed to fetch PR diff") from e

        # Review with AI
        review = None
        try:
            review = await review_diff_with_ai(diff_text, pr_title, pr_body)
            logger.info(f">>> AI review generated (length: {len(review)} chars)")
        except Exception as e:
            logger.exception("Failed to generate AI review")
            review = "_Error: Failed to generate AI review._"

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

        # Save review to database
        try:
            review_history = ReviewHistory(
                user_id=repository.user_id,
                repository_id=repository.id,
                pr_number=pr_number,
                pr_title=pr_title,
                pr_author=pr_author,
                pr_url=pr_url,
                review_content=review,
                ai_model=settings.openai_model_id,
                status="posted"
            )
            db.add(review_history)
            await db.commit()
            logger.info(f">>> Review saved to database")
        except Exception as e:
            logger.exception("Failed to save review to database")
            # Don't fail the request if we can't save to DB

        logger.info(f">>> Successfully posted AI review to {repo_full_name}#{pr_number}")
        return JSONResponse({"msg": "AI review posted"})

    logger.info(f"Unhandled event: {x_github_event}")
    return JSONResponse({"msg": f"unhandled event {x_github_event}"})
