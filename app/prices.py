"""Product prices the farmer enters in "Můj sklad" and the cost they mean per hectare and per field.

There is no public price list for professional plant protection products in the Czech Republic
(prices depend on the distributor, pack size and contract), so each user enters their own
purchase price per litre or kilogram.
"""
from __future__ import annotations

import re

PRICE_UNITS = ("l", "kg")
# dose unit before "/ha" -> (price unit it belongs to, factor to convert to that unit)
_BASE = {"l": ("l", 1.0), "ml": ("l", 0.001), "kg": ("kg", 1.0), "g": ("kg", 0.001)}
_DOSE_RE = re.compile(r"(\d+(?:[.,]\d+)?)(?:\s*[-–]\s*(\d+(?:[.,]\d+)?))?\s*(ml|l|kg|g)\s*/\s*ha\b", re.I)


def per_ha_unit(dose_unit: str | None) -> tuple[str, float] | None:
    """'ml/ha' -> ('l', 0.001); None for doses not given per hectare (l/t, kg/m3...)."""
    u = (dose_unit or "").strip().lower().replace(" ", "")
    if not u.endswith("/ha"):
        return None
    return _BASE.get(u[:-3])


def parse_dose(text: str | None) -> tuple[float, float, str] | None:
    """'0,6-0,8 l/ha' -> (0.6, 0.8, 'l/ha'); used when the registry has no structured dose."""
    m = _DOSE_RE.search(text or "")
    if not m:
        return None
    lo = float(m.group(1).replace(",", "."))
    hi = float(m.group(2).replace(",", ".")) if m.group(2) else lo
    return lo, hi, m.group(3).lower() + "/ha"


def use_cost(use: dict, price: float | None, price_unit: str | None) -> dict | None:
    """Cost per hectare (min-max Kč) of one registered use at the user's price.

    Returns None when there is no price; {"error": ...} when the dose cannot be priced
    (not per hectare, or litres vs kilograms)."""
    if not price or price_unit not in PRICE_UNITS:
        return None
    lo, hi, unit = use.get("dose_min"), use.get("dose_max"), use.get("dose_unit")
    if lo is None and hi is None or not per_ha_unit(unit):
        parsed = parse_dose(use.get("dose_text") or use.get("dose"))
        if parsed:
            lo, hi, unit = parsed
    base = per_ha_unit(unit)
    if base is None or (lo is None and hi is None):
        return {"error": "dávka není na hektar"}
    if base[0] != price_unit:
        return {"error": f"cena je za {price_unit}, dávka v {unit}"}
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return {"min": round(lo * base[1] * price, 2), "max": round(hi * base[1] * price, 2)}


def record_cost(dose: float | None, dose_unit: str, area_ha: float | None, price: float | None) -> float | None:
    """Cost of one spray record in Kč: dose x treated area x price per l/kg of the product."""
    base = per_ha_unit(dose_unit)
    if not (price and dose and area_ha and base):
        return None
    return round(dose * base[1] * area_ha * price, 2)


def price_unit_for(dose_unit: str) -> str:
    """'ml/ha' -> 'l', 'g/ha' -> 'kg' (the unit a record's price is given in)."""
    b = per_ha_unit(dose_unit)
    return b[0] if b else ""
