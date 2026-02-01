import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Wir fügen den 'app' Ordner zum Python-Pfad hinzu, damit Imports wie 'from const import ...' funktionieren
sys.path.append(os.path.join(os.path.dirname(__file__), "../app"))

from category_handler.leave_home_handler import LeaveHomeHandler

@pytest.mark.asyncio
async def test_leave_home_handler_execute():
    """
    Testet den LeaveHomeHandler isoliert.
    - Mockt HA Service (Input)
    - Mockt GenAI Client (Output)
    - Prüft Logik (Filterung) und Flow.
    """
    
    # 1. Setup Mocks
    
    # HA Service Mock
    mock_ha_service = AsyncMock()
    
    # Test-Daten: Ein Mix aus relevanten und irrelevanten Geräten
    # Wir simulieren: 1 Licht an, 1 Fenster offen, 1 Gerät mit hohem Verbrauch
    mock_context = {
        "controllable_devices": [
            # Sollte erkannt werden (Licht an)
            {"eid": "light.wohnzimmer", "area": "Wohnzimmer", "state": "on", "device_class": "light"},
            # Sollte ignoriert werden (Licht aus)
            {"eid": "light.kueche", "area": "Küche", "state": "off", "device_class": "light"},
            # Sollte ignoriert werden (kein Licht)
            {"eid": "switch.steckdose", "area": "Büro", "state": "on", "device_class": "switch"},
        ],
        "sensors": [
            # Sollte erkannt werden (Fenster offen / on)
            {"eid": "binary_sensor.fenster_bad", "area": "Bad", "state": "on", "device_class": "window"},
            # Sollte ignoriert werden (Fenster zu / off)
            {"eid": "binary_sensor.fenster_schlafzimmer", "area": "Schlafzimmer", "state": "off", "device_class": "window"},
            # Sollte erkannt werden (Hoher Verbrauch > 500)
            {"eid": "sensor.waschmaschine_power", "area": "Waschküche", "state": "1200.5", "device_class": "power"},
            # Sollte ignoriert werden (Niedriger Verbrauch)
            {"eid": "sensor.tv_power", "area": "Wohnzimmer", "state": "50", "device_class": "power"},
        ]
    }
    mock_ha_service.get_smart_home_context.return_value = mock_context

    # GenAI Client Mock
    # Wir patchen 'get_client' dort, wo es im Handler importiert/genutzt wird
    with patch("category_handler.leave_home_handler.get_client") as mock_get_client:
        
        # Mock für das Client-Objekt und die models.generate_content Methode
        mock_model_client = MagicMock()
        mock_get_client.return_value = mock_model_client
        
        mock_response = MagicMock()
        expected_ai_response = "Das Licht im Wohnzimmer ist an. Das Fenster im Bad ist offen. Die Waschmaschine läuft."
        mock_response.text = expected_ai_response
        
        mock_model_client.models.generate_content.return_value = mock_response

        # 2. Execution
        handler = LeaveHomeHandler()
        # Parameters werden im LeaveHomeHandler aktuell ignoriert, wir übergeben leere Liste
        result = await handler.execute([], mock_ha_service)

        # 3. Assertions

        # A. Wurde der HA Service korrekt aufgerufen?
        mock_ha_service.get_smart_home_context.assert_awaited_once()

        # B. Wurde der GenAI Client aufgerufen?
        mock_model_client.models.generate_content.assert_called_once()

        # C. Prüfen, ob der Prompt die gefilterten Daten enthält
        call_args = mock_model_client.models.generate_content.call_args
        # call_args.kwargs['contents'] enthält den Prompt
        prompt_content = call_args.kwargs.get("contents", "")
        
        # Positive Checks (Sollten im JSON String im Prompt enthalten sein)
        assert "light.wohnzimmer" in prompt_content
        assert "binary_sensor.fenster_bad" in prompt_content
        assert "sensor.waschmaschine_power" in prompt_content
        
        # Negative Checks (Sollten herausgefiltert sein)
        assert "light.kueche" not in prompt_content
        assert "binary_sensor.fenster_schlafzimmer" not in prompt_content
        assert "sensor.tv_power" not in prompt_content

        # D. Prüfen des Rückgabewerts
        assert result == expected_ai_response
