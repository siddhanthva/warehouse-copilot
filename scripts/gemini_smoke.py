"""Level 0 check: one Gemini call through Vertex AI. Needs `gcloud auth application-default login`."""
from warehouse_copilot.agent.llm import make_llm
from warehouse_copilot.config import get_settings

s = get_settings()
reply = make_llm().invoke("Reply with one short sentence: what is Medicare?")
print(f"[{s.gemini_model} @ {s.google_cloud_location}] {reply.text}")
