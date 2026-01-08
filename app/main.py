import asyncio
import json
import traceback
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from dotenv import load_dotenv

from category_handler.leave_home_handler import LeaveHomeHandler
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
    Category.LEAVE_HOME: LeaveHomeHandler,
    Category.ADVICE: AdviceHandler,
    Category.CONTROL: ControlHandler,
    Category.INFO: InfoHandler,
}
# 1. Config & Setup
load_dotenv()

print(f"HA_URL: {HA_URL}")

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
    "waschkueche_power": "sensor.shelly_waschkueche_switch_0_power",
}

HISTORY_MAPPING = {
    "Wallbox": "sensor.senec_webapi_v3_wallbox_consumption_total",
    "Akku_Geladen": "sensor.senec_webapi_v3_accuexport_total",
    "Akku_Entladen": "sensor.senec_webapi_v3_accuimport_total",
    "PV_Erzeugung_Gesamt": "sensor.senec_webapi_v3_powergenerated_total",
    "Waschkueche_Gesamt": "sensor.shelly_waschkueche_switch_0_energy",
    "Waermepumpe_Gesamt": "sensor.shelly_ac_em1_total_active_energy",
    "Hausverbrauch_Gesamt": "sensor.senec_webapi_v3_consumption_total",
}

# --- HELPER FUNCTIONS ---

def filter_entities(all_states, allowed_domains, blocklist):
    """
    Filtert aus allen ~500 Entitäten die relevanten steuerbaren Geräte heraus.
    """
    targets = []

    for entity in all_states:
        eid = entity["entity_id"]
        name = entity["attributes"].get("friendly_name", eid)
        device_class = entity["attributes"].get("device_class", eid)
        domain = eid.split(".")[0]
        area = entity["area"]
        state = entity["state"]

        # 1. Domain Check
        if domain not in allowed_domains:
            continue

        # 2. Blocklist Check
        if any(blocked in name for blocked in blocklist):
            continue

        # 3. Unavailable Check (optional, um Kontext klein zu halten)
        if state in ["unavailable", "unknown"]:
            continue

        targets.append(
            {
                "eid": eid,
                "name": name,
                "area": area,
                "state": f"{state}",
                "device_class": f"{device_class}",
            }
        )

    return targets


async def get_areas(headers):
    async with httpx.AsyncClient() as http_client_areas:
        headers["Content-Type"] = "application/json"
        body = {
            "template": "{% set ns = namespace(items=[]) %}{% for s in states %}{% set area = area_name(s.entity_id) %}{% if area %}{% set ns.items = ns.items + [(s.entity_id, area)] %}{% endif %}{% endfor %}{{ dict(ns.items) | to_json }}"
        }
        try:
            response = await http_client_areas.post(
                f"{HA_URL}/api/template", headers=headers, json=body, timeout=5.0
            )
            if response.status_code != 200:
                return {}
            return response.json()
        except Exception as e:
            print(f"HA Error: {e}")
            return {}


async def fetch_history_point(client, entity_id, timestamp, headers):
    """
    Holt den Status einer Entity zu einem exakten Zeitpunkt in der Vergangenheit.
    """
    try:
        ts_str = timestamp.isoformat()
        # Kleines Zeitfenster definieren (1 Sekunde reicht)
        end_str = (timestamp + timedelta(seconds=1)).isoformat()

        url = f"{HA_URL}/api/history/period/{ts_str}"
        params = {
            "filter_entity_id": entity_id,
            "end_time": end_str,
            "minimal_response": "true"
        }

        response = await client.get(url, headers=headers, params=params, timeout=5.0)

        if response.status_code == 200:
            data = response.json()
            # Struktur ist [[{state...}]]. Wir nehmen den ersten Eintrag.
            if data and len(data) > 0 and len(data[0]) > 0:
                val = data[0][0].get("state")
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None
        return None
    except Exception:
        return None

