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
    Webhook für Alexa.
    Sichert ab, dass nur Requests mit korrektem ?token=... Parameter durchkommen.
    """

    # 1. Security Check
    # Wir prüfen, ob das Token in der URL mit unserer ENV übereinstimmt.
    if not ALEXA_ACCESS_TOKEN:
        print("ACHTUNG: ALEXA_ACCESS_TOKEN nicht gesetzt! Webhook ist unsicher.")
    elif token != ALEXA_ACCESS_TOKEN:
        print(f"Unauthorized Access Attempt with token: {token}")
        # Wir werfen 403 Forbidden
        raise HTTPException(status_code=403, detail="Invalid Access Token")

    # 2. Request Verarbeitung
    payload = await request.json()

    # ... Hier kommt später die echte Logik rein ...
    # Aktuell nur Echo

    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {
                "type": "PlainText",
                "text": "Verbindung sicher und SDK aktualisiert."
            },
            "shouldEndSession": True
        }
    }

if __name__ == "__main__":
    import uvicorn
    # Startet den Server auf Port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)