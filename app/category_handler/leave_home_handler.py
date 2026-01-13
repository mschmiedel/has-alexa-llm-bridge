import json

from category_handler.base import BaseHandler

from genai_client.client import get_client

from const import AI_MODEL_NAME, tools_schema


class LeaveHomeHandler(BaseHandler):
    async def execute(self, parameters, smart_home_context):
        global response_text
        print("LeaveHomeHandler aufgerufen.")

        # Hilfsfunktion für sichere Float-Umwandlung (verhindert Crash bei "unavailable" etc.)
        def safe_float(value):
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0

        # 1. Aktive Lichter (Nur eid, area, state)
        aktive_lichter = [
            {"eid": d["eid"], "area": d["area"], "state": d["state"]}
            for d in smart_home_context.get("controllable_devices", [])
            if d.get("device_class", "").startswith("light") and d.get("state") != "off"
        ]

        # 2. Offene Fenster/Türen (Nur eid, area, state)
        # Hinweis: binary_sensor ist oft "on" statt "open", Logik prüft hier auf "nicht off"
        fenster_tueren = [
            {"eid": d["eid"], "area": d["area"], "state": d["state"]}
            for d in smart_home_context.get("sensors", [])
            if d.get("area")
            and d.get("device_class") in ["window", "door"]
            and d.get("state") not in ["off", "closed"]
        ]

        # 3. Hoher Verbrauch (Nur eid, area, state)
        hoher_verbrauch = [
            {"eid": d["eid"], "area": d["area"], "state": d["state"]}
            for d in smart_home_context.get("sensors", [])
            if d.get("area")
            and d.get("area") not in ["Wärmepumpe"]  # Ausschlussliste
            and d.get("device_class") == "power"
            and safe_float(d.get("state")) > 500
        ]

        # --- Debug Ausgabe ---
        print(f"Lights (Minified): {json.dumps(aktive_lichter, indent=2)}")
        print(f"Sensors (Minified): {json.dumps(fenster_tueren, indent=2)}")
        print(f"High Power (Minified): {json.dumps(hoher_verbrauch, indent=2)}")
        system_prompt = f"""
            Du bist ein Smart Home Assistent. Der Nutzer verlässt das Haus.
            Fasse den folgenden Status kurz zusammen (max 30 Wörter).
        
            [AKTUELLER STATUS]
            - Offene Fenster/Türen: {json.dumps(fenster_tueren) if fenster_tueren else "Keine"}
            - Brennende Lichter: {json.dumps(aktive_lichter) if aktive_lichter else "Keine"}
            - Hoher Verbrauch (>500W): {json.dumps(hoher_verbrauch) if hoher_verbrauch else "Kein"}
        
            [REGELN]
            - Wenn alles "Keine/Kein" ist, sag nur: "Alles sicher, schönen Tag!"
            - Erwähne NUR die Dinge, die NICHT "Keine" sind, je einen Satz für jede Liste.
            - In jedem Satz, erwähne jeden betroffenen Bereich (area), z.b.
              * In der Diele, Treppe und Küche brennen Lichter, ein Gerät in Waschküche hat einen hohen Verbrauch"
        """
        # --- PROMPT BAUEN ---

        try:
            response = get_client().models.generate_content(
                model=AI_MODEL_NAME,
                contents=system_prompt,
                config={"tools": [{"function_declarations": tools_schema}]},
            )

            response_text = response.text if response.text else "Keine Antwort."

        except Exception as e:
            print(f"AI Error: {e}")
            response_text = "Fehler im KI-Modell."

        return response_text
