import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from category_handler.advice_planner import (
    ApplianceAdvice,
    VehicleAdvice,
    Verdict,
    advise_appliance,
    advise_vehicle,
)
from category_handler.base import BaseHandler, HandlerResult
from category_handler.device_profiles import is_vehicle, profile_for
from evcc_service.main import EvccService, EvccUnavailableError
from genai_client.client import get_client

AI_MODEL_NAME = "gemini-flash-lite-latest"
TIMEZONE = ZoneInfo("Europe/Berlin")

logger = logging.getLogger(__name__)

PROMPT = """
Du bist ein Energieberater aus einem Smart Home. Die Empfehlung wurde bereits berechnet.
Formuliere sie als gesprochene Antwort.

[ERGEBNIS]
{advice}

[REGELN]
- Höchstens 30 Wörter, ein bis zwei kurze Sätze, kein Markdown.
- Nenne die Empfehlung und, wenn vorhanden, die Uhrzeit aus dem Ergebnis. Erfinde keine Werte.
- Beim Gerät (Spülmaschine, Waschmaschine, ...): JETZT heißt sofort starten, SPÄTER nenne
  den Startzeitpunkt, EGAL heißt der Zeitpunkt spielt kaum eine Rolle.
- Bei JETZT und SPÄTER nenne einen konkreten Wert zur Begründung, z.B. den PV-Anteil in
  Prozent oder die gesparten Cent.
- Bei EGAL nenne keine Ersparnis und keinen Startzeitpunkt. Sage nur, dass der Zeitpunkt
  kaum eine Rolle spielt, und gib höchstens den Hinweis, dass es mit Sonnenstrom am
  günstigsten ist.
- Bei der Klimaanlage: Heizen oder Kühlen lässt sich nicht beliebig verschieben. Wenn eine
  Außentemperatur im Ergebnis steht, nenne sie, und rate dazu, mit Sonnenstrom vorzuheizen
  oder vorzukühlen, statt nur zu warten.
- Beim Auto: Nenne, wann der Plan laden vorsieht, oder dass heute nicht geladen wird.

[BEISPIELE]
Ergebnis: Waschmaschine, JETZT, PV-Anteil 90 Prozent
Antwort: "Ja, mach an! Die Sonne deckt gerade 90 Prozent ab."

Ergebnis: Trockner, SPÄTER ab 11:30 Uhr, spart 24 Cent
Antwort: "Lieber warten. Ab 11:30 Uhr läuft er mit Sonnenstrom und spart etwa 24 Cent."

Ergebnis: Klimaanlage, SPÄTER ab 11:00 Uhr, Außentemperatur jetzt 3 Grad
Antwort: "Heize ruhig ab 11:00 Uhr vor. Dann läuft sie mit Sonnenstrom, jetzt sind es nur 3 Grad."

Ergebnis: Laptop, EGAL
Antwort: "Egal, der Laptop braucht kaum Strom. Mit Sonnenstrom ist es trotzdem am günstigsten."

Ergebnis: Auto, Laden geplant 11:00 bis 13:15 Uhr
Antwort: "Der Plan lädt das Auto heute von 11:00 bis 13:15 Uhr mit Sonnenstrom."

Input: "{parameters}"
"""


def _fmt_time(moment: datetime, now: datetime) -> str:
    local = moment.astimezone(TIMEZONE)
    day = (local.date() - now.date()).days
    prefix = {0: "", 1: "morgen "}.get(day, f"{local:%d.%m.} ")
    return f"{prefix}{local:%H:%M} Uhr"


