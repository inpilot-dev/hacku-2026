"""The shopper's delivery details (GET/PUT /profile), asked for during onboarding.

Kept server-side in one 0600 file per user under MANDATE_PROFILE_DATA_DIR
(default services/api/.data/profiles, gitignored). Checkout forms on shop sites
are filled from it, so these details are sent to the browser agent's models
(TypeSafe chooses fields, an OpenRouter model writes their values) and to the shop.
Nothing here logs them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".data" / "profiles"
FIELDS = ("full_name", "email", "phone", "address_line1", "address_line2", "district", "region", "city", "country",
          "postal_code")
REGIONS = ("Hong Kong Island", "Kowloon", "New Territories")
# The 18 districts, so the region (which Hong Kong checkouts often ask for) can be filled in when not given.
DISTRICT_REGIONS = {
    **dict.fromkeys(("central and western", "central", "wan chai", "eastern", "southern"), "Hong Kong Island"),
    **dict.fromkeys(("yau tsim mong", "sham shui po", "kowloon city", "wong tai sin", "kwun tong"), "Kowloon"),
    **dict.fromkeys(("tsuen wan", "tuen mun", "yuen long", "north", "tai po", "sai kung", "sha tin", "kwai tsing",
                     "islands"), "New Territories"),
}
REQUIRED = ("full_name", "email", "phone", "address_line1", "district", "country")


class ProfileStore:
    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or os.environ.get("MANDATE_PROFILE_DATA_DIR") or DEFAULT_DIR)

    def _path(self, user_id: str) -> Path:
        safe = user_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id) else hashlib.sha256(user_id.encode()).hexdigest()
        return self.data_dir / f"{safe}.json"

    def get(self, user_id: str) -> dict | None:
        path = self._path(user_id)
        return json.loads(path.read_text()) if path.is_file() else None

    def put(self, user_id: str, profile: dict) -> dict:
        record = {k: (profile.get(k) or "").strip() or None for k in FIELDS}
        if not record["region"] and record["district"]:
            record["region"] = DISTRICT_REGIONS.get(record["district"].lower().removesuffix(" district"))
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(user_id)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
        os.replace(tmp, path)
        return record


def missing_fields(profile: dict | None) -> list[str]:
    return [k for k in REQUIRED if not (profile or {}).get(k)]


def shipping_lines(profile: dict) -> str:
    """The details as labelled lines for the checkout goal."""
    labels = {"full_name": "Full name", "email": "Email", "phone": "Phone (Hong Kong mobile)",
              "address_line1": "Address line 1", "address_line2": "Address line 2", "district": "District / area",
              "region": "Region (Hong Kong Island / Kowloon / New Territories)",
              "city": "City", "country": "Country / region", "postal_code": "Postal code"}
    lines = [f"{labels[k]}: {profile[k]}" for k in FIELDS if profile.get(k)]
    if not profile.get("postal_code"):
        lines.append("Postal code: none (Hong Kong has no postal codes; leave it empty, or enter 000000 if required)")
    return "\n".join(lines)


# What each form value is, so a model can tell which one a checkout field wants.
FORM_KEYS = {
    "email": "email address",
    "phone": "phone number",
    "full_name": "full name, only for a single name field",
    "first_name": "first / given name, when the form has separate first and last name fields",
    "last_name": "last / family name (surname), when the form has separate first and last name fields",
    "address_line1": "street address or main address line",
    "address_line2": "flat, floor, apartment, suite or building (second address line)",
    "full_address": "the whole address in one line, only for a single address field",
    "district": "district or area (e.g. Wan Chai)",
    "region": "region: Hong Kong Island, Kowloon or New Territories",
    "city": "city",
    "country": "country or region",
    "postal_code": "postal or ZIP code",
}


def form_values(profile: dict) -> dict[str, str]:
    """Every value a checkout form may ask for, by FORM_KEYS key. Fields are only ever filled from these."""
    values = {k: profile[k] for k in FIELDS if profile.get(k)}
    name = (profile.get("full_name") or "").split()
    if len(name) >= 2:
        values["first_name"], values["last_name"] = " ".join(name[:-1]), name[-1]
    if profile.get("address_line1"):
        values["full_address"] = ", ".join(profile[k] for k in ("address_line2", "address_line1", "district", "city")
                                           if profile.get(k))
    return values
