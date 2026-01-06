import json
import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from dotenv import load_dotenv

from genai_client.client import get_client
from const import Category, ALEXA_ACCESS_TOKEN, HA_URL, HA_TOKEN
from category_handler.advice_handler import AdviceHandler
from category_handler.control_handler import ControlHandler
from category_handler.info_handler import InfoHandler

# ---------------------------------------------------------
# DAS STRATEGY MAPPING (Der "Router")
# Wir mappen Enum -> Klasse
# ---------------------------------------------------------
HANDLER_REGISTRY = {
    Category.ADVICE: AdviceHandler,
    Category.CONTROL: ControlHandler,
    Category.INFO: InfoHandler,
}
# 1. Config & Setup
load_dotenv()

print(f"HA_URL:", HA_URL)

app = FastAPI(title="Smart Home AI")


# --- DEFINITIONEN FÜR FILTERUNG & MAPPING ---

# 1. Energie-Sensoren: Mapping von "Sprechender Name" -> "Deine Entity ID"
ENERGY_MAPPING = {
    "netz_saldo_watt": "sensor.senec_grid_state_power",
    "pv_aktuell_watt": "sensor.senec_solar_generated_power",
    "pv_rest_prognose_kwh": "sensor.solar_energy_remaining_today",
    "batterie_haus_prozent": "sensor.senec_battery_charge_percent",
    "batterie_auto_prozent": "sensor.mgzsev_soc",
    "aktuelle-co2-prozent": "sensor.electricity_maps_anteil_fossiler_brennstoffe_im_netz",
    "niedrigste-co2-prozent": "sensor.strom_prognose_analyse",
    "niedrigste-co2-uhrzeit": "sensor.strom_prognose_analyse_timestamp",
    "waschkueche_power": "sensor.shelly_waschkueche_switch_0_power"
}

# --- HELPER FUNCTIONS ---

def filter_controllable_entities(all_states):
    """
    Filtert aus allen ~500 Entitäten die relevanten steuerbaren Geräte heraus.
    """
    targets = []

    # Erlaubte Domains
    allowed_domains = ["light", "cover", "climate", "switch", "vacuum"]

    # Blocklist (Rausfiltern von unnötigem Kram)
    blocklist = [
        "Internet Access", "Update", "Firmware", "Status", "sensor",
        "ChildLock", "Reboot", "Identifizieren", "Scene", "Schedule"
    ]

    for entity in all_states:
        eid = entity['entity_id']
        name = entity['attributes'].get('friendly_name', eid)
        domain = eid.split('.')[0]
        state = entity['state']

        # 1. Domain Check
        if domain not in allowed_domains:
            continue

        # 2. Blocklist Check
        if any(blocked in name for blocked in blocklist):
            continue

        # 3. Unavailable Check (optional, um Kontext klein zu halten)
        if state in ["unavailable", "unknown"]:
            continue

        # Format: "light.wohnzimmer (Wohnzimmer Decke): on"
        targets.append(f"{eid} ({name}): {state}")

    return targets

async def get_smart_home_context():
    """
    Holt ALLE Daten von HA und bereitet sie in zwei Kategorien auf:
    1. energy_context (für Logik)
    2. available_devices (für Tools)
    """
    if not HA_URL or not HA_TOKEN:
        return {}, []

    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as http_client:
        try:
            # Wir holen ALLES (/api/states) statt nur einzelne Entities
            response = await http_client.get(f"{HA_URL}/api/states", headers=headers, timeout=5.0)
            if response.status_code != 200:
                return {}, []

            all_states = response.json()

            # A) Energie-Kontext bauen (Mapping anwenden)
            energy_context = {}
            # Hilfs-Dict für schnellen Zugriff per ID
            state_map = {e['entity_id']: e['state'] for e in all_states}

            for key, entity_id in ENERGY_MAPPING.items():
                val = state_map.get(entity_id, "N/A")
                # Versuch, Zahlen direkt als Float zu speichern (hilft der KI beim Rechnen)
                try:
                    val = float(val)
                except:
                    pass
                energy_context[key] = val

            # B) Steuerbare Geräte filtern
            available_devices = filter_controllable_entities(all_states)

            return energy_context, available_devices

        except Exception as e:
            print(f"HA Error: {e}")
            return {}, []

