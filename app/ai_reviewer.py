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
        max_tokens=700,
    )
    return response.choices[0].message.content.strip()
