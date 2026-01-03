import os
import json
import httpx
from google import genai  # <--- NEUER IMPORT
from fastapi import FastAPI, HTTPException, Request, Query
from pydantic import BaseModel
from dotenv import load_dotenv

# 1. Config & Setup
load_dotenv()

app = FastAPI(title="Smart Home AI")

# Konfiguration
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
WATCH_ENTITIES = os.getenv("WATCH_ENTITIES", "").split(",")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
AI_MODEL_NAME = os.getenv("AI_MODEL_NAME", "gemini-2.5-flash") # Auf 2.0 geändert für neues SDK

# NEU: Ein Secret Token für Alexa
ALEXA_ACCESS_TOKEN = os.getenv("ALEXA_ACCESS_TOKEN", "testAccessToken")

# Definition der Werkzeuge, die Gemini nutzen darf
tools_schema = [
    {
        "name": "control_device",
        "description": "Schaltet ein Smart Home Gerät an oder aus.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "entity_id": {
                    "type": "STRING",
                    "description": "Die ID des Geräts, z.B. light.wohnzimmer oder switch.steckdose"
                },
                "action": {
                    "type": "STRING",
                    "description": "Die Aktion: 'turn_on' oder 'turn_off'",
                    "enum": ["turn_on", "turn_off"]
                }
            },
            "required": ["entity_id", "action"]
        }
    }
]

# Google AI Client Setup (Neues SDK)
if not GOOGLE_API_KEY:
    print("WARNUNG: GOOGLE_API_KEY fehlt")
    client = None
else:
    # Der neue Client wird einmalig instanziiert
    client = genai.Client(api_key=GOOGLE_API_KEY)

# --- DTOs ---
class QueryRequest(BaseModel):
    query: str

class AIResponse(BaseModel):
    answer: str
    context_used: dict

# --- Helper Functions ---
async def fetch_ha_context():
    """Holt Daten von HA (wie gehabt)"""
    if not HA_URL or not HA_TOKEN:
        return {}

    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(f"{HA_URL}/api/states", headers=headers, timeout=5.0)
            if response.status_code != 200: return {}

            all_states = response.json()
            context_data = {}
            for entity in all_states:
                if entity['entity_id'] in WATCH_ENTITIES:
                    context_data[entity['entity_id']] = {
                        "state": entity['state'],
                        "name": entity['attributes'].get('friendly_name', entity['entity_id']),
                        "unit": entity['attributes'].get('unit_of_measurement', '')
                    }
                    if entity['entity_id'] == 'sun.sun':
                        context_data['sun.sun']['next_dawn'] = entity['attributes'].get('next_dawn')
            return context_data
        except Exception as e:
            print(f"HA Error: {e}")
            return {}

async def execute_ha_service(domain: str, service: str, entity_id: str):
    """
    Führt eine Aktion in HA aus.
    z.B. domain="light", service="turn_on", entity_id="light.wohnzimmer"
    """
    if not HA_URL or not HA_TOKEN:
        print("HA Config fehlt.")
        return False

    url = f"{HA_URL}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    payload = {"entity_id": entity_id}

    print(f"HA ACTION: {domain}.{service} -> {entity_id}")

    async with httpx.AsyncClient() as http_client:
        try:
            resp = await http_client.post(url, json=payload, headers=headers, timeout=5.0)
            return resp.status_code == 200
        except Exception as e:
            print(f"HA Action Error: {e}")
            return False

# --- Endpoints ---

@app.get("/health")
def health_check():
    return {"status": "alive", "sdk": "google-genai-v1"}

@app.post("/api/ask", response_model=AIResponse)
async def ask_assistant(request: QueryRequest):
    """Interner Endpunkt (z.B. für Bruno)"""
    context = await fetch_ha_context()

    system_prompt = f"""
    Du bist ein Smart Home Assistent.
    Daten: {json.dumps(context)}
    Frage: {request.query}
    Antworte kurz auf Deutsch.
    """

    try:
        # NEUE SDK SYNTAX
        response = client.models.generate_content(
            model=AI_MODEL_NAME,
            contents=system_prompt
        )
        return AIResponse(answer=response.text, context_used=context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Error: {str(e)}")

# --- ALEXA WEBHOOK MIT SECURITY ---
@app.post("/alexa-webhook")
async def handle_alexa(request: Request, token: str = Query(None)):
    """
    Der intelligente Webhook mit Function Calling (Tools).
    """
    # 1. Security Check
    current_token = ALEXA_ACCESS_TOKEN or "testAccessToken"
    if token != current_token:
        print(f"SECURITY ALERT: Invalid token {token}")
        raise HTTPException(status_code=403, detail="Invalid Access Token")

    try:
        # 2. Payload parsen
        payload = await request.json()
        # Debugging: Zeig uns im Log, was Alexa schickt
        # print(f"ALEXA PAYLOAD: {json.dumps(payload)}")

        req = payload.get('request', {})
        req_type = req.get('type')

        response_text = "Ich bin mir nicht sicher, was passiert ist."
        should_end = True

        # --- FALL 1: Session Start ("Öffne Haus") ---
        if req_type == 'LaunchRequest':
            response_text = "Hallo! Ich bin bereit. Was möchtest du tun?"
            should_end = False # Session offen lassen

        # --- FALL 2: Benutzer fragt oder befiehlt etwas ---
        elif req_type == 'IntentRequest':
            intent = req.get('intent', {})
            slots = intent.get('slots', {})
            user_query = None

            # Prüfen ob 'query' Slot existiert
            if 'query' in slots and 'value' in slots['query']:
                user_query = slots['query']['value']

            if user_query:
                print(f"USER INPUT: {user_query}")

                # Daten holen
                context = await fetch_ha_context()
                available_entities = list(context.keys())

                # Prompt bauen
                system_prompt = f"""
                Du bist ein Smart Home Assistent.
                Verfügbare Geräte-IDs (nutze NUR diese für das Tool): {json.dumps(available_entities)}
                Aktuelle Sensorwerte: {json.dumps(context)}
                
                Anweisung:
                1. Wenn der User etwas schalten will (Licht an/aus), NUTZE das Tool 'control_device'.
                2. Wenn der User eine Info will, antworte basierend auf den Sensorwerten.
                3. Antworte immer kurz auf Deutsch.
                
                User Input: "{user_query}"
                """

                try:
                    # Request an Gemini MIT Tools-Konfiguration
                    # Wir müssen die Funktions-Definitionen in ein "Tool"-Objekt verpacken
                    response = client.models.generate_content(
                        model=AI_MODEL_NAME,
                        contents=system_prompt,
                        config={
                            "tools": [{
                                "function_declarations": tools_schema
                            }]
                        }
                    )

                    # --- ENTSCHEIDUNG: TEXT ODER TOOL? ---
                    function_call = None

                    # Wir prüfen die Parts der Antwort (v2 SDK)
                    if response.candidates and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                function_call = part.function_call
                                break

                    if function_call:
                        # Gemini will schalten!
                        print(f"TOOL CALL DETECTED: {function_call.name}")

                        fname = function_call.name
                        args = function_call.args

                        if fname == "control_device":
                            entity = args.get("entity_id")
                            action = args.get("action")

                            # Domain extrahieren (z.B. light.x -> light)
                            if entity and "." in entity:
                                domain = entity.split('.')[0]

                                # Aktion ausführen
                                success = await execute_ha_service(domain, action, entity)

                                if success:
                                    response_text = f"Okay, erledigt. {entity} ist jetzt {action}."
                                else:
                                    response_text = f"Fehler: Ich konnte {entity} nicht erreichen."
                            else:
                                response_text = "Ich habe keine gültige Geräte-ID gefunden."
                    else:
                        # Gemini will nur reden (normale Antwort)
                        response_text = response.text if response.text else "Ich habe keine Antwort."

                except Exception as ai_error:
                    print(f"GEMINI ERROR: {ai_error}")
                    response_text = "Ich konnte mein Gehirn nicht erreichen. Fehler im KI-Modell."
            else:
                response_text = "Ich habe das nicht verstanden."

            should_end = True

        # --- FALL 3: Session Ende ---
        elif req_type == 'SessionEndedRequest':
            return {"version": "1.0", "response": {}}

        # --- ANTWORT BAUEN ---
        return {
            "version": "1.0",
            "response": {
                "outputSpeech": {
                    "type": "PlainText",
                    "text": response_text
                },
                "shouldEndSession": should_end
            }
        }

    except Exception as e:
        # Notfall-Catch
        print(f"CRITICAL EXCEPTION: {e}")
        return {
            "version": "1.0",
            "response": {
                "outputSpeech": {
                    "type": "PlainText",
                    "text": "Kritischer Systemfehler. Prüfe die Logs."
                },
                "shouldEndSession": True
            }
        }

if __name__ == "__main__":
    import uvicorn
    # Startet den Server auf Port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)