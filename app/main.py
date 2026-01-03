import os
import json
import httpx
import google.generativeai as genai
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# 1. Config & Setup
load_dotenv()

app = FastAPI(title="Smart Home AI")

# Konfiguration laden
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
WATCH_ENTITIES = os.getenv("WATCH_ENTITIES", "").split(",")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Google AI konfigurieren
if not GOOGLE_API_KEY:
    print("WARNUNG: GOOGLE_API_KEY fehlt in .env")
else:
    genai.configure(api_key=GOOGLE_API_KEY)
    # Flash ist schnell und günstig, perfekt für Realtime-Anfragen
    model = genai.GenerativeModel('gemini-2.5-flash')

# --- DTOs (Data Transfer Objects) ---
class QueryRequest(BaseModel):
    query: str

class AIResponse(BaseModel):
    answer: str
    context_used: dict  # Zum Debuggen: Was hat die AI gesehen?

# --- Helper Functions ---
async def fetch_ha_context():
    """
    Holt und bereinigt Daten von HA.
    Rückgabe: Dict mit Entity-IDs und Werten.
    """
    if not HA_URL or not HA_TOKEN:
        raise ValueError("HA Configuration missing")

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{HA_URL}/api/states", headers=headers, timeout=5.0)
            response.raise_for_status()
            all_states = response.json()

            # Filtern und Struktur vereinfachen (Tokens sparen + Lesbarkeit für AI)
            context_data = {}
            for entity in all_states:
                if entity['entity_id'] in WATCH_ENTITIES:
                    # Wir bauen ein vereinfachtes Objekt für die AI
                    context_data[entity['entity_id']] = {
                        "state": entity['state'],
                        "name": entity['attributes'].get('friendly_name', entity['entity_id']),
                        "unit": entity['attributes'].get('unit_of_measurement', '')
                    }

                    # Spezialfall: Attribute, die wichtig sein könnten (z.B. next_dawn bei sun)
                    if entity['entity_id'] == 'sun.sun':
                        context_data['sun.sun']['next_dawn'] = entity['attributes'].get('next_dawn')

            return context_data
        except Exception as e:
            print(f"Error fetching HA data: {e}")
            return {} # Fallback: Leerer Kontext ist besser als Crash

# --- Endpoints ---

@app.get("/health")
def health_check():
    return {"status": "alive"}

@app.get("/api/debug/ha-data")
async def debug_data():
    """Zeigt nur die Rohdaten an (ohne AI)"""
    return await fetch_ha_context()

@app.post("/api/ask", response_model=AIResponse)
async def ask_assistant(request: QueryRequest):
    """
    Der Haupt-Endpoint:
    1. Holt Live-Daten von HA
    2. Baut den Prompt
    3. Fragt Gemini
    """
    # 1. Daten holen
    context = await fetch_ha_context()

    # 2. System Prompt bauen (Hier steuern wir die Persönlichkeit)
    # Hinweis: Ich habe deine Präferenz (Europäisch/Direkt) hier eingebaut.
    system_prompt = f"""
    Du bist ein intelligenter Haus-Assistent. 
    Du hast Zugriff auf die aktuellen Sensordaten des Hauses (siehe JSON unten).
    
    Deine Persönlichkeit:
    - Stil: Europäisch, direkt, effizient. Kein "Geschleime".
    - Antworten: Kurz, kompakt, auf das Wesentliche reduziert.
    - Sprache: Deutsch (per Du).
    
    Aktuelle Sensordaten:
    {json.dumps(context, indent=2)}
    
    Frage des Bewohners: {request.query}
    """

    try:
        # 3. AI Abfragen
        response = await model.generate_content_async(system_prompt)
        return AIResponse(answer=response.text, context_used=context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)