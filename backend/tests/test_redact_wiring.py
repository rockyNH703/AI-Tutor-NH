"""Every model and embeddings provider the app builds masks secrets first (app/llm/redact.py)."""

from app.llm.base import ChatMessage, GenerationOptions
from app.llm.factory import build_llm
from app.llm.redact import RedactingEmbeddings, RedactingProvider
from app.rag.embeddings import build_embeddings, embedding_model_id
from tests.conftest import make_settings

SECRET_LINE = "snmp-server community FakeCommunity123 RO"
MASKED_LINE = "snmp-server community <redacted> RO"


async def test_the_built_model_masks_secrets_before_the_provider():
    llm = build_llm(make_settings())
    assert isinstance(llm, RedactingProvider)
    stream = await llm.stream([ChatMessage("user", SECRET_LINE)], GenerationOptions())
    async for _ in stream.parts:
        pass
    assert llm.calls[-1][-1].content == MASKED_LINE  # what the (fake) provider received


def test_the_built_embeddings_mask_secrets_before_the_provider():
    settings = make_settings()
    embeddings = build_embeddings(settings)
    assert isinstance(embeddings, RedactingEmbeddings)
    assert embedding_model_id(embeddings) == f"fake:{settings.embedding_model}"
    assert embeddings.embed_query(SECRET_LINE) == embeddings.embed_query(MASKED_LINE)
