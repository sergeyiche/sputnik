"""Embeddings provider factory."""

from __future__ import annotations

import os

from langchain_core.embeddings import Embeddings

from packages.integrations.gigachat.auth import get_gigachat_auth_service
from packages.integrations.gigachat.langchain_config import (
    get_gigachat_ca_bundle_file,
    get_gigachat_verify_ssl_certs,
)


class PrefixedE5Embeddings(Embeddings):
    """multilingual-e5_* expects ``query:`` / ``passage:`` prefixes for best retrieval."""

    def __init__(self, base: Embeddings) -> None:
        self._base = base

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._base.embed_documents([f"passage: {text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._base.embed_query(f"query: {text}")


def create_embeddings(*, access_token: str | None = None) -> Embeddings:
    provider = os.getenv("EMBEDDINGS_PROVIDER", "gigachat").lower()

    if provider == "gigachat":
        from langchain_gigachat import GigaChatEmbeddings

        auth = get_gigachat_auth_service()
        common = {
            "scope": os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            "verify_ssl_certs": get_gigachat_verify_ssl_certs(),
            "ca_bundle_file": get_gigachat_ca_bundle_file(),
            "base_url": os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
            "auth_url": os.getenv(
                "GIGACHAT_OAUTH_URL",
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            ),
            "timeout": float(os.getenv("GIGACHAT_TIMEOUT", "60")),
        }

        if access_token is not None:
            return GigaChatEmbeddings(access_token=access_token, **common)

        return GigaChatEmbeddings(credentials=auth.sdk_credentials(), **common)

    if provider == "local":
        from langchain_huggingface import HuggingFaceEmbeddings

        model = os.getenv("LOCAL_EMBEDDINGS_MODEL", "intfloat/multilingual-e5-small")
        base = HuggingFaceEmbeddings(
            model_name=model,
            encode_kwargs={"normalize_embeddings": True},
        )
        # e5-family models need asymmetric prefixes
        if "e5" in model.lower():
            return PrefixedE5Embeddings(base)
        return base

    raise ValueError(f"Unknown EMBEDDINGS_PROVIDER: {provider}. Supported: gigachat, local")