# --- A. DER ROUTER (KLASSIFIZIERUNG) ---
async def classify_intent(query: str):
    """
    Entscheidet, was der User will. Kostet fast nichts und macht alles stabiler.
    """
    router_prompt = f"""
    Klassifiziere den User Input in genau eine Kategorie.
    
    Kategorien:
    1. "CONTROL" -> Der User will aktiv etwas schalten (Licht an, Rolladen hoch, Heizung aus).
    2. "ADVICE"  -> Der User fragt nach Energie-Entscheidungen (Waschmaschine jetzt? Auto laden?).
                 -> Sätze konnen z.B. mit "Ist gerade guter Zeitpunkt?" beginnen.
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
            config={"response_mime_type": "application/json"} # Erzwingt JSON
        )
        return json.loads(resp.text).get("intent")
    except:
        return "FOO" # Fallback


async def process_category(category: Category, user_query, energy_data, device_list):
    # 1. Die richtige Klasse aus dem Dictionary holen
    handler_class = HANDLER_REGISTRY.get(category)

    if not handler_class:
        raise ValueError(f"Kein Handler für {category} definiert!")

    # 2. Instanz erstellen (oder Singleton nutzen) und ausführen
    handler = handler_class()
    return await handler.execute(user_query, energy_data, device_list)

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
        req = payload.get('request', {})
        print(f"REQUEST: {req}")
        req_type = req.get('type')
        intent_name = req.get('intent', {}).get('name')
        response_text = "Fehler."
        should_end = True
        # 1. Die Konfiguration: Welcher Intent nutzt welchen Slot-Namen?
        intent_slot_map = {
            "CommandIntent": "command",     # z.B. "schalte..."
            "QuestionIntent": "question",   # z.B. "ob..."
            "StatementIntent": "statement", # z.B. "dass..."
            "RawInputIntent": "query"       # Dein alter Intent (Fallback)
        }


        if req_type == 'LaunchRequest':
            response_text = "Hallo! Ich bin bereit."
            should_end = False

        elif intent_name in intent_slot_map:  # <--- Doppelpunkt nicht vergessen!
            user_query = None # Besser als "Nichts", damit man später filtern kann

            # Sicherstellen, dass 'intent' und 'slots' überhaupt da sind
            if 'intent' in req and 'slots' in req['intent']:
                slots = req['intent']['slots']
                target_slot_name = intent_slot_map[intent_name]

                # Sicherer Zugriff: Erst den Slot holen, dann den Value prüfen
                current_slot = slots.get(target_slot_name)

                if current_slot and 'value' in current_slot:
                    user_query = current_slot['value']

            # Fallback, falls user_query leer blieb
            if not user_query:
                user_query = "Keine Eingabe erkannt"

            print(f"USER INPUT: {user_query}")

            # --- DATEN HOLEN (NEU) ---
            energy_data, device_list = await get_smart_home_context()
            print(f"EnergyData: {energy_data}")
            print(f"DeviceList: {device_list}")

            category = Category[await classify_intent(user_query)]
            print(f"Category: {category.name}")

            response_text = await process_category(category, user_query, energy_data, device_list)
            print(f"USER OUTPUT: {response_text}")

            should_end = True

        return {
            "version": "1.0",
            "response": {
                "outputSpeech": {"type": "PlainText", "text": response_text},
                "shouldEndSession": should_end
            }
        }

    except Exception as e:
        print(f"CRITICAL: {e}")
        return {"version": "1.0", "response": {"outputSpeech": {"type": "PlainText", "text": "Systemfehler."}}}

# --- SERVER START ---
if __name__ == "__main__":
    import uvicorn
    # Startet den Server auf Port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)