import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .config import get_settings
from .github_app import github_request, is_trusted_repository_user
from .review_engine import get_openai_client

logger = logging.getLogger("pr-guardian.merge")

MERGE_CONFLICT_OFFER_MARKER = "<!-- pr-guardian-conflict-offer -->"
UNRESOLVABLE_TOKEN = "__PR_GUARDIAN_UNRESOLVABLE__"
COMMAND_PREFIXES = ("/pr-guardian", "@pr-guardian", "pr guardian ai", "pr guardian")
COMMAND_PHRASES = (
    "resolve conflicts",
    "resolve merge conflicts",
    "fix merge conflicts",
    "fix the merge conflicts",
    "solve merge conflicts",
    "solve the merge conflicts",
)
SUPPORTED_CONFLICT_STATUSES = {"modified", "added"}
MERGE_RESOLUTION_SYSTEM_PROMPT = """You resolve Git merge conflicts for source files.

Rules:
- Produce a single merged file that keeps the intended behavior from both branches whenever possible.
- Preserve valid syntax, imports, and formatting for the file type.
- Prefer integrating both sides instead of dropping one side's logic.
- Return ONLY the merged file contents.
- Do not wrap the result in Markdown fences.
- Do not include explanations unless you cannot safely resolve the conflict.

If the conflict cannot be resolved confidently, return exactly:
__PR_GUARDIAN_UNRESOLVABLE__
<one short reason>
"""


@dataclass
class ConflictCandidate:
    path: str
    ancestor_content: str | None
    base_content: str
    head_content: str
    base_status: str
    head_status: str


@dataclass
class ConflictResolution:
    path: str
    merged_content: str


@dataclass
class MergeConflictResult:
    success: bool
    message: str
    resolved_files: list[str]
    skipped_files: list[str]
    commit_sha: str | None = None


def normalize_comment_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def is_merge_conflict_command(text: str, bot_name: str) -> bool:
    normalized = normalize_comment_text(text)
    if not normalized:
        return False

    command_phrase_found = any(phrase in normalized for phrase in COMMAND_PHRASES)
    if not command_phrase_found:
        return False

    bot_aliases = set(COMMAND_PREFIXES)
    bot_name_normalized = normalize_comment_text(bot_name)
    if bot_name_normalized:
        bot_aliases.add(bot_name_normalized)

    return normalized.startswith(COMMAND_PREFIXES) or any(alias in normalized for alias in bot_aliases)


def build_merge_conflict_offer_comment(bot_name: str) -> str:
    return (
        f"{MERGE_CONFLICT_OFFER_MARKER}\n"
        f"## Merge Conflict Help from {bot_name}\n\n"
        "This pull request currently has merge conflicts.\n\n"
        "If you want, an authorized repo user or the PR author can ask me to try resolving them by commenting:\n\n"
        "`/pr-guardian resolve conflicts`\n\n"
        "I will only push a resolution commit after an explicit request."
    )


def merge_conflict_offer_already_posted(comments: list[dict[str, Any]]) -> bool:
    return any(MERGE_CONFLICT_OFFER_MARKER in str(comment.get("body", "")) for comment in comments)


async def is_authorized_conflict_requester(
    repo_owner: str,
    repo_name: str,
    username: str,
    author_association: str | None,
    pr_author_username: str,
) -> bool:
    if username and pr_author_username and username.casefold() == pr_author_username.casefold():
        return True

    return await is_trusted_repository_user(
        repo_owner=repo_owner,
        repo_name=repo_name,
        username=username,
        author_association=author_association,
    )


async def get_issue_comments(comments_url: str) -> list[dict[str, Any]]:
    response = await github_request("GET", comments_url)
    return response.json()


