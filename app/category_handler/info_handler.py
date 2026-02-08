import json
from typing import List, Any, Dict
from category_handler.base import BaseHandler, HandlerResult
from const import tools_schema
from genai_client.client import get_client

AI_MODEL_NAME = "gemini-flash-lite-latest"

class InfoHandler(BaseHandler):
    async def execute(self, parameters: List[Any], ha_service: Any, session_attributes: Dict[str, Any] = None, intent_name: str = None) -> HandlerResult:
        print("InfoHandler aufgerufen.")
        response_text = "Fehler."
        
        smart_home_context = await ha_service.get_smart_home_context()

        system_prompt = f"""
                Du bist ein Smart Home Assistent.
                
                [KONTEXT]
                Energie-Werte: {json.dumps(smart_home_context.get("energy_context", {}))}
                Geräte: {json.dumps(smart_home_context.get("controllable_devices", []))}
                Sensoren: {json.dumps(smart_home_context.get("sensors", []))}
                
                [ENTSCHEIDUNGS-LOGIK]
                Analysiere den User Input genau:
                
                
                FRAGE:
                -> NUTZE KEIN TOOL! Antworte nur mit Text basierend auf diesen Regeln:
                        - Annahmen / Rahmenbedingungen:    
                            - Es können Fragen über mehrere Geräte hinweg gestellt werden.
                            - Bündle Antwort in einer logischen Art
                
                [BEISPIELE - LERNE DARAUS!]
                Input: "Wir wollen das Haus verlassen."
                Antwort: "Okay, Du hast Fenster Schlafzimmer offen, und es Brennt Licht in der Waschküche."
                
                Input: "Gibt es heute noch PV Strom?"
                Antwort: "Ja, heute kannst Du noch mit 5kWh rechnen, der PV Akku ist bei 100%"
                
                Input: "{parameters}"
                """
        # --- PROMPT BAUEN ---

        try:
            response = get_client().models.generate_content(
                model=AI_MODEL_NAME,
                contents=system_prompt,
                config={"tools": [{"function_declarations": tools_schema}]},
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
                            dom = eid.split(".")[0] if "." in eid else ""
                            if await ha_service.execute_ha_service(dom, act, eid):
                                response_text = f"Okay, {act} für {eid} ausgeführt."
                            else:
                                response_text = f"Fehler beim Schalten von {eid}."
                        break

            if not tool_called:
                response_text = response.text if response.text else "Keine Antwort."

        except Exception as e:
            print(f"AI Error: {e}")
            response_text = "Fehler im KI-Modell."

        return HandlerResult(text=response_text)
