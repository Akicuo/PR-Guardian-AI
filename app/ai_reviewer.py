from .config import get_settings
from .review_engine import build_review_chunks_from_files, render_review_markdown, run_review_chunks


async def generate_ai_review(pr_title: str, pr_body: str, files: list) -> str:
    """
    Generate an AI review for a pull request.

    Args:
        pr_title: The pull request title
        pr_body: The pull request body/description
        files: List of files with patches

    Returns:
        The AI-generated review text
    """
    settings = get_settings()
    chunks = build_review_chunks_from_files(files, settings.review_chunk_chars)
    result = await run_review_chunks(pr_title, pr_body, chunks)
    return render_review_markdown(result)
