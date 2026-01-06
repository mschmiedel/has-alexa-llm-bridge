import json

from category_handler.base import BaseHandler

from genai_client.client import get_client
from ha_service.main import execute_ha_service

from const import AI_MODEL_NAME, tools_schema


class AdviceHandler(BaseHandler):
    async def execute(self, parameters, smart_home_context):
        global response_text
        print(f"AdviceHandler aufgerufen.")

        print(f"Energie-Werte: {json.dumps(smart_home_context["energy_context"])}")
        system_prompt = f"""
            Du bist ein Energieberater aus einem Smart Home.
            
            [KONTEXT]
            Energie-Werte: {json.dumps(smart_home_context["energy_context"])}
            
            [ENTSCHEIDUNGS-LOGIK]
            Der User will Beratung über den Zeitpunkt, wann er das genannte Gerät nutzen sollte.
            
            BERATUNG / FRAGE ("Soll ich", "Ist jetzt guter Zeitpunkt")
            Antworte nur mit Text basierend auf diesen Regeln:
                    - Formulierung: kurzer Satz bis höchstens 30 Wörter, wenn möglich konkrete Sensorwerte mit eintragen, die zur Entscheidung geführt haben.
                    - Annahmen / Rahmenbedingungen:    
                        'netz_saldo_watt' Positiv bedeutet Netzbezug, negativ PV-Einspeisung.
                        'sensor.senec_house_power' ist der Hausverbrauch
                        'sensor.senec_wallbox_1_power' ist der Wallbox Verbrauch, nicht im Hausverbrauch enthalten.
                        'sensor.shelly_ac_em1_power' ist der Verbrauch der Wärmepumpe, ist im Hausverbrauch enthalten.
                        'aktuelle-co2-prozent': aktuelle CO2 Prozent im Strommix, unter 30% ist sauber. 
                        'niedrigste-co2-prozent': niedrigster co2 prozentsatz im Strommix in den nächsten Stunden
                        'niedrigste-co2-uhrzeit': uhrzeit, wann strom am saubersten sein wird. 
                        'batterie_haus_prozent und sensor.senec_battery_state_power'. Nehme 10kWh Akku an, negative Power bedeutet Akku entlädt
                        'waschkueche_power' ist die Summe des aktuellen Verbrauchs von Waschmaschine und Trockner.
                        Geräte: Spülmaschine rechne 2500W, Waschmaschine rechne 1000W, Trockner rechne 600W.    
                    - Empfehle 'JETZT', wenn der Überschuss für den typischen Geräteverbrauch reicht (netz_saldo_watt < (-Geräteverbrauch)).
                    - ansonsten Empfehle 'WARTEN', wenn 'pv_rest_prognose_kwh' voraussichtlich ausreicht:
                                - nicht wenn Akku < 50% und < 7kWh für heute prognostiziert
                    - ansonsten Empfehle 'SPÄTER/NACHTS'
                                - falls aktuelle-co2-prozent gerade nicht sauber ist, 
                                    und niedrigste-co2-prozent mindestens 25% niedriger ist 
                                    und niedrigste-co2-uhrzeit mindestens 2h in der Zukunft ist 
                    - ansonsten Empfehle 'EGAL' und liefere kurze Begründung, weshalb es egal ist.
            
            [BEISPIELE - LERNE DARAUS!]
            Input: "Device: Waschmaschine"
            Antwort: "Ja, mach an! Wir speisen gerade 2500 Watt ein."
            
            Input: "Device: Trockner"
            Antwort: "Lieber warten. Aktuell kein Überschuss, aber später kommt Sonne."
            
            Input: "Device: Auto"
            Antwort: "Es gibt heute keinen PV Strom mehr, aber CO2 Intensität wird um 18:00 niedrig sein."

            Input: "Device: Auto"
            Antwort: "Es gibt heute keinen PV Strom mehr, und CO2 Intensität wird nicht mehr besser."
            
            Input: "{parameters}"
            """
        # --- PROMPT BAUEN ---

        try:
            response = get_client().models.generate_content(
                model=AI_MODEL_NAME,
                contents=system_prompt,
                config={"tools": [{"function_declarations": tools_schema}]}
            )

            # Tool Call Check (v2 SDK Style)
            tool_called = False
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        tool_called = True
                        fc = part.function_call
                        if fc.name == "control_device":
                            eid = fc.args.get("entity_id")
                            act = fc.args.get("action")
                            dom = eid.split('.')[0] if "." in eid else ""
                            if await execute_ha_service(dom, act, eid):
                                response_text = f"Okay, {act} für {eid} ausgeführt."
                            else:
                                response_text = f"Fehler beim Schalten von {eid}."
                        break

            if not tool_called:
                response_text = response.text if response.text else "Keine Antwort."

        except Exception as e:
            print(f"AI Error: {e}")
            response_text = "Fehler im KI-Modell."

        return response_text