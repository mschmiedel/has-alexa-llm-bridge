import requests
import os
import sys
from dotenv import load_dotenv  # <--- WICHTIG

# 1. Lade Variablen aus der .env Datei
load_dotenv()

# --- KONFIGURATION ---
# 1. Variable holen
base_url = os.getenv("HA_URL")

# 2. Prüfen, ob sie existiert
if not base_url:
    print("Fehler: Die Umgebungsvariable 'HA_URL' ist nicht gesetzt.")
    sys.exit(1)

# 3. Sicher zusammensetzen (entfernt Slash am Ende von base_url, falls vorhanden)
URL = f"{base_url.rstrip('/')}/api/states"
TOKEN = os.getenv("HA_TOKEN")
# ---------------------

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "content-type": "application/json",
}

try:
    response = requests.get(URL, headers=headers)
    response.raise_for_status()
    data = response.json()

    # Wir filtern und sortieren gleich, damit es lesbar ist
    with open("ha_sensors.txt", "w", encoding="utf-8") as f:
        # Sortiert nach Domain (z.B. light, sensor)
        sorted_entities = sorted(data, key=lambda x: x['entity_id'])

        for entity in sorted_entities:
            eid = entity['entity_id']
            # Optional: Filtern, wenn du z.B. nur Sensoren willst
            # if not eid.startswith(("sensor.", "binary_sensor.")): continue

            name = entity['attributes'].get('friendly_name', 'Unbenannt')
            unit = entity['attributes'].get('unit_of_measurement', '')
            state = entity['state']

            # Format: domain.name | "Freundlicher Name" | Aktueller Wert
            line = f"{eid} | {name} | {state} {unit}"
            f.write(line + "\n")

    print(f"Fertig! {len(data)} Entitäten in 'ha_sensors.txt' gespeichert.")

except Exception as e:
    print(f"Fehler: {e}")