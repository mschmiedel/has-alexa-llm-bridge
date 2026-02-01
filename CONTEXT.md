# Projekt Kontext: Alexa LLM Bridge

## 1. Projektziel
Dies ist ein Backend-Service, der als intelligente Brücke zwischen einem Sprachassistenten (Alexa) und einem LLM (Google Gemini) fungiert.
Ziel ist eine saubere Trennung von Voice-Interface, Business-Logik und Smart-Home-Integration.

## 2. Architektur & Code-Map

### A. Entry Point (`app/main.py`)
-   Nimmt Webhooks entgegen.
-   Darf **keine** komplexen Daten vorladen. Seine einzige Aufgabe ist Routing zum richtigen Handler.
-   Soll **SOLID Prinzipien** einhalten. Alles was nicht zum Routing gehört, muss ausgelagert werden.

### B. Business Logic: Handlers (`app/category_handler/`)
-   Nutzt das **Strategy Pattern**. Jeder Intent hat einen Handler (erbt von `BaseHandler`).
-   **Pull-Prinzip:** Handler holen sich benötigte Daten selbst über Services, anstatt dass `main.py` einen riesigen Daten-Blob ("God Object") übergibt.
-   **Generisch:** Handler müssen unabhängig von Alexa sein (Input/Output darf keine Alexa-spezifischen JSON-Strukturen enthalten, sondern Pydantic Models).
-   **Constraint:** Handler dürfen **keine direkten HTTP Calls** an HA machen. Sie müssen den `ha_service` nutzen.

### C. Infrastructure / Services (`app/ha_service/`, `app/genai_client/`)
-   `app/ha_service/`: Kapselt den Zugriff auf Home Assistant.
    -   Sollte Methoden anbieten wie `get_entity_state(entity_id)` oder `get_climate_data(room)`.
    -   Versteckt die Komplexität der HA API und Auth-Token.
-   `app/genai_client/`: Wrapper für Gemini API.

## 3. Coding Standards

-   **Tech Stack:** Python 3.12, FastAPI, Pydantic.
-   **Typisierung:** Strikte Type Hints sind Pflicht.
-   **Async/Await:** Konsequent nutzen für I/O (HA Calls, Gemini Calls).
-   **Dependency Injection:** Services sollten den Handlern injiziert werden (im Konstruktor oder beim Methodenaufruf), statt global importiert zu werden.

## 4. Testing & QA (Bruno)
-   Wir nutzen **Bruno** (`/bruno`) für API-Tests.
-   **Environments:** `local`, `prod-lan`, `prod-internet`.
-   **Regel:** Der Agent muss sicherstellen, dass Tests gegen `local` laufen bzw. die externen Services gemockt werden können. Bevor Code geändert wird: Verstehe die `.bru` Files!

## 5. Aktuelle Refactoring-Ziele (PRIORITÄT)

Derzeit gibt es technische Schulden, die der Agent beheben soll:

1.  **Inversion of Data Flow (Refactoring):**
    -   *Ist:* `main.py` holt pauschal Daten und übergibt sie.
    -   *Soll:* `main.py` instanziiert den `ha_service`. Der Handler bekommt den Service übergeben und ruft `await ha_service.get_relevant_data()` auf.

2.  **Strict Typing:**
    -   Ersetzen von `dict` Rückgaben durch Pydantic Models (z.B. `AssistantResponse`), um die API-Schnittstelle hart zu definieren.