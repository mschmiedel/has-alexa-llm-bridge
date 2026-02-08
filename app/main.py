import json
import traceback

from fastapi import FastAPI, HTTPException, Request, Query
from dotenv import load_dotenv

from category_handler.leave_home_handler import LeaveHomeHandler
from genai_client.client import get_client
from const import Category, ALEXA_ACCESS_TOKEN, HA_URL
from category_handler.advice_handler import AdviceHandler
from category_handler.control_handler import ControlHandler
from category_handler.info_handler import InfoHandler
from category_handler.base import HandlerResult
from ha_service.main import HaService

# ---------------------------------------------------------
# DAS STRATEGY MAPPING (Der "Router")
# Wir mappen Enum -> Klasse
# ---------------------------------------------------------
HANDLER_REGISTRY = {
    Category.LEAVE_HOME: LeaveHomeHandler,
    Category.ADVICE: AdviceHandler,
    Category.CONTROL: ControlHandler,
    Category.INFO: InfoHandler,
}
# 1. Config & Setup
load_dotenv()

print(f"HA_URL: {HA_URL}")

app = FastAPI(title="Smart Home AI")


# --- A. DER ROUTER (KLASSIFIZIERUNG) ---
async def classify_intent(query: str):
    router_prompt = f"""
    Klassifiziere den User Input in genau eine Kategorie.
    
    Kategorien:
    1. "CONTROL" -> Der User will aktiv etwas schalten (Licht an, Rolladen hoch, Heizung aus).
    2. "ADVICE"  -> Der User fragt nach Energie-Entscheidungen (Waschmaschine jetzt? Auto laden?).
    3. "INFO"    -> Der User will nur Statuswerte wissen (Wie warm ist es? Wieviel Strom verbrauchen wir? Ist Licht im Wohnzimmer an?).
                 -> Beispiele: 
                     - Wie warm ist es?
                     - Wieviel Strom verbrauchen wir?
                     - Ich möchte das Haus verlassen, was muss ich beachten?
    
    Antworte NUR mit dem JSON: {{"intent": "KATEGORIE"}}
    
    Input: "{query}"
    """
    try:
        resp = get_client().models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=router_prompt,
            config={"response_mime_type": "application/json"},  # Erzwingt JSON
        )
        return json.loads(resp.text).get("intent")
    except Exception:
        traceback.print_exc()
        return "FOO"  # Fallback


async def process_category(category: Category, parameters, ha_service: HaService, session_attributes=None, intent_name=None):
    # 1. Die richtige Klasse aus dem Dictionary holen
    handler_class = HANDLER_REGISTRY.get(category)

    if not handler_class:
        raise ValueError(f"Kein Handler für {category} definiert!")

    # 2. Instanz erstellen (oder Singleton nutzen) und ausführen
    handler = handler_class()
    return await handler.execute(parameters, ha_service, session_attributes, intent_name)


@app.get("/health")
def health_check():
    return {"status": "alive", "sdk": "google-genai-v1"}


@app.post("/alexa-webhook")
async def handle_alexa(request: Request, token: str = Query(None)):
    # 1. Security
    current_token = ALEXA_ACCESS_TOKEN
    if token != current_token:
        raise HTTPException(status_code=403, detail="Invalid Token")

    try:
        payload = await request.json()
        req = payload.get("request", {})
        session = payload.get("session", {})
        session_attributes = session.get("attributes", {}) or {}
        
        print(f"REQUEST: {req}")
        req_type = req.get("type")
        intent_name = req.get("intent", {}).get("name")
        
        response_text = "Fehler."
        should_end = True
        new_session_attributes = {}

        # 1. Die Konfiguration: Welcher Intent nutzt welchen Slot-Namen?
        intent_slot_map = {
            "LeaveHomeIntent": {"category": Category.LEAVE_HOME, "parameters": []},
            "EnergyAdviceIntent": {
                "category": Category.ADVICE,
                "parameters": ["device"],
            },
            "StatusInfoIntent": {"category": Category.INFO, "parameters": ["subject"]},
            "SmartControlIntent": {
                "category": Category.CONTROL,
                "parameters": ["device", "action"],
            },
        }

        if req_type == "LaunchRequest":
            response_text = "Hallo! Ich bin bereit."
            should_end = False

        elif intent_name in ["AMAZON.StopIntent", "AMAZON.CancelIntent"]:
            response_text = "Tschüss!"
            should_end = True

        elif intent_name == "AMAZON.HelpIntent":
            response_text = """
                Um Tips beim Verlassen des Hauses zu bekommen kannst Du sagen: 
                    Ich/wir verlasse das Haus
                    Ich/wir gehen jetzt
                    Ich/wir gehen raus
                Um Tips zur Nutzung von Geräten zu bekommen kannst Du sagen:
                    Lohnt sich Auto laden?
                    Wann soll ich Waschmaschine anmachen?
            """
            should_end =  False

        elif intent_name == "AMAZON.FallbackIntent":
            response_text = "Das habe ich leider nicht verstanden."
            should_end = False

        else:
            category = None
            parameters = []

            # A. Check Context for Follow-Up (Yes/No)
            if intent_name in ["AMAZON.YesIntent", "AMAZON.NoIntent"]:
                cat_val = session_attributes.get("category")
                if cat_val:
                    try:
                        category = Category(cat_val)
                    except ValueError:
                        print(f"Warning: Invalid category in session: {cat_val}")
            
            # B. Standard Intent Mapping
            if not category and intent_name in intent_slot_map:
                category = intent_slot_map[intent_name]["category"]
                if req["intent"].get("slots", {}):
                    for parameterName in intent_slot_map[intent_name]["parameters"]:
                        if parameterName in req["intent"]["slots"]:
                             val = req["intent"]["slots"][parameterName].get("value")
                             if val:
                                 parameters.append(val)

            # C. Execute
            if category:
                print(f"USER INPUT: {category.name}: {parameters} | Intent: {intent_name}")

                # --- SERVICE INSTANZIIEREN ---
                ha_service = HaService()

                result = await process_category(
                    category, parameters, ha_service, session_attributes, intent_name
                )
                
                # Unwrap HandlerResult
                if isinstance(result, HandlerResult):
                    response_text = result.text
                    should_end = result.should_end_session
                    new_session_attributes = result.session_attributes
                else:
                    # Fallback old style
                    response_text = str(result)
                    should_end = True

                print(f"USER OUTPUT: {response_text}")

            else:
                response_text = "Ich habe Dich nicht verstanden."
                should_end = True

        return {
            "version": "1.0",
            "sessionAttributes": new_session_attributes,
            "response": {
                "outputSpeech": {"type": "PlainText", "text": response_text},
                "shouldEndSession": should_end,
            },
        }

    except Exception as e:
        print(f"CRITICAL: {e}")
        traceback.print_exc()
    return {
        "version": "1.0",
        "response": {"outputSpeech": {"type": "PlainText", "text": "Systemfehler."}},
    }


# --- SERVER START ---
if __name__ == "__main__":
    import uvicorn

    # Startet den Server auf Port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
