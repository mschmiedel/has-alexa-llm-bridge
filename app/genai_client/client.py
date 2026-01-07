# Dateiname: ai_client.py
import os
from google import genai
from dotenv import load_dotenv

from const import GOOGLE_API_KEY

# 1. Umgebungsvariablen laden (.env Datei lesen)
# Das sucht automatisch nach einer .env Datei im Projektordner
load_dotenv()

# 2. Key auslesen
api_key = os.getenv("GOOGLE_API_KEY")

# 3. Interne Variable für das Singleton
_client_instance = None


def get_client():
    """
    Gibt die Client-Instanz zurück (Lazy Singleton).
    Erstellt sie nur, wenn sie noch nicht existiert.
    """
    global _client_instance

    # Wenn wir den Client schon haben, sofort zurückgeben (Caching)
    if _client_instance is not None:
        return _client_instance

    # Prüfen, ob der Key da ist
    if not api_key:
        print("❌ FEHLER: GOOGLE_API_KEY wurde in der .env Datei nicht gefunden!")
        return None

    try:
        # Client konfigurieren und erstellen
        print("🔌 Initialisiere Google AI Client...")
        _client_instance = genai.Client(api_key=GOOGLE_API_KEY)

        return _client_instance

    except Exception as e:
        print(f"❌ Fehler beim Erstellen des Clients: {e}")
        return None