async def get_smart_home_context():
    """
    Holt ALLE Daten von HA und bereitet sie auf.
    """
    if not HA_URL or not HA_TOKEN:
        return {"energy_context": {}, "energy_history": {}, "controllable_devices": [], "sensors": []}

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }

    area_task = asyncio.create_task(get_areas(headers))

    async with httpx.AsyncClient() as http_client:
        try:
            # 1. Aktuelle States holen (für Live Context & aktuelle Zählerstände)
            response = await http_client.get(
                f"{HA_URL}/api/states", headers=headers, timeout=5.0
            )

            controllable_devices = []
            sensors = []
            energy_context = {}
            energy_history = {}
            state_map = {} # Cache für schnellen Zugriff

            if response.status_code == 200:
                all_states = response.json()
                area_data = await area_task

                # State Map aufbauen
                for state in all_states:
                    entity_id = state["entity_id"]
                    state["area"] = area_data.get(entity_id)

                    try:
                        val = float(state["state"])
                    except Exception:
                        val = state["state"]
                    state_map[entity_id] = val

                controllable_devices = filter_entities(
                    all_states, ["light", "cover", "climate", "switch", "vacuum"],
                    ["Internet Access", "Update", "Firmware", "Status", "sensor", "ChildLock", "Reboot", "Identifizieren", "Scene", "Schedule", "quality", "rssi", "overheat", "overpower"]
                )
                sensors = filter_entities(
                    all_states, ["sensor", "binary_sensor"],
                    ["Internet Access", "Update", "Firmware", "Status", "ChildLock", "Reboot", "Identifizieren", "Scene", "Schedule", "quality", "rssi", "overheat", "overpower"]
                )

                # --- 2. ENERGY CONTEXT (LIVE) ---
                for key, entity_id in ENERGY_MAPPING.items():
                    val = state_map.get(entity_id, "N/A")
                    energy_context[key] = val

                # --- 3. ENERGY HISTORY (Vergangenheit) ---
                now = datetime.now()
                history_tasks = []
                task_map = []

                # Wir iterieren über das HISTORY MAPPING
                for key, entity_id in HISTORY_MAPPING.items():
                    # 7 Tage zurück
                    for day in range(1, 8):
                        ts = now - timedelta(days=day)
                        history_tasks.append(fetch_history_point(http_client, entity_id, ts, headers))
                        task_map.append((key, day))

                # Alle History-Calls parallel abfeuern
                if history_tasks:
                    history_results = await asyncio.gather(*history_tasks)

                    # Temporäre Struktur für Rohdaten
                    raw_history = {k: [] for k in HISTORY_MAPPING.keys()}

                    # Ergebnisse einsortieren
                    for i, res in enumerate(history_results):
                        key, day = task_map[i]
                        raw_history[key].append(res)

                    # Differenzen berechnen
                    for key, past_vals in raw_history.items():
                        entity_id = HISTORY_MAPPING[key]

                        # Aktueller Zählerstand als Startpunkt
                        current_total = state_map.get(entity_id)

                        # Fallback, falls aktueller Wert fehlt
                        if not isinstance(current_total, (int, float)):
                            energy_history[key] = []
                            continue

                        diffs = []
                        last_val = current_total

                        # past_vals ist [Wert_Gestern, Wert_Vorgestern...]
                        for val_past in past_vals:
                            if last_val is not None and val_past is not None:
                                # Verbrauch = Wert(Neu) - Wert(Alt)
                                diff = last_val - val_past
                                # Negative Diffs abfangen (z.B. Zählertausch), sonst runden
                                diffs.append(round(max(0.0, diff), 2))
                            else:
                                diffs.append(None)

                            # Referenz für nächsten Tag verschieben
                            last_val = val_past

                        energy_history[key] = diffs

            return {
                "energy_context": energy_context,
                "energy_history": energy_history,
                "controllable_devices": controllable_devices,
                "sensors": sensors,
            }
        except Exception as e:
            print(f"HA Error: {e}")
            traceback.print_exc()
            return {"energy_context": {}, "energy_history": {}, "controllable_devices": [], "sensors": []}

# --- A. DER ROUTER (KLASSIFIZIERUNG) ---
async def classify_intent(query: str):
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
            config={"response_mime_type": "application/json"},  # Erzwingt JSON
        )
        return json.loads(resp.text).get("intent")
    except Exception:
        traceback.print_exc()
        return "FOO"  # Fallback


async def process_category(category: Category, parameters, smart_home_context):
    # 1. Die richtige Klasse aus dem Dictionary holen
    handler_class = HANDLER_REGISTRY.get(category)

    if not handler_class:
        raise ValueError(f"Kein Handler für {category} definiert!")

    # 2. Instanz erstellen (oder Singleton nutzen) und ausführen
    handler = handler_class()
    return await handler.execute(parameters, smart_home_context)


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
        print(f"REQUEST: {req}")
        req_type = req.get("type")
        intent_name = req.get("intent", {}).get("name")
        response_text = "Fehler."
        should_end = True
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

        elif intent_name == "AMAZON.StopIntent" or intent_name == "AMAZON.CancelIntent":
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

        elif intent_name in intent_slot_map:  # <--- Doppelpunkt nicht vergessen!
            parameters = []
            category = None

            # Sicherstellen, dass 'intent' und 'slots' überhaupt da sind
            if "intent" in req:
                category = intent_slot_map[intent_name]["category"]
                if req["intent"].get("slots", {}):
                    for parameterName in intent_slot_map[intent_name]["parameters"]:
                        if parameterName in req["intent"]["slots"]:
                            parameters.append(
                                req["intent"]["slots"][parameterName]["value"]
                            )

            # Fallback, falls user_query leer blieb
            if category:
                print(f"USER INPUT: {category.name}: {parameters} ")

                # --- DATEN HOLEN (NEU) ---
                smart_home_context = await get_smart_home_context()
                print(f"EnergyData: {smart_home_context['energy_context']}")
                print(f"EnergyData: {smart_home_context['energy_history']}")
                # print(f"DeviceList: {smart_home_context['controllable_devices']}")
                # print(f"Sensors: {smart_home_context['sensors']}")

                response_text = await process_category(
                    category, parameters, smart_home_context
                )
                print(f"USER OUTPUT: {response_text}")

                should_end = True

            else:
                response_text = "Ich habe Dich nicht verstanden."
                should_end = True

        return {
            "version": "1.0",
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
