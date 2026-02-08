import json
import logging
from typing import List, Any, Dict
from category_handler.base import BaseHandler, HandlerResult
from genai_client.client import get_client
from const import tools_schema, Category

AI_MODEL_NAME = "gemini-flash-lite-latest"
logger = logging.getLogger(__name__)

class LeaveHomeHandler(BaseHandler):
    async def execute(self, parameters: List[Any], ha_service: Any, session_attributes: Dict[str, Any] = None, intent_name: str = None) -> HandlerResult:
        logger.info(f"LeaveHomeHandler aufgerufen. Intent: {intent_name}")
        
        session_attributes = session_attributes or {}
        state = session_attributes.get("state")

        # --- FOLLOW-UP: YES ---
        if intent_name == "AMAZON.YesIntent" and state == "AWAITING_LIGHTS_CONFIRMATION":
            lights_to_off = session_attributes.get("lights_to_turn_off", [])
            if not lights_to_off:
                return HandlerResult("Ich habe keine Lichter zum Ausschalten gefunden.", should_end_session=True)
            
            count = 0
            for eid in lights_to_off:
                dom = eid.split(".")[0]
                if await ha_service.execute_ha_service(dom, "turn_off", eid):
                    count += 1
            
            return HandlerResult(f"Alles klar, ich habe {count} Lichter ausgeschaltet. Tschüss!", should_end_session=True)

        # --- FOLLOW-UP: NO ---
        if intent_name == "AMAZON.NoIntent" and state == "AWAITING_LIGHTS_CONFIRMATION":
             return HandlerResult("Okay, ich lasse die Lichter an. Tschüss!", should_end_session=True)

        # --- INITIAL REQUEST (oder Fallback) ---
        try:
            smart_home_context = await ha_service.get_smart_home_context()
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Smart Home Context: {e}")
            return HandlerResult("Fehler beim Abrufen der Smart Home Daten.")

        def safe_float(value):
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0

        # 1. Aktive Lichter
        aktive_lichter = [
            {"eid": d["eid"], "area": d["area"], "state": d["state"]}
            for d in smart_home_context.get("controllable_devices", [])
            if d.get("device_class", "").startswith("light") and d.get("state") != "off"
        ]

        # 2. Offene Fenster/Türen
        fenster_tueren = [
            {"eid": d["eid"], "area": d["area"], "state": d["state"]}
            for d in smart_home_context.get("sensors", [])
            if d.get("area")
            and d.get("device_class") in ["window", "door"]
            and d.get("state") not in ["off", "closed"]
        ]

        # 3. Hoher Verbrauch
        hoher_verbrauch = [
            {"eid": d["eid"], "area": d["area"], "state": d["state"]}
            for d in smart_home_context.get("sensors", [])
            if d.get("area")
            and d.get("area") not in ["Wärmepumpe"]
            and d.get("device_class") == "power"
            and safe_float(d.get("state")) > 500
        ]

        # Logik für Lichter-Frage
        ask_about_lights = len(aktive_lichter) > 0
        
        system_prompt = f"""
            Du bist ein Smart Home Assistent. Der Nutzer verlässt das Haus.
            Fasse den folgenden Status kurz zusammen (max 30 Wörter).
        
            [AKTUELLER STATUS]
            - Offene Fenster/Türen: {json.dumps(fenster_tueren) if fenster_tueren else "Keine"}
            - Brennende Lichter: {json.dumps(aktive_lichter) if aktive_lichter else "Keine"}
            - Hoher Verbrauch (>500W): {json.dumps(hoher_verbrauch) if hoher_verbrauch else "Kein"}
        
            [REGELN]
            - Wenn alles "Keine/Kein" ist, sag nur: "Alles sicher, schönen Tag!"
            - Erwähne NUR die Dinge, die NICHT "Keine" sind.
            - { "FRAGE AM ENDE: 'Soll ich die Lichter ausschalten?'" if ask_about_lights else "Verabschiede Dich." }
        """

        try:
            client = get_client()
            response = client.models.generate_content(
                model=AI_MODEL_NAME,
                contents=system_prompt,
                config={"tools": [{"function_declarations": tools_schema}]},
            )
            response_text = response.text if response.text else "Keine Antwort."

        except Exception as e:
            logger.error(f"AI Error: {e}")
            response_text = "Fehler im KI-Modell."
            
        # Ergebnis bauen
        if ask_about_lights:
            return HandlerResult(
                text=response_text,
                should_end_session=False,
                session_attributes={
                    "category": Category.LEAVE_HOME.value, # String value needed for JSON serialization usually
                    "state": "AWAITING_LIGHTS_CONFIRMATION",
                    "lights_to_turn_off": [light["eid"] for light in aktive_lichter]
                }
            )
        else:
            return HandlerResult(text=response_text, should_end_session=True)
