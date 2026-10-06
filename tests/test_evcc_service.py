import json
import os
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from evcc_service.main import EvccService, EvccUnavailableError  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "evopt_sample.json")


def _state_only(body):
    """Antwortet auf den State, der Temperatur-Tarif ist nicht konfiguriert."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tariff/temperature":
            return httpx.Response(404, json={"error": "tariff not available"})
        return httpx.Response(200, json=body)

    return handler


def _client_returning(handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    return patch("evcc_service.main.httpx.AsyncClient", lambda: real(transport=transport))


class TestEvccService(unittest.IsolatedAsyncioTestCase):
    async def test_get_plan_parses_real_response(self):
        with open(FIXTURE) as f:
            body = json.load(f)
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _state_only(body)(request)

        with _client_returning(handler):
            plan = await EvccService("https://evcc.example/").get_plan()

        state_request = next(r for r in requests if r.url.path == "/api/state")
        self.assertEqual(str(state_request.url), "https://evcc.example/api/state?jq=.evopt")
        self.assertEqual(len(plan.slots), len(body["details"]["timestamp"]))
        self.assertEqual([d.type for d in plan.devices], ["vehicle", "battery"])
        first = plan.slots[0]
        self.assertEqual((first.end - first.start).total_seconds(), 309)
        # EUR/Wh -> EUR/kWh
        self.assertAlmostEqual(first.price_feedin, 0.071)
        self.assertEqual(set(first.charge_wh), {"vehicle-1", "home_battery"})
        self.assertIsNone(first.temperature)

    async def test_temperature_forecast_is_attached(self):
        with open(FIXTURE) as f:
            body = json.load(f)
        start = body["details"]["timestamp"][1]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tariff/temperature":
                rates = [{"start": start, "end": "2099-01-01T00:00:00+00:00", "value": 7.5}]
                return httpx.Response(200, json={"rates": rates})
            return httpx.Response(200, json=body)

        with _client_returning(handler):
            plan = await EvccService("https://evcc.example").get_plan()

        self.assertIsNone(plan.slots[0].temperature)
        self.assertEqual(plan.slots[1].temperature, 7.5)

    async def test_device_without_name(self):
        with open(FIXTURE) as f:
            body = json.load(f)
        del body["details"]["batteryDetails"][0]["name"]

        with _client_returning(_state_only(body)):
            plan = await EvccService("https://evcc.example").get_plan()

        self.assertEqual(plan.devices[0].key, "vehicle:0")
        self.assertIn("vehicle:0", plan.slots[0].charge_wh)

    async def test_missing_url(self):
        with self.assertRaises(EvccUnavailableError):
            await EvccService("").get_plan()

    async def test_missing_plan(self):
        with _client_returning(lambda r: httpx.Response(200, json=None)):
            with self.assertRaises(EvccUnavailableError):
                await EvccService("https://evcc.example").get_plan()

    async def test_http_error(self):
        with _client_returning(lambda r: httpx.Response(500)):
            with self.assertRaises(EvccUnavailableError):
                await EvccService("https://evcc.example").get_plan()

    async def test_unexpected_format(self):
        with _client_returning(lambda r: httpx.Response(200, json={"foo": 1})):
            with self.assertRaises(EvccUnavailableError):
                await EvccService("https://evcc.example").get_plan()


if __name__ == "__main__":
    unittest.main()