def _appliance_facts(advice: ApplianceAdvice, now: datetime) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "gerät": advice.device,
        "empfehlung": advice.verdict.value,
        "pv_anteil_jetzt_prozent": round(advice.now.solar_share * 100),
        "strompreis_jetzt_cent": round(advice.price_grid_now * 100, 1),
        "einspeiseverguetung_cent": round(advice.price_feedin_now * 100, 1),
    }
    if advice.temperature_now is not None:
        facts["aussentemperatur_jetzt_grad"] = round(advice.temperature_now)
    if advice.verdict == Verdict.LATER:
        if advice.temperature_best is not None:
            facts["aussentemperatur_bester_start_grad"] = round(advice.temperature_best)
        facts["bester_start"] = _fmt_time(advice.best.start, now)
        facts["pv_anteil_bester_start_prozent"] = round(advice.best.solar_share * 100)
        facts["ersparnis_cent"] = round((advice.now.cost_eur - advice.best.cost_eur) * 100)
    return facts


def _vehicle_facts(advice: VehicleAdvice, now: datetime) -> dict[str, Any]:
    return {
        "gerät": "Auto",
        "fahrzeug": advice.title,
        "geplante_ladefenster": [
            f"{_fmt_time(w.start, now)} bis {_fmt_time(w.end, now)} ({w.energy_kwh:.1f} kWh)"
            for w in advice.windows
        ],
        "geplante_energie_kwh": round(advice.total_kwh, 1),
    }


def _fallback_text(facts: dict[str, Any]) -> str:
    """Antwort ohne LLM, falls Gemini ausfällt."""
    if "geplante_ladefenster" in facts:
        if not facts["geplante_ladefenster"]:
            return "Der Plan sieht heute kein Laden des Autos vor."
        return f"Laden geplant: {facts['geplante_ladefenster'][0]}."
    device = facts["gerät"]
    verdict = facts["empfehlung"]
    if verdict == Verdict.NOW.value:
        return f"{device}: Jetzt starten, {facts['pv_anteil_jetzt_prozent']} Prozent aus Sonnenstrom."
    if verdict == Verdict.LATER.value:
        return f"{device}: Besser ab {facts['bester_start']}."
    return f"{device}: Der Zeitpunkt ist egal."


class AdviceHandler(BaseHandler):
    def __init__(self, evcc_service: EvccService | None = None):
        self.evcc_service = evcc_service or EvccService()

    async def execute(
        self,
        parameters: list[Any],
        ha_service: Any = None,
        session_attributes: dict[str, Any] | None = None,
        intent_name: str | None = None,
    ) -> HandlerResult:
        logger.info("AdviceHandler aufgerufen.")
        device = str(parameters[0]) if parameters else ""
        if not device:
            return HandlerResult("Für welches Gerät möchtest Du eine Empfehlung?")

        try:
            plan = await self.evcc_service.get_plan()
        except EvccUnavailableError as e:
            logger.error(f"evcc: {e}")
            return HandlerResult("Ich komme gerade nicht an die Energiedaten heran.")

        now = plan.slots[0].start.astimezone(TIMEZONE)

        if is_vehicle(device):
            vehicle = advise_vehicle(plan)
            if not vehicle:
                return HandlerResult("Im Plan ist gerade kein Auto angeschlossen.")
            facts = _vehicle_facts(vehicle, now)
        else:
            appliance = advise_appliance(plan, profile_for(device))
            if not appliance:
                return HandlerResult("Der Plan reicht nicht weit genug für eine Empfehlung.")
            facts = _appliance_facts(appliance, now)

        logger.info(f"Beratung: {json.dumps(facts, ensure_ascii=False)}")
        return HandlerResult(await self._phrase(facts, parameters))

    async def _phrase(self, facts: dict[str, Any], parameters: list[Any]) -> str:
        prompt = PROMPT.format(
            advice=json.dumps(facts, ensure_ascii=False), parameters=parameters
        )
        try:
            response = await asyncio.to_thread(
                get_client().models.generate_content,
                model=AI_MODEL_NAME,
                contents=prompt,
            )
            if response.text:
                return response.text
        except Exception as e:  # noqa: BLE001 Gemini-SDK wirft vielfältige Fehler, es gibt den Template-Fallback
            logger.error(f"AI Error: {e}")
        return _fallback_text(facts)
