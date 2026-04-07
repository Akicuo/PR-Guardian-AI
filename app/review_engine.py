import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx
from openai import OpenAI

from .config import get_settings

logger = logging.getLogger("pr-guardian.review")

HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

REVIEW_SYSTEM_PROMPT = """You are a strict senior code reviewer.
Review only for concrete problems in the changed code.

Rules:
- Focus on bugs, correctness issues, security risks, regressions, missing validation, bad assumptions, and meaningful performance risks.
- Mention maintainability problems only when they create a real implementation or support risk.
- Do not praise the code.
- Do not mention positives.
- Do not say "looks good" or similar.
- Ignore stylistic nits unless they create a concrete risk.
- Only report findings that are grounded in the provided diff and context.
- If there are no significant issues, return no findings.

Return valid JSON only with this shape:
{
  "verdict": "bad" | "no_significant_issues",
  "summary": "one short neutral sentence",
  "findings": [
    {
      "severity": "high" | "medium" | "low",
      "file": "path/to/file",
      "location": "function, symbol, hunk, or line range",
      "title": "short issue title",
      "reason": "why this is a problem"
    }
  ]
}
"""

REVIEW_REPAIR_SYSTEM_PROMPT = """You convert code review output into strict JSON.
Return valid JSON only with this exact shape:
{
  "verdict": "bad" | "no_significant_issues",
  "summary": "one short neutral sentence",
  "findings": [
    {
      "severity": "high" | "medium" | "low",
      "file": "path/to/file",
      "location": "function, symbol, hunk, or line range",
      "title": "short issue title",
      "reason": "why this is a problem"
    }
  ]
}

Do not add markdown fences, prose, or extra keys.
If the source does not contain usable findings, return:
{"verdict":"no_significant_issues","summary":"No significant issues found in the changed code.","findings":[]}
"""

CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


@dataclass
class ReviewFinding:
    severity: str
    file: str
    location: str
    title: str
    reason: str


@dataclass
class ReviewResult:
    verdict: str
    summary: str
    findings: list[ReviewFinding]


@dataclass
class ChangedFileContext:
    filename: str
    status: str
    context_snippets: list[str]


class ReviewResponseParseError(ValueError):
    def __init__(self, message: str, raw_response: str, *, repair_attempted: bool) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.repair_attempted = repair_attempted

    @property
    def preview(self) -> str:
        normalized = re.sub(r"\s+", " ", self.raw_response).strip()
        if len(normalized) <= 240:
            return normalized
        return normalized[:237] + "..."


@lru_cache
def get_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


def get_github_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"token {settings.github_token}",
        "Accept": accept,
        "User-Agent": "PR-Guardian-AI/1.0",
    }


def split_diff_sections(diff_text: str) -> list[str]:
    if not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    sections: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        sections.append(current)

    joined = ["\n".join(section).strip() for section in sections if any(part.strip() for part in section)]
    return joined or [diff_text.strip()]


def split_diff_section_by_hunk(section: str, max_chars: int) -> list[str]:
    if len(section) <= max_chars:
        return [section]

    lines = section.splitlines()
    hunk_indexes = [index for index, line in enumerate(lines) if line.startswith("@@ ")]
    if not hunk_indexes:
        return [section]

    header = lines[:hunk_indexes[0]]
    hunks: list[list[str]] = []

    for index, start in enumerate(hunk_indexes):
        end = hunk_indexes[index + 1] if index + 1 < len(hunk_indexes) else len(lines)
        hunks.append(lines[start:end])

    chunks: list[str] = []
    current = list(header)

    for hunk in hunks:
        candidate = current + hunk
        if current != header and len("\n".join(candidate)) > max_chars:
            chunks.append("\n".join(current).strip())
            current = list(header) + hunk
        else:
            current = candidate

        if current != header and len("\n".join(current)) > max_chars:
            chunks.append("\n".join(current).strip())
            current = list(header)

    if current != header:
        chunks.append("\n".join(current).strip())

    return [chunk for chunk in chunks if chunk]


