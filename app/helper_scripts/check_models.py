# check_models.py
import os
from dotenv import load_dotenv

from genai.client import get_client

load_dotenv()

print("Verfügbare Modelle für generateContent:")
for m in get_client().models.list():
    if 'generateContent' in m.supported_actions:
        print(f"- {m.name}")