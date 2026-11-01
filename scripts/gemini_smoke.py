"""Level 0 smoke test: one Gemini call on Vertex AI using ADC and .env settings."""
import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()
llm = ChatGoogleGenerativeAI(
    model=os.environ["GEMINI_MODEL"],
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
)
print(llm.invoke("Reply with one short sentence confirming you are reachable.").content)