def pack_review_sections(sections: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for section in sections:
        separator_len = 2 if current else 0
        projected = current_len + separator_len + len(section)
        if current and projected > max_chars:
            chunks.append("\n\n".join(current))
            current = [section]
            current_len = len(section)
            continue

        current.append(section)
        current_len = projected

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def extract_filename_from_diff_section(section: str) -> str:
    plus_match = re.search(r"^\+\+\+ b/(.+)$", section, re.MULTILINE)
    if plus_match:
        return plus_match.group(1).strip()

    diff_match = re.search(r"^diff --git a/(.+) b/(.+)$", section, re.MULTILINE)
    if diff_match:
        right_name = diff_match.group(2).strip()
        if right_name != "/dev/null":
            return right_name
        return diff_match.group(1).strip()

    return "unknown"


def parse_patch_hunks(patch_text: str) -> list[tuple[int, int]]:
    hunks: list[tuple[int, int]] = []

    for line in patch_text.splitlines():
        match = HUNK_HEADER_RE.match(line)
        if not match:
            continue

        new_start = int(match.group("new_start"))
        new_count = int(match.group("new_count") or "1")
        if new_count == 0:
            anchor = max(new_start - 1, 1)
            hunks.append((anchor, anchor))
            continue

        end_line = new_start + new_count - 1
        hunks.append((new_start, end_line))

    return hunks


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []

    ordered = sorted(ranges)
    merged = [ordered[0]]

    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + 1:
            merged[-1] = (previous_start, max(previous_end, end))
            continue
        merged.append((start, end))

    return merged


def build_context_snippets(
    file_content: str,
    patch_text: str,
    context_lines: int,
    max_snippets: int = 4,
) -> list[str]:
    if not file_content or not patch_text:
        return []

    file_lines = file_content.splitlines()
    if not file_lines:
        return []

    ranges: list[tuple[int, int]] = []
    for start, end in parse_patch_hunks(patch_text):
        snippet_start = max(start - context_lines, 1)
        snippet_end = min(end + context_lines, len(file_lines))
        ranges.append((snippet_start, snippet_end))

    snippets: list[str] = []
    for start, end in merge_ranges(ranges)[:max_snippets]:
        width = len(str(end))
        rendered = "\n".join(
            f"{line_number:>{width}}: {file_lines[line_number - 1]}"
            for line_number in range(start, end + 1)
        )
        snippets.append(f"Lines {start}-{end}:\n{rendered}")

    return snippets


def format_context_block(file_context: ChangedFileContext | None, max_chars: int) -> str:
    if not file_context or not file_context.context_snippets:
        return ""

    snippets = list(file_context.context_snippets)
    while snippets:
        text = "Context snippets:\n```text\n" + "\n\n".join(snippets) + "\n```"
        if len(text) <= max_chars:
            return text
        snippets.pop()

    return ""


def build_review_sections(
    diff_text: str,
    file_contexts: dict[str, ChangedFileContext],
    max_chars: int,
) -> list[str]:
    sections: list[str] = []

    for diff_section in split_diff_sections(diff_text):
        filename = extract_filename_from_diff_section(diff_section)
        file_context = file_contexts.get(filename)
        context_block = format_context_block(file_context, max_chars // 3)

        for diff_chunk in split_diff_section_by_hunk(diff_section, max_chars):
            diff_block = f"Diff section:\n```diff\n{diff_chunk}\n```"
            section_parts = [f"File: {filename}"]
            if file_context:
                section_parts.append(f"Status: {file_context.status}")
            section_parts.append(diff_block)
            if context_block:
                section_parts.append(context_block)
            sections.append("\n\n".join(section_parts))

    return sections


async def fetch_pull_request_files(repo_full_name: str, pr_number: int | str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page = 1

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        while True:
            url = (
                f"https://api.github.com/repos/{repo_full_name}/pulls/"
                f"{pr_number}/files?per_page=100&page={page}"
            )
            response = await client.get(url, headers=get_github_headers())
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1

    return files


async def fetch_file_content(repo_full_name: str, path: str, ref: str) -> str | None:
    url = (
        f"https://api.github.com/repos/{repo_full_name}/contents/"
        f"{quote(path, safe='')}?ref={quote(ref, safe='')}"
    )

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=get_github_headers())
        if response.status_code == 404:
            return None
        response.raise_for_status()

    payload = response.json()
    if payload.get("encoding") != "base64" or "content" not in payload:
        return None

    return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")


async def build_file_context(
    repo_full_name: str,
    head_sha: str,
    file_info: dict[str, Any],
    context_lines: int,
) -> ChangedFileContext:
    filename = file_info["filename"]
    status = file_info.get("status", "modified")
    patch_text = file_info.get("patch") or ""

    if status == "removed" or not patch_text:
        return ChangedFileContext(filename=filename, status=status, context_snippets=[])

    try:
        file_content = await fetch_file_content(repo_full_name, filename, head_sha)
    except Exception:
        logger.warning("Failed to fetch context for %s at %s", filename, head_sha, exc_info=True)
        return ChangedFileContext(filename=filename, status=status, context_snippets=[])

    snippets = build_context_snippets(file_content or "", patch_text, context_lines)
    return ChangedFileContext(filename=filename, status=status, context_snippets=snippets)


async def collect_file_contexts(
    repo_full_name: str,
    pr_number: int | str,
    head_sha: str,
    context_lines: int,
) -> dict[str, ChangedFileContext]:
    if not repo_full_name or not pr_number or not head_sha:
        return {}

    try:
        files = await fetch_pull_request_files(repo_full_name, pr_number)
    except Exception:
        logger.warning(
            "Failed to fetch PR files for %s#%s, continuing without extra context",
            repo_full_name,
            pr_number,
            exc_info=True,
        )
        return {}

    tasks = [
        build_file_context(repo_full_name, head_sha, file_info, context_lines)
        for file_info in files
    ]
    results = await asyncio.gather(*tasks)
    return {result.filename: result for result in results}


def build_chunk_prompt(pr_title: str, pr_body: str | None, chunk_text: str, chunk_number: int, total_chunks: int) -> str:
    description = pr_body or "(no description)"
    return (
        f"Pull Request Title: {pr_title}\n"
        f"Pull Request Description:\n{description}\n\n"
        f"Review chunk {chunk_number} of {total_chunks}.\n"
        "Analyze only the changed code shown below and its attached surrounding context.\n"
        "Return JSON only.\n\n"
        f"{chunk_text}"
    )


def build_review_request_params(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
) -> dict[str, Any]:
    settings = get_settings()
    request_params: dict[str, Any] = {
        "model": settings.openai_model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }

    if "z.ai" in settings.openai_base_url.lower():
        request_params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    return request_params


def normalize_finding(raw_finding: dict[str, Any]) -> ReviewFinding | None:
    if not isinstance(raw_finding, dict):
        return None

    title = str(raw_finding.get("title", "")).strip()
    reason = str(raw_finding.get("reason", "")).strip()
    if not title or not reason:
        return None

    severity = str(raw_finding.get("severity", "medium")).strip().lower()
    if severity not in {"high", "medium", "low"}:
        severity = "medium"

    return ReviewFinding(
        severity=severity,
        file=str(raw_finding.get("file", "unknown")).strip() or "unknown",
        location=str(raw_finding.get("location", "changed code")).strip() or "changed code",
        title=title,
        reason=reason,
    )


def parse_review_response(text: str) -> ReviewResult:
    payload = extract_json_payload(text)
    findings = [
        finding
        for finding in (normalize_finding(item) for item in payload.get("findings", []))
        if finding
    ]

    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in {"bad", "no_significant_issues"}:
        verdict = "bad" if findings else "no_significant_issues"

    summary = str(payload.get("summary", "")).strip()
    if not summary:
        summary = "No significant issues found in the changed code." if not findings else "Found issues in the changed code."

    return ReviewResult(verdict=verdict, summary=summary, findings=findings)


def normalize_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [normalize_message_content(part) for part in content]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict):
        for key in ("text", "content", "value", "output_text"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    for attribute in ("text", "content", "value", "output_text"):
        value = getattr(content, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            normalized = normalize_message_content(value)
            if normalized:
                return normalized

    return ""


def decode_first_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if not candidate:
        return None

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", candidate):
        try:
            payload, _ = decoder.raw_decode(candidate[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    return None


def extract_json_payload(text: str) -> dict[str, Any]:
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(match.group(1).strip() for match in CODE_FENCE_RE.finditer(text))

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        payload = decode_first_json_object(candidate)
        if payload is not None:
            return payload

    raise ValueError("No JSON object found in model response")


def deduplicate_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[ReviewFinding] = []

    for finding in findings:
        key = (
            finding.file.casefold(),
            finding.location.casefold(),
            finding.title.casefold(),
            finding.reason.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    severity_order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda item: (severity_order.get(item.severity, 3), item.file, item.location, item.title))
    return unique


def merge_review_results(results: list[ReviewResult]) -> ReviewResult:
    findings = deduplicate_findings(
        [finding for result in results for finding in result.findings]
    )
    if findings:
        summary = f"Found {len(findings)} issue(s) in the changed code."
        return ReviewResult(verdict="bad", summary=summary, findings=findings)

    return ReviewResult(
        verdict="no_significant_issues",
        summary="No significant issues found in the changed code.",
        findings=[],
    )


def render_review_markdown(result: ReviewResult) -> str:
    if result.findings:
        lines = [
            "**Verdict:** Bad",
            "",
            f"**Summary:** {result.summary}",
            "",
            "### Bad parts",
        ]
        for finding in result.findings:
            lines.append(
                f"- **{finding.severity.upper()}** [{finding.file}] {finding.location}: "
                f"**{finding.title}**. {finding.reason}"
            )
        return "\n".join(lines)

    return "\n".join(
        [
            "**Verdict:** No significant issues found",
            "",
            f"**Summary:** {result.summary}",
        ]
    )


def build_review_chunks_from_files(files: list[dict[str, Any]], max_chars: int) -> list[str]:
    sections: list[str] = []

    for file_info in files:
        filename = file_info.get("filename", "unknown")
        patch_text = file_info.get("patch") or ""
        if not patch_text:
            continue

        diff_section = f"diff --git a/{filename} b/{filename}\n+++ b/{filename}\n{patch_text}"
        for diff_chunk in split_diff_section_by_hunk(diff_section, max_chars):
            sections.append(f"File: {filename}\n\nDiff section:\n```diff\n{diff_chunk}\n```")

    return pack_review_sections(sections, max_chars)


def build_review_chunks_from_diff(
    diff_text: str,
    file_contexts: dict[str, ChangedFileContext],
) -> list[str]:
    settings = get_settings()
    section_budget = max(settings.review_chunk_chars // 2, 4000)
    sections = build_review_sections(diff_text, file_contexts, section_budget)
    return pack_review_sections(sections, settings.review_chunk_chars)


def repair_review_response(raw_content: str) -> str:
    settings = get_settings()
    response = get_openai_client().chat.completions.create(
        **build_review_request_params(
            REVIEW_REPAIR_SYSTEM_PROMPT,
            (
                "Rewrite the following output as strict JSON only.\n\n"
                "Source output:\n"
                f"{raw_content}"
            ),
            max_tokens=min(settings.review_max_output_tokens, 800),
        )
    )
    if not response.choices:
        raise ValueError("AI repair returned no choices")

    message = response.choices[0].message
    repaired = normalize_message_content(message.content)
    if not repaired and hasattr(message, "reasoning_content"):
        repaired = normalize_message_content(message.reasoning_content)
    if not repaired:
        raise ValueError("AI repair returned empty content")

    return repaired


def _call_openai_review(pr_title: str, pr_body: str | None, chunk_text: str, chunk_number: int, total_chunks: int) -> ReviewResult:
    settings = get_settings()
    response = get_openai_client().chat.completions.create(
        **build_review_request_params(
            REVIEW_SYSTEM_PROMPT,
            build_chunk_prompt(pr_title, pr_body, chunk_text, chunk_number, total_chunks),
            max_tokens=settings.review_max_output_tokens,
        )
    )
    if not response.choices:
        raise ValueError("AI returned no choices")

    message = response.choices[0].message
    content = normalize_message_content(message.content)
    if not content and hasattr(message, "reasoning_content"):
        content = normalize_message_content(message.reasoning_content)
    if not content:
        raise ValueError("AI returned empty content")

    logger.info("Parsed review chunk %s/%s response (%s chars)", chunk_number, total_chunks, len(content))
    try:
        return parse_review_response(content)
    except (ValueError, json.JSONDecodeError) as first_error:
        logger.warning(
            "Review chunk %s/%s returned non-parseable output; attempting JSON repair. Preview: %s",
            chunk_number,
            total_chunks,
            re.sub(r"\s+", " ", content).strip()[:240],
        )
        try:
            repaired_content = repair_review_response(content)
            return parse_review_response(repaired_content)
        except (ValueError, json.JSONDecodeError) as repair_error:
            raise ReviewResponseParseError(
                "Failed to parse review response after repair attempt",
                content,
                repair_attempted=True,
            ) from repair_error


async def run_review_chunks(pr_title: str, pr_body: str | None, chunks: list[str]) -> ReviewResult:
    if not chunks:
        return ReviewResult(
            verdict="no_significant_issues",
            summary="No significant issues found in the changed code.",
            findings=[],
        )

    results: list[ReviewResult] = []
    total_chunks = len(chunks)

    for index, chunk_text in enumerate(chunks, start=1):
        logger.info("Reviewing chunk %s/%s (%s chars)", index, total_chunks, len(chunk_text))
        result = await asyncio.to_thread(
            _call_openai_review,
            pr_title,
            pr_body,
            chunk_text,
            index,
            total_chunks,
        )
        results.append(result)

    return merge_review_results(results)
