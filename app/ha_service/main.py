import asyncio
import traceback
from datetime import datetime, timedelta
import httpx

from const import HA_URL, HA_TOKEN

# Mappings moved from main.py
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
    "haus_power": "sensor.senec_house_power"
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

class HaService:
    def __init__(self):
        self.base_url = HA_URL
        self.token = HA_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def execute_ha_service(self, domain: str, service: str, entity_id: str):
        """Führt Aktion aus"""
        if not self.base_url or not self.token:
            return False
        url = f"{self.base_url}/api/services/{domain}/{service}"
        
        payload = {"entity_id": entity_id}
        print(f"HA ACTION: {domain}.{service} -> {entity_id}")
        async with httpx.AsyncClient() as http_client:
            try:
                resp = await http_client.post(
                    url, json=payload, headers=self.headers, timeout=5.0
                )
                return resp.status_code == 200
            except Exception:
                return False

    def filter_entities(self, all_states, allowed_domains, blocklist):
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

    async def get_areas(self):
        async with httpx.AsyncClient() as http_client_areas:
            body = {
                "template": "{% set ns = namespace(items=[]) %}{% for s in states %}{% set area = area_name(s.entity_id) %}{% if area %}{% set ns.items = ns.items + [(s.entity_id, area)] %}{% endif %}{% endfor %}{{ dict(ns.items) | to_json }}"
            }
            try:
                response = await http_client_areas.post(
                    f"{self.base_url}/api/template", headers=self.headers, json=body, timeout=5.0
                )
                if response.status_code != 200:
                    return {}
                return response.json()
            except Exception as e:
                print(f"HA Error: {e}")
                return {}

    async def fetch_history_point(self, client, entity_id, timestamp):
        """
        Holt den Status einer Entity zu einem exakten Zeitpunkt in der Vergangenheit.
        """
        try:
            ts_str = timestamp.isoformat()
            # Kleines Zeitfenster definieren (1 Sekunde reicht)
            end_str = (timestamp + timedelta(seconds=1)).isoformat()

            url = f"{self.base_url}/api/history/period/{ts_str}"
            params = {
                "filter_entity_id": entity_id,
                "end_time": end_str,
                "minimal_response": "true"
            }

            response = await client.get(url, headers=self.headers, params=params, timeout=5.0)

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

    async def get_smart_home_context(self):
        """
        Holt ALLE Daten von HA und bereitet sie auf.
        """
        if not self.base_url or not self.token:
            return {"energy_context": {}, "energy_history": {}, "controllable_devices": [], "sensors": []}

        area_task = asyncio.create_task(self.get_areas())

        async with httpx.AsyncClient() as http_client:
            try:
                # 1. Aktuelle States holen (für Live Context & aktuelle Zählerstände)
                response = await http_client.get(
                    f"{self.base_url}/api/states", headers=self.headers, timeout=5.0
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

                    controllable_devices = self.filter_entities(
                        all_states, ["light", "cover", "climate", "switch", "vacuum"],
                        ["Internet Access", "Update", "Firmware", "Status", "sensor", "ChildLock", "Reboot", "Identifizieren", "Scene", "Schedule", "quality", "rssi", "overheat", "overpower"]
                    )
                    sensors = self.filter_entities(
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
                            history_tasks.append(self.fetch_history_point(http_client, entity_id, ts))
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
