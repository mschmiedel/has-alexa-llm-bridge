import asyncio
from datetime import datetime, timedelta

import httpx
from const import EVCC_URL
from evcc_service.models import (
    EvoptResponse,
    Plan,
    PlanDevice,
    PlanSlot,
    TemperatureRate,
)


class EvccUnavailableError(Exception):
    """evcc ist nicht erreichbar oder liefert keinen Optimizer-Plan."""


def _temperature_at(moment: datetime, rates: list[TemperatureRate]) -> float | None:
    return next((r.value for r in rates if r.start <= moment < r.end), None)


def build_plan(raw: EvoptResponse, temperatures: list[TemperatureRate] | None = None) -> Plan:
    series = raw.req.time_series
    timestamps = raw.details.timestamp
    devices = [
        PlanDevice(type=d.type, key=d.name or f"{d.type}:{i}", title=d.title)
        for i, d in enumerate(raw.details.batteryDetails)
    ]

    slots: list[PlanSlot] = []
    for i, start in enumerate(timestamps):
        slots.append(
            PlanSlot(
                start=start,
                end=start + timedelta(seconds=series.dt[i]),
                solar_wh=series.ft[i],
                load_wh=series.gt[i],
                grid_export_wh=raw.res.grid_export[i],
                grid_import_wh=raw.res.grid_import[i],
                price_grid=series.p_N[i] * 1000,
                price_feedin=series.p_E[i] * 1000,
                temperature=_temperature_at(start, temperatures or []),
                charge_wh={
                    device.key: result.charging_power[i]
                    for device, result in zip(devices, raw.res.batteries)
                },
            )
        )
    return Plan(updated=raw.updated, devices=devices, slots=slots)


class EvccService:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or EVCC_URL or "").rstrip("/")

    async def get_temperatures(self, client: httpx.AsyncClient) -> list[TemperatureRate]:
        """Temperaturprognose; leer, wenn in evcc kein Temperatur-Tarif konfiguriert ist."""
        try:
            resp = await client.get(f"{self.base_url}/api/tariff/temperature", timeout=10.0)
            resp.raise_for_status()
            return [TemperatureRate.model_validate(r) for r in resp.json()["rates"]]
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    async def get_plan(self) -> Plan:
        """Holt den aktuellen Optimizer-Plan (nur das evopt-Fragment des States)."""
        if not self.base_url:
            raise EvccUnavailableError("EVCC_URL ist nicht gesetzt")

        try:
            async with httpx.AsyncClient() as client:
                resp, temperatures = await asyncio.gather(
                    client.get(
                        f"{self.base_url}/api/state",
                        params={"jq": ".evopt"},
                        timeout=10.0,
                    ),
                    self.get_temperatures(client),
                )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise EvccUnavailableError(f"evcc nicht erreichbar: {e}") from e

        if not body:
            raise EvccUnavailableError("evcc liefert keinen Optimizer-Plan")

        try:
            return build_plan(EvoptResponse.model_validate(body), temperatures)
        except (ValueError, IndexError) as e:
            raise EvccUnavailableError(f"Unerwartetes evcc-Format: {e}") from e
