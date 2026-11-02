"""Gemini on Vertex AI.

ChatVertexAI (langchain-google-vertexai) is deprecated since 3.2.0 and will be removed
in 4.0. The supported path is ChatGoogleGenerativeAI with vertexai=True, which uses
Application Default Credentials locally (gcloud auth application-default login), the
Workload Identity Federation token in CI and the service account on Cloud Run. Using
Vertex (not AI Studio) is what makes the GCP free-trial credit apply.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from warehouse_copilot.config import get_settings


def make_llm(model: str | None = None) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    s = get_settings()
    if not s.google_cloud_project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
    return ChatGoogleGenerativeAI(
        model=model or s.gemini_model,
        vertexai=True,
        project=s.google_cloud_project,
        location=s.google_cloud_location,
        thinking_level=s.thinking_level,
        max_retries=2,
    )
