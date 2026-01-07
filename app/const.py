from enum import Enum, auto
import os
from dotenv import load_dotenv

load_dotenv()


class Category(Enum):
    ADVICE = auto()
    LEAVE_HOME = auto()
    CONTROL = auto()
    INFO = auto()


tools_schema = [
    {
        "name": "control_device",
        "description": "Schaltet ein Smart Home Gerät an oder aus.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "entity_id": {
                    "type": "STRING",
                    "description": "Die ID des Geräts, z.B. light.wohnzimmer",
                },
                "action": {
                    "type": "STRING",
                    "description": "Die Aktion: 'turn_on' oder 'turn_off'",
                    "enum": ["turn_on", "turn_off"],
                },
            },
            "required": ["entity_id", "action"],
        },
    }
]


# Konfiguration
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
AI_MODEL_NAME = os.getenv("AI_MODEL_NAME", "gemini-2.5-flash-lite")
ALEXA_ACCESS_TOKEN = os.getenv("ALEXA_ACCESS_TOKEN", "testAccessToken")
