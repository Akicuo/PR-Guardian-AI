import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any, Dict

import httpx
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

# Log startup configuration
logger.info("=" * 60)
logger.info("PR Guardian AI Starting")
logger.info(f"OpenAI Model: {settings.openai_model_id}")
logger.info(f"OpenAI Base URL: {settings.openai_base_url}")
logger.info(f"Bot Name: {settings.bot_name}")
logger.info(f"Max Verification Calls: {settings.max_verification_calls}")
logger.info(f"Log Level: {settings.log_level}")
logger.info("=" * 60)

# OpenAI client
openai_client = OpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url
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

@app.get("/")
async def root():
    return {"status": "ok", "app": "PR Guardian AI"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    # Generate request ID for tracing
    request_id = str(uuid.uuid4())[:8]

    raw_body = await request.body()

    verify_github_signature(raw_body, x_hub_signature_256, settings.github_webhook_secret)

    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.exception(f"[{request_id}] Invalid JSON payload")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("=" * 30)
    logger.info(f"[{request_id}] Webhook received")
    logger.info(f"[{request_id}] Event: {x_github_event}")

    # 1) Ping
    if x_github_event == "ping":
        return JSONResponse({"msg": "pong"})

    # 2) Installation
    if x_github_event == "installation":
        logger.info(f"[{request_id}] Installation payload: {payload.get('action')}")
        return JSONResponse({"msg": "installation event ok"})

    # 3) Pull Request
    if x_github_event == "pull_request":
        action = payload.get("action")
        logger.info(f"[{request_id}] Action: {action}")

        if action not in {"opened", "synchronize", "reopened"}:
            logger.info(f"[{request_id}] Ignoring PR action: {action}")
            return JSONResponse({"msg": f"ignored action {action}"})

        pr = payload.get("pull_request", {})
        comments_url = pr.get("comments_url")
        diff_url = pr.get("diff_url")
        pr_title = pr.get("title", "")
        pr_body = pr.get("body", "")
        pr_number = pr.get("number", "")
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        repo_owner, repo_name = repo_full_name.split("/", 1) if "/" in repo_full_name else (repo_full_name, "")

        # Variable state logging
        logger.debug(f"[{request_id}] Repository full name: {repo_full_name}")
        logger.debug(f"[{request_id}] Repository owner: '{repo_owner}', name: '{repo_name}'")
        logger.debug(f"[{request_id}] PR number: {pr_number}")
        logger.debug(f"[{request_id}] Comments URL: {comments_url}")
        logger.debug(f"[{request_id}] Diff URL: {diff_url}")

        # Validate required variables
        if not repo_owner or not repo_name:
            logger.error(f"[{request_id}] Invalid repo decomposition: owner='{repo_owner}', name='{repo_name}' from '{repo_full_name}'")

        logger.info(f"[{request_id}] PR: {repo_full_name}#{pr_number}")
        logger.info(f"[{request_id}] Title: {pr_title}")

        # ===== NEW VERIFICATION WORKFLOW =====

        # Step 1: Post initial acknowledgment comment
        initial_comment_body = f"""## {settings.bot_name} - Analyzing PR

<div align="center">

Thank you for creating this pull request!

I'm analyzing the changes now...

</div>

---

This review will be verified against the actual codebase to ensure accuracy.

*Analysis in progress...*"""

        try:
            await post_pr_comment(comments_url, initial_comment_body)
            logger.info(f"[{request_id}] Posted initial acknowledgment comment")
        except Exception as e:
            logger.exception(f"[{request_id}] Failed to post initial comment")
            # Continue anyway - this is not critical

        # Step 2: Fetch diff and generate draft review
        try:
            diff_text = await fetch_pr_diff(diff_url)
            logger.info(f"[{request_id}] Fetched diff: {len(diff_text)} chars")
        except Exception as e:
            logger.exception(f"[{request_id}] Failed to fetch PR diff")
            raise HTTPException(status_code=500, detail="Failed to fetch PR diff") from e

        try:
            from .ai_reviewer import generate_draft_review
            draft_review = await generate_draft_review(pr_title, pr_body, diff_text)
            logger.info(f"[{request_id}] Generated draft review: {len(draft_review)} chars")
            logger.debug(f"[{request_id}] Draft preview: {draft_review[:200]}...")
        except Exception as e:
            logger.exception(f"[{request_id}] Failed to generate draft review")
            raise HTTPException(status_code=500, detail="Failed to generate draft review") from e

        if not draft_review or not draft_review.strip():
            logger.error(f"[{request_id}] AI returned empty draft review!")
            draft_review = "_Unable to generate review._"

        # Step 3: Verify the review using GitHub API
        try:
            from .verifier import ReviewVerifier
            from .github_app import get_pr_head_ref, find_bot_comment, update_pr_comment

            # Get PR head ref
            logger.debug(f"[{request_id}] Verification parameters: repo_owner={repo_owner}, repo_name={repo_name}, pr_number={pr_number}")
            pr_head_ref, pr_head_sha = await get_pr_head_ref(repo_owner, repo_name, pr_number)
            logger.info(f"[{request_id}] PR head ref: {pr_head_ref}, sha: {pr_head_sha[:8] if pr_head_sha else 'N/A'}")

            # Initialize verifier
            verifier = ReviewVerifier(repo_owner, repo_name, pr_number)

            # Run verification
            verified_review, verification_results = await verifier.verify_review(
                draft_review,
                pr_head_ref or f"refs/pull/{pr_number}/head"
            )

            logger.info(f"[{request_id}] Verification complete: {len(verification_results)} claims checked")
            logger.info(f"[{request_id}] Tool calls made: {verifier.tool_calls_made}/{'unlimited' if verifier.unlimited else verifier.max_tool_calls}")

            # Log verification results
            for i, result in enumerate(verification_results):
                logger.debug(f"[{request_id}] Result {i+1}: claim='{result.claim.description}', valid={result.is_valid}")

            # Log corrections
            for result in verification_results:
                if not result.is_valid:
                    logger.info(f"[{request_id}] Corrected claim: {result.claim.description} -> {result.corrected_claim}")

        except Exception as e:
            logger.exception(f"[{request_id}] Verification failed, using draft review")
            # Fallback to draft review if verification fails
            verified_review = draft_review
            verification_results = []

        # Step 4: Post final verified review
        # Try to update the initial comment, or post a new one
        try:
            from .github_app import find_bot_comment, update_pr_comment

            # Try to find our initial comment
            initial_comment = await find_bot_comment(repo_owner, repo_name, pr_number, settings.bot_name)

            final_comment_body = f"""## Code Review by {settings.bot_name}

<div align="center">

**This is an automated code review**

</div>

***

{verified_review}

***

*This comment was automatically generated by [{settings.bot_name}]({settings.openai_base_url}) using {settings.openai_model_id}*"""

            if initial_comment:
                # Update the initial comment
                comment_id = initial_comment["id"]
                await update_pr_comment(repo_owner, repo_name, comment_id, final_comment_body)
                logger.info(f"[{request_id}] Updated initial comment (ID: {comment_id})")
            else:
                # Post new comment
                await post_pr_comment(comments_url, final_comment_body)
                logger.info(f"[{request_id}] Posted final verified review as new comment")

        except Exception as e:
            logger.exception(f"[{request_id}] Failed to update/post final comment")
            # Try to post as new comment if update failed
            try:
                final_comment_body = f"""## Code Review by {settings.bot_name}

<div align="center">

**This is an automated code review**

</div>

***

{verified_review}

***

*This comment was automatically generated by [{settings.bot_name}]({settings.openai_base_url}) using {settings.openai_model_id}*"""
                await post_pr_comment(comments_url, final_comment_body)
                logger.info(f"[{request_id}] Posted final review as new comment (fallback)")
            except Exception as e2:
                logger.exception(f"[{request_id}] Failed to post final comment")
                raise HTTPException(status_code=500, detail="Failed to post final comment") from e2

        logger.info(f"[{request_id}] Successfully posted verified review to {repo_full_name}#{pr_number}")
        return JSONResponse({"msg": "Verified review posted"})

    logger.info(f"[{request_id}] Unhandled event: {x_github_event}")
    return JSONResponse({"msg": f"unhandled event {x_github_event}"})
