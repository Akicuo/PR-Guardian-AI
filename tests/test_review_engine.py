import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.review_engine import (
    ReviewResponseParseError,
    build_context_snippets,
    extract_json_payload,
    merge_review_results,
    normalize_message_content,
    _call_openai_review,
    pack_review_sections,
    split_diff_section_by_hunk,
    split_diff_sections,
    ReviewFinding,
    ReviewResult,
)


class ReviewEngineTests(unittest.TestCase):
    def test_split_diff_sections_keeps_file_boundaries(self) -> None:
        diff_text = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )

        sections = split_diff_sections(diff_text)

        self.assertEqual(2, len(sections))
        self.assertIn("a.py", sections[0])
        self.assertIn("b.py", sections[1])

    def test_split_diff_section_by_hunk_never_cuts_mid_hunk(self) -> None:
        section = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-one\n"
            "+two\n"
            "@@ -10,2 +10,2 @@\n"
            "-three\n"
            "+four\n"
        )

        chunks = split_diff_section_by_hunk(section, 70)

        self.assertEqual(2, len(chunks))
        self.assertTrue(all("@@ " in chunk for chunk in chunks))

    def test_build_context_snippets_renders_line_numbers(self) -> None:
        file_content = "\n".join(f"line {index}" for index in range(1, 21))
        patch = "@@ -5,1 +5,2 @@\n-line 5\n+line five\n+line six\n"

        snippets = build_context_snippets(file_content, patch, context_lines=2)

        self.assertEqual(1, len(snippets))
        self.assertIn("Lines 3-8", snippets[0])
        self.assertIn("5: line 5", snippets[0])

    def test_pack_review_sections_respects_limit(self) -> None:
        sections = ["A" * 10, "B" * 10, "C" * 10]

        chunks = pack_review_sections(sections, 25)

        self.assertEqual(2, len(chunks))

    def test_extract_json_payload_handles_fenced_json(self) -> None:
        payload = extract_json_payload(
            "```json\n"
            '{"verdict":"bad","summary":"Found 1 issue.","findings":[]}\n'
            "```"
        )

        self.assertEqual("bad", payload["verdict"])

    def test_extract_json_payload_handles_prose_wrapped_json(self) -> None:
        payload = extract_json_payload(
            'Here is the review result:\n{"verdict":"no_significant_issues","summary":"No issues.","findings":[]}\nThanks.'
        )

        self.assertEqual("no_significant_issues", payload["verdict"])

    def test_normalize_message_content_handles_content_parts(self) -> None:
        content = [
            {"type": "text", "text": "First line"},
            SimpleNamespace(type="output_text", text="Second line"),
        ]

        normalized = normalize_message_content(content)

        self.assertEqual("First line\nSecond line", normalized)

    def test_call_openai_review_repairs_non_json_output(self) -> None:
        initial_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="I found one issue. Please see JSON below maybe.", reasoning_content=None)
                )
            ]
        )
        repaired_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"verdict":"bad","summary":"Found 1 issue.","findings":[]}',
                        reasoning_content=None,
                    )
                )
            ]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: initial_response)
            )
        )

        with patch("app.review_engine.get_openai_client", return_value=fake_client), patch(
            "app.review_engine.repair_review_response",
            return_value=repaired_response.choices[0].message.content,
        ) as repair_mock:
            result = _call_openai_review("Example PR", "", "File: app.py", 1, 1)

        self.assertEqual("bad", result.verdict)
        repair_mock.assert_called_once()

    def test_call_openai_review_raises_parse_error_after_failed_repair(self) -> None:
        initial_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Definitely not JSON", reasoning_content=None)
                )
            ]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: initial_response)
            )
        )

        with patch("app.review_engine.get_openai_client", return_value=fake_client), patch(
            "app.review_engine.repair_review_response",
            return_value="still not json",
        ):
            with self.assertRaises(ReviewResponseParseError) as error_context:
                _call_openai_review("Example PR", "", "File: app.py", 1, 1)

        self.assertTrue(error_context.exception.repair_attempted)
        self.assertIn("Definitely not JSON", error_context.exception.preview)

    def test_merge_review_results_deduplicates_findings(self) -> None:
        finding = ReviewFinding(
            severity="high",
            file="app/main.py",
            location="review_diff_with_ai",
            title="Example",
            reason="Example reason",
        )
        results = [
            ReviewResult(verdict="bad", summary="one", findings=[finding]),
            ReviewResult(verdict="bad", summary="two", findings=[finding]),
        ]

        merged = merge_review_results(results)

        self.assertEqual(1, len(merged.findings))
        self.assertEqual("bad", merged.verdict)


if __name__ == "__main__":
    unittest.main()
