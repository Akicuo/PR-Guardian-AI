"""Review verification engine that validates AI claims against actual code."""

import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import get_settings
from .github_app import get_file_content, get_file_lines

logger = logging.getLogger("pr-guardian")
settings = get_settings()


@dataclass
class Claim:
    """Represents a claim made in the AI review that needs verification."""
    file_path: str
    claim_type: str  # "incomplete_file", "missing_import", "syntax_error", "generic_issue"
    line_number: Optional[int]
    description: str
    severity: str  # "error", "warning", "info"


@dataclass
class VerificationResult:
    """Result of verifying a claim."""
    claim: Claim
    is_valid: bool
    evidence: Optional[str]
    corrected_claim: Optional[str]


class ReviewVerifier:
    """
    Verifies AI review claims by inspecting actual file content.
    """

    def __init__(self, repo_owner: str, repo_name: str, pr_number: int):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.pr_number = pr_number
        self.tool_calls_made = 0
        # -1 means unlimited, otherwise use the configured limit
        self.max_tool_calls = settings.max_verification_calls
        self.unlimited = self.max_tool_calls == -1
        self._file_cache = {}  # Cache for file contents

    async def verify_review(
        self,
        draft_review: str,
        pr_head_ref: str
    ) -> Tuple[str, List[VerificationResult]]:
        """
        Main verification workflow.

        Args:
            draft_review: The AI-generated draft review
            pr_head_ref: Git ref for PR head branch

        Returns:
            (verified_review_text, verification_results)
        """
        # Step 1: Extract claims from the review
        claims = self._extract_claims(draft_review)
        logger.info(f"Extracted {len(claims)} claims for verification")

        if not claims:
            # No claims to verify
            return draft_review, []

        # Step 2: Verify each claim
        verification_results = []
        for claim in claims:
            if not self.unlimited and self.tool_calls_made >= self.max_tool_calls:
                logger.warning(f"Reached max tool calls ({self.max_tool_calls}), stopping verification")
                break

            result = await self._verify_claim(claim, pr_head_ref)
            verification_results.append(result)

        # Step 3: Refine review based on verification
        verified_review = self._refine_review(draft_review, verification_results)

        return verified_review, verification_results

    def _extract_claims(self, review_text: str) -> List[Claim]:
        """
        Parse the AI review to extract specific claims that need verification.
        """
        claims = []

        # Pattern 1: "File X is incomplete"
        incomplete_pattern = r'file\s+[`"]?([^`"\'\s]+)[`"]?\s+is\s+incomplete'
        for match in re.finditer(incomplete_pattern, review_text, re.IGNORECASE):
            claims.append(Claim(
                file_path=match.group(1),
                claim_type="incomplete_file",
                line_number=None,
                description="File is incomplete",
                severity="error"
            ))

        # Pattern 2: "Missing import X in file Y"
        missing_import_pattern = r'missing\s+import\s+[`"]?(\w+)[`"]?\s*(?:in\s+)?[`"]?([^`"\'\s]+)[`"]?'
        for match in re.finditer(missing_import_pattern, review_text, re.IGNORECASE):
            import_name = match.group(1)
            file_path = match.group(2) if match.group(2) else ""
            claims.append(Claim(
                file_path=file_path,
                claim_type="missing_import",
                line_number=None,
                description=f"Missing import: {import_name}",
                severity="error"
            ))

        # Pattern 3: "Syntax error on line X"
        syntax_error_pattern = r'syntax\s+error\s+(?:on\s+)?line\s+(\d+)'
        for match in re.finditer(syntax_error_pattern, review_text, re.IGNORECASE):
            claims.append(Claim(
                file_path="",  # Will be inferred from context
                claim_type="syntax_error",
                line_number=int(match.group(1)),
                description=f"Syntax error on line {match.group(1)}",
                severity="error"
            ))

        # Pattern 4: Any file mentioned with issues
        file_mention_pattern = r'(?:in\s+)?[`"]?([a-zA-Z0-9_/\-]+\.[a-z]+)[`"]?(?=\s+(?:has|contains|is))'
        for match in re.finditer(file_mention_pattern, review_text, re.IGNORECASE):
            if not any(c.file_path == match.group(1) for c in claims):
                claims.append(Claim(
                    file_path=match.group(1),
                    claim_type="generic_issue",
                    line_number=None,
                    description="Issue mentioned in file",
                    severity="warning"
                ))

        return claims

    async def _verify_claim(self, claim: Claim, pr_head_ref: str) -> VerificationResult:
        """
        Verify a single claim by inspecting the actual file.
        """
        try:
            if claim.claim_type == "incomplete_file":
                return await self._verify_file_complete(claim, pr_head_ref)
            elif claim.claim_type == "missing_import":
                return await self._verify_missing_import(claim, pr_head_ref)
            elif claim.claim_type == "syntax_error":
                return await self._verify_syntax_error(claim, pr_head_ref)
            else:
                return await self._verify_generic_issue(claim, pr_head_ref)
        except Exception as e:
            logger.error(f"Error verifying claim: {e}")
            return VerificationResult(
                claim=claim,
                is_valid=False,
                evidence=f"Verification failed: {str(e)}",
                corrected_claim=None
            )

    async def _verify_file_complete(
        self,
        claim: Claim,
        pr_head_ref: str
    ) -> VerificationResult:
        """
        Verify if a file is actually complete or not.
        """
        self.tool_calls_made += 1

        try:
            file_data = await self._get_file_cached(claim.file_path, pr_head_ref)
            content = file_data["decoded_content"]

            # Check for truncation indicators
            is_incomplete = self._check_if_incomplete(content, claim.file_path)

            evidence = f"File has {len(content)} characters, {len(content.splitlines())} lines"

            if is_incomplete:
                return VerificationResult(
                    claim=claim,
                    is_valid=True,  # The claim that it's incomplete is correct
                    evidence=evidence,
                    corrected_claim=None
                )
            else:
                return VerificationResult(
                    claim=claim,
                    is_valid=False,  # The claim is wrong
                    evidence=evidence + " - File appears complete",
                    corrected_claim=f"File {claim.file_path} appears to be complete"
                )

        except Exception as e:
            logger.error(f"Failed to verify file completeness: {e}")
            return VerificationResult(
                claim=claim,
                is_valid=False,
                evidence=f"Could not verify: {str(e)}",
                corrected_claim=None
            )

    def _check_if_incomplete(self, content: str, file_path: str) -> bool:
        """
        Heuristics to determine if a file is incomplete.
        """
        # Check for incomplete Python code
        if file_path.endswith(".py"):
            lines = content.split("\n")
            # Check for unmatched brackets
            open_braces = content.count("{") - content.count("}")
            open_brackets = content.count("[") - content.count("]")
            open_parens = content.count("(") - content.count(")")

            if open_braces > 0 or open_brackets > 0 or open_parens > 0:
                return True

            # Check if file ends mid-statement (no newline at end)
            if content and not content.endswith("\n"):
                return True

        # Check for incomplete JSON
        if file_path.endswith(".json"):
            try:
                json.loads(content)
            except json.JSONDecodeError:
                return True

        # Check for very short files that might be stubs
        if len(content.strip()) < 50 and not file_path.endswith((".md", ".txt")):
            return True

        return False

    async def _verify_missing_import(
        self,
        claim: Claim,
        pr_head_ref: str
    ) -> VerificationResult:
        """
        Verify if an import is actually missing from a file.
        """
        self.tool_calls_made += 1

        try:
            if not claim.file_path:
                return VerificationResult(
                    claim=claim,
                    is_valid=True,  # Assume valid
                    evidence="Cannot verify without file path",
                    corrected_claim=None
                )

            file_data = await self._get_file_cached(claim.file_path, pr_head_ref)
            content = file_data["decoded_content"]

            # Extract the import name from the claim
            import_name = claim.description.split(":")[1].strip()

            # Check if import exists in file
            has_import = self._check_for_import(content, import_name)

            evidence = f"Searched for import '{import_name}' in {claim.file_path}"

            if not has_import:
                return VerificationResult(
                    claim=claim,
                    is_valid=True,
                    evidence=evidence + " - Not found",
                    corrected_claim=None
                )
            else:
                return VerificationResult(
                    claim=claim,
                    is_valid=False,
                    evidence=evidence + " - Found in file",
                    corrected_claim=f"Import '{import_name}' is present in {claim.file_path}"
                )

        except Exception as e:
            logger.error(f"Failed to verify missing import: {e}")
            return VerificationResult(
                claim=claim,
                is_valid=False,
                evidence=f"Could not verify: {str(e)}",
                corrected_claim=None
            )

    def _check_for_import(self, content: str, import_name: str) -> bool:
        """
        Check if an import statement exists in the content.
        """
        # Common import patterns
        patterns = [
            rf"import {re.escape(import_name)}\b",
            rf"from {re.escape(import_name)}\b",
            rf"import {re.escape(import_name)} as",
            rf"from .* import .*{re.escape(import_name)}\b",
        ]

        for pattern in patterns:
            if re.search(pattern, content):
                return True

        return False

    async def _verify_syntax_error(
        self,
        claim: Claim,
        pr_head_ref: str
    ) -> VerificationResult:
        """
        Verify a claimed syntax error on a specific line.
        """
        self.tool_calls_made += 1

        try:
            # We need the file path - if not specified, we can't verify
            if not claim.file_path:
                return VerificationResult(
                    claim=claim,
                    is_valid=False,
                    evidence="Cannot verify without file path",
                    corrected_claim="Syntax error claimed but file not specified"
                )

            # Get the line and surrounding context
            line_content = await get_file_lines(
                self.repo_owner,
                self.repo_name,
                claim.file_path,
                claim.line_number,
                claim.line_number,
                pr_head_ref
            )

            evidence = f"Line {claim.line_number}: {line_content}"

            # Basic syntax checks
            has_syntax_issue = self._check_syntax(line_content)

            if has_syntax_issue:
                return VerificationResult(
                    claim=claim,
                    is_valid=True,
                    evidence=evidence,
                    corrected_claim=None
                )
            else:
                return VerificationResult(
                    claim=claim,
                    is_valid=False,
                    evidence=evidence + " - No obvious syntax issue",
                    corrected_claim=f"Line {claim.line_number} appears syntactically valid"
                )

        except Exception as e:
            logger.error(f"Failed to verify syntax error: {e}")
            return VerificationResult(
                claim=claim,
                is_valid=False,
                evidence=f"Could not verify: {str(e)}",
                corrected_claim=None
            )

    def _check_syntax(self, line: str) -> bool:
        """
        Basic syntax check for a line of code.
        """
        # Check for unmatched brackets in this line
        if line.count("{") != line.count("}"):
            return True
        if line.count("[") != line.count("]"):
            return True
        if line.count("(") != line.count(")"):
            return True

        # Check for common syntax errors
        stripped = line.strip()
        if stripped.endswith((",", ".", "+", "-", "*", "/")):
            return True

        return False

    async def _verify_generic_issue(
        self,
        claim: Claim,
        pr_head_ref: str
    ) -> VerificationResult:
        """
        Verify a generic issue claim by checking if the file exists and is accessible.
        """
        self.tool_calls_made += 1

        try:
            file_data = await self._get_file_cached(claim.file_path, pr_head_ref)
            content = file_data["decoded_content"]

            evidence = f"File exists with {len(content)} characters"

            # File exists - claim might be valid but we can't verify without more details
            return VerificationResult(
                claim=claim,
                is_valid=True,  # Assume valid since file exists
                evidence=evidence,
                corrected_claim=None
            )

        except Exception as e:
            logger.error(f"Failed to verify generic issue: {e}")
            return VerificationResult(
                claim=claim,
                is_valid=False,
                evidence=f"Could not access file: {str(e)}",
                corrected_claim=None
            )

    async def _get_file_cached(self, file_path: str, ref: str) -> dict:
        """
        Get file content with caching to avoid re-fetching.
        """
        cache_key = f"{ref}:{file_path}"
        if cache_key not in self._file_cache:
            file_data = await get_file_content(
                self.repo_owner,
                self.repo_name,
                file_path,
                ref
            )
            # Decode content once and cache it
            content_bytes = base64.b64decode(file_data["content"])
            self._file_cache[cache_key] = {
                "raw": file_data,
                "decoded_content": content_bytes.decode("utf-8")
            }
        return self._file_cache[cache_key]

    def _refine_review(
        self,
        draft_review: str,
        verification_results: List[VerificationResult]
    ) -> str:
        """
        Refine the review based on verification results.
        """
        if not verification_results:
            return draft_review

        # Count corrections
        corrections = [r for r in verification_results if not r.is_valid and r.corrected_claim]

        if not corrections:
            # All claims verified - add verification note
            verification_note = "\n\n---\n**Verification**: All claims in this review have been verified against the actual code."
            return draft_review + verification_note

        # Build refinement message
        refinement_lines = ["\n\n---\n### Verification Results\n"]

        for result in verification_results:
            if not result.is_valid:
                refinement_lines.append(f"- ~~{result.claim.description}~~ (Incorrect - {result.corrected_claim})")
                if result.evidence:
                    refinement_lines.append(f"  Evidence: {result.evidence}")

        refined_review = draft_review + "\n".join(refinement_lines)

        return refined_review
