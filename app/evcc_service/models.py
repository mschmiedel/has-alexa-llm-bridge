from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- Rohformat: evcc /api/state?jq=.evopt -------------------------------------
# Energiewerte pro Slot sind Wh (Leistung in W = Wh / (dt / 3600)), Preise sind EUR/Wh.


class EvoptTimeSeries(_Lenient):
    dt: list[int]
    ft: list[float]
    gt: list[float]
    p_N: list[float]
    p_E: list[float]


class EvoptRequest(_Lenient):
    time_series: EvoptTimeSeries


class EvoptBatteryResult(_Lenient):
    charging_power: list[float]
    discharging_power: list[float]
    state_of_charge: list[float]


class EvoptResult(_Lenient):
    grid_export: list[float]
    grid_import: list[float]
    batteries: list[EvoptBatteryResult]


class EvoptBatteryDetail(_Lenient):
    type: Literal["battery", "vehicle", "loadpoint"]
    title: str | None = None
    name: str | None = None  # fehlt z.B. bei nicht identifiziertem Fahrzeug
    capacity: float | None = None


class EvoptDetails(_Lenient):
    timestamp: list[datetime]
    batteryDetails: list[EvoptBatteryDetail]


class EvoptResponse(_Lenient):
    updated: datetime
    req: EvoptRequest
    res: EvoptResult
    details: EvoptDetails


class TemperatureRate(_Lenient):
    start: datetime
    end: datetime
    value: float


# --- Aufbereitetes Modell für die Beratung ------------------------------------


class PlanDevice(BaseModel):
    type: Literal["battery", "vehicle", "loadpoint"]
    key: str  # eindeutig je Gerät, auch wenn evcc keinen Namen liefert
    title: str | None = None


class PlanSlot(BaseModel):
    start: datetime
    end: datetime
    solar_wh: float
    load_wh: float
    grid_export_wh: float
    grid_import_wh: float
    price_grid: float  # EUR/kWh
    price_feedin: float  # EUR/kWh
    charge_wh: dict[str, float]  # geplante Ladung je Gerät (key -> Wh)
    temperature: float | None = None  # Außentemperatur °C, falls Prognose vorhanden

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


class Plan(BaseModel):
    updated: datetime
    devices: list[PlanDevice]
    slots: list[PlanSlot]
