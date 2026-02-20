from openai import OpenAI

from .config import get_settings

settings = get_settings()
openai_client = OpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url
)


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
    text = f"PR Title: {pr_title}\nPR Body: {pr_body}\n\nFiles Changed:\n"
    for f in files[:5]:
        if f.get("patch"):
            text += f"\nFile: {f['filename']}\n{f['patch'][:2000]}\n"

    response = openai_client.chat.completions.create(
        model=settings.openai_model_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert senior code reviewer. "
                    "Provide a concise review focusing on:\n"
                    "- Potential bugs and security risks\n"
                    "- Performance issues\n"
                    "- Best practices and improvements\n"
                    "- If everything looks good, say that explicitly\n"
                    "Use Markdown with bullet points."
                )
            },
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=140000,
    )
    return response.choices[0].message.content.strip()


async def generate_draft_review(pr_title: str, pr_body: str, diff_text: str) -> str:
    """
    Generate a draft review that will be verified.

    Optimized for the verification workflow - encourages specific file paths
    and line numbers for all claims.

    Args:
        pr_title: The pull request title
        pr_body: The pull request body/description
        diff_text: The git diff text

    Returns:
        The AI-generated draft review text
    """
    import asyncio

    max_chars = 16000
    short_diff = diff_text[:max_chars]

    system_prompt = (
        "You are an expert senior code reviewer. "
        "Given a Git diff, you will provide a concise review:\n"
        "- Point out potential bugs, security risks, and performance issues.\n"
        "- Suggest improvements and best practices.\n"
        "- If you think a file is incomplete, clearly state which file and why.\n"
        "- If you notice missing imports, specify the file and import name.\n"
        "- If everything looks good, say that explicitly.\n"
        "- Answer in German (Swiss style german but not schweizerdeutsch) and use Markdown with bullet points.\n"
        "- IMPORTANT: Be specific about file paths and line numbers for all claims."
    )

    user_prompt = f"""
Pull Request Title: {pr_title}

Pull Request Description:
{pr_body or "(no description)"}

Git Diff:
{short_diff}
"""

    def _call_openai():
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

        resp = openai_client.chat.completions.create(**request_params)

        if not resp.choices:
            return "_Error: AI returned no response._"

        content = resp.choices[0].message.content
        if not content:
            # Last resort: check reasoning_content
            if hasattr(resp.choices[0].message, 'reasoning_content') and resp.choices[0].message.reasoning_content:
                content = resp.choices[0].message.reasoning_content
            else:
                return "_Error: AI returned empty content._"

        return content.strip()

    review_text = await asyncio.to_thread(_call_openai)
    return review_text