async def get_compare_details(repo_owner: str, repo_name: str, base: str, head: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/compare/{quote(base, safe='')}...{quote(head, safe='')}"
    response = await github_request("GET", url)
    return response.json()


async def fetch_text_file_content(repo_owner: str, repo_name: str, path: str, ref: str) -> str | None:
    url = (
        f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/"
        f"{quote(path, safe='')}?ref={quote(ref, safe='')}"
    )
    try:
        response = await github_request("GET", url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise

    payload = response.json()
    if payload.get("encoding") != "base64" or "content" not in payload:
        return None

    try:
        return base64.b64decode(payload["content"]).decode("utf-8")
    except UnicodeDecodeError:
        return None


async def build_conflict_candidates(
    repo_owner: str,
    repo_name: str,
    base_sha: str,
    head_sha: str,
) -> tuple[list[ConflictCandidate], list[str]]:
    settings = get_settings()
    compare = await get_compare_details(repo_owner, repo_name, base_sha, head_sha)
    merge_base_sha = compare.get("merge_base_commit", {}).get("sha")
    if not merge_base_sha:
        return [], ["Unable to determine the merge base for this pull request."]

    base_compare = await get_compare_details(repo_owner, repo_name, merge_base_sha, base_sha)
    head_compare = await get_compare_details(repo_owner, repo_name, merge_base_sha, head_sha)

    base_files = {item["filename"]: item for item in base_compare.get("files", [])}
    head_files = {item["filename"]: item for item in head_compare.get("files", [])}
    candidate_paths = sorted(set(base_files) & set(head_files))
    skipped_files: list[str] = []

    if settings.merge_conflict_max_files > 0:
        original_count = len(candidate_paths)
        candidate_paths = candidate_paths[: settings.merge_conflict_max_files]
        if original_count > len(candidate_paths):
            skipped_files.append(
                f"{original_count - len(candidate_paths)} additional files exceeded the automatic merge limit"
            )

    candidates: list[ConflictCandidate] = []

    for path in candidate_paths:
        base_info = base_files[path]
        head_info = head_files[path]
        base_status = str(base_info.get("status", "modified"))
        head_status = str(head_info.get("status", "modified"))

        if base_status not in SUPPORTED_CONFLICT_STATUSES or head_status not in SUPPORTED_CONFLICT_STATUSES:
            skipped_files.append(f"{path} ({base_status}/{head_status})")
            continue

        ancestor_content, base_content, head_content = await asyncio.gather(
            fetch_text_file_content(repo_owner, repo_name, path, merge_base_sha),
            fetch_text_file_content(repo_owner, repo_name, path, base_sha),
            fetch_text_file_content(repo_owner, repo_name, path, head_sha),
        )

        if base_content is None or head_content is None:
            skipped_files.append(f"{path} (binary, removed, or unsupported encoding)")
            continue

        if base_content == head_content:
            continue

        if ancestor_content == base_content or ancestor_content == head_content:
            continue

        total_chars = len(base_content) + len(head_content) + len(ancestor_content or "")
        if total_chars > settings.merge_conflict_max_input_chars:
            skipped_files.append(f"{path} (too large to merge automatically)")
            continue

        candidates.append(
            ConflictCandidate(
                path=path,
                ancestor_content=ancestor_content,
                base_content=base_content,
                head_content=head_content,
                base_status=base_status,
                head_status=head_status,
            )
        )

    return candidates, skipped_files


def build_merge_resolution_prompt(candidate: ConflictCandidate) -> str:
    ancestor = candidate.ancestor_content if candidate.ancestor_content is not None else "(file did not exist at merge base)"
    return (
        f"Resolve the merge conflict for `{candidate.path}`.\n\n"
        "Git merge base version:\n"
        "```text\n"
        f"{ancestor}\n"
        "```\n\n"
        "Current base branch version:\n"
        "```text\n"
        f"{candidate.base_content}\n"
        "```\n\n"
        "Current pull request branch version:\n"
        "```text\n"
        f"{candidate.head_content}\n"
        "```"
    )


def parse_merge_resolution_output(content: str) -> tuple[str | None, str | None]:
    cleaned = (content or "").strip()
    if not cleaned:
        return None, "The model returned an empty merge result."

    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()

    if cleaned.startswith(UNRESOLVABLE_TOKEN):
        reason = cleaned[len(UNRESOLVABLE_TOKEN):].strip() or "The model could not resolve the conflict safely."
        return None, reason

    return cleaned, None


def _call_openai_merge_resolution(candidate: ConflictCandidate) -> tuple[str | None, str | None]:
    settings = get_settings()
    response = get_openai_client().chat.completions.create(
        model=settings.openai_model_id,
        messages=[
            {"role": "system", "content": MERGE_RESOLUTION_SYSTEM_PROMPT},
            {"role": "user", "content": build_merge_resolution_prompt(candidate)},
        ],
        temperature=0,
        max_tokens=settings.merge_conflict_max_output_tokens,
    )
    if not response.choices:
        return None, "The model returned no merge resolution."

    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        return None, "The model hit its output limit before finishing the merged file."

    message = choice.message
    content = (message.content or "").strip()
    if not content and hasattr(message, "reasoning_content"):
        content = (message.reasoning_content or "").strip()
    return parse_merge_resolution_output(content)


async def resolve_candidates_with_ai(candidates: list[ConflictCandidate]) -> tuple[list[ConflictResolution], list[str]]:
    resolutions: list[ConflictResolution] = []
    skipped_files: list[str] = []

    for candidate in candidates:
        merged_content, error = await asyncio.to_thread(_call_openai_merge_resolution, candidate)
        if merged_content is None:
            skipped_files.append(f"{candidate.path} ({error})")
            continue

        resolutions.append(ConflictResolution(path=candidate.path, merged_content=merged_content))

    return resolutions, skipped_files


async def get_commit_details(repo_owner: str, repo_name: str, commit_sha: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/commits/{quote(commit_sha, safe='')}"
    response = await github_request("GET", url)
    return response.json()


async def create_blob(repo_owner: str, repo_name: str, content: str) -> str:
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/blobs"
    response = await github_request(
        "POST",
        url,
        json={"content": content, "encoding": "utf-8"},
    )
    return response.json()["sha"]


async def create_tree(repo_owner: str, repo_name: str, base_tree_sha: str, entries: list[dict[str, Any]]) -> str:
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/trees"
    response = await github_request(
        "POST",
        url,
        json={"base_tree": base_tree_sha, "tree": entries},
    )
    return response.json()["sha"]


async def create_commit(repo_owner: str, repo_name: str, message: str, tree_sha: str, parent_sha: str) -> str:
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/commits"
    response = await github_request(
        "POST",
        url,
        json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
    )
    return response.json()["sha"]


async def update_branch_ref(repo_owner: str, repo_name: str, branch_name: str, commit_sha: str) -> None:
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs/heads/{quote(branch_name, safe='')}"
    await github_request("PATCH", url, json={"sha": commit_sha, "force": False})


async def create_resolution_commit(
    repo_owner: str,
    repo_name: str,
    head_branch: str,
    head_sha: str,
    resolutions: list[ConflictResolution],
    pr_number: int,
) -> str:
    commit = await get_commit_details(repo_owner, repo_name, head_sha)
    base_tree_sha = commit["tree"]["sha"]

    tree_entries: list[dict[str, Any]] = []
    for resolution in resolutions:
        blob_sha = await create_blob(repo_owner, repo_name, resolution.merged_content)
        tree_entries.append(
            {
                "path": resolution.path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
        )

    tree_sha = await create_tree(repo_owner, repo_name, base_tree_sha, tree_entries)
    commit_sha = await create_commit(
        repo_owner,
        repo_name,
        f"Resolve merge conflicts for PR #{pr_number}",
        tree_sha,
        head_sha,
    )
    await update_branch_ref(repo_owner, repo_name, head_branch, commit_sha)
    return commit_sha


async def try_resolve_pull_request_conflicts(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    pr_details: dict[str, Any],
) -> MergeConflictResult:
    head = pr_details.get("head", {})
    base = pr_details.get("base", {})
    head_repo = head.get("repo", {}) or {}
    base_repo = base.get("repo", {}) or {}

    if head_repo.get("full_name") != base_repo.get("full_name"):
        return MergeConflictResult(
            success=False,
            message="This PR comes from a fork, so I cannot safely push an automatic conflict resolution commit with the current token setup.",
            resolved_files=[],
            skipped_files=[],
        )

    head_sha = head.get("sha", "")
    base_sha = base.get("sha", "")
    head_branch = head.get("ref", "")

    if not head_sha or not base_sha or not head_branch:
        return MergeConflictResult(
            success=False,
            message="The pull request payload is missing the branch information needed to resolve conflicts.",
            resolved_files=[],
            skipped_files=[],
        )

    candidates, skipped_files = await build_conflict_candidates(
        repo_owner=repo_owner,
        repo_name=repo_name,
        base_sha=base_sha,
        head_sha=head_sha,
    )
    if not candidates:
        message = "I couldn't find any text conflicts I can safely rewrite automatically."
        if skipped_files:
            message += " Some files need a human merge: " + ", ".join(skipped_files[:5])
        return MergeConflictResult(
            success=False,
            message=message,
            resolved_files=[],
            skipped_files=skipped_files,
        )

    resolutions, unresolved_files = await resolve_candidates_with_ai(candidates)
    skipped_files.extend(unresolved_files)

    if not resolutions:
        return MergeConflictResult(
            success=False,
            message="I reviewed the conflicting files but couldn't produce a safe automatic resolution.",
            resolved_files=[],
            skipped_files=skipped_files,
        )

    if skipped_files:
        return MergeConflictResult(
            success=False,
            message=(
                "I found some resolvable conflicts, but I stopped before pushing because other files still need manual attention: "
                + ", ".join(skipped_files[:5])
            ),
            resolved_files=[resolution.path for resolution in resolutions],
            skipped_files=skipped_files,
        )

    commit_sha = await create_resolution_commit(
        repo_owner=repo_owner,
        repo_name=repo_name,
        head_branch=head_branch,
        head_sha=head_sha,
        resolutions=resolutions,
        pr_number=pr_number,
    )
    return MergeConflictResult(
        success=True,
        message="I pushed a merge conflict resolution commit to the PR branch. GitHub may take a moment to recalculate the merge status.",
        resolved_files=[resolution.path for resolution in resolutions],
        skipped_files=[],
        commit_sha=commit_sha,
    )
