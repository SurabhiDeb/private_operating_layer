from openai import OpenAI
from core.config import settings

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

def get_embedding(text: str) -> list[float]:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding