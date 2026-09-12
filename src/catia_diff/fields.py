"""Canonical title-block field names and their EN/TR aliases.

Title blocks differ per company, per CAD system and per language.  Extractors
map whatever they find (DXF block attributes, PDF text, vision output) onto
the canonical names below so the checkers only ever deal with one vocabulary.
"""

from __future__ import annotations

import re
from typing import Final

DRAWING_NUMBER: Final = "drawing_number"
PART_NUMBER: Final = "part_number"
TITLE: Final = "title"
REVISION: Final = "revision"
REVISION_DATE: Final = "revision_date"
SCALE: Final = "scale"
SHEET: Final = "sheet"
SHEET_SIZE: Final = "sheet_size"
UNITS: Final = "units"
MATERIAL: Final = "material"
MASS: Final = "mass"
SURFACE_TREATMENT: Final = "surface_treatment"
HEAT_TREATMENT: Final = "heat_treatment"
GENERAL_TOLERANCE: Final = "general_tolerance"
SURFACE_ROUGHNESS: Final = "surface_roughness"
PROJECTION: Final = "projection"
DRAWN_BY: Final = "drawn_by"
DRAWN_DATE: Final = "drawn_date"
CHECKED_BY: Final = "checked_by"
APPROVED_BY: Final = "approved_by"
APPROVED_DATE: Final = "approved_date"
COMPANY: Final = "company"
QUANTITY: Final = "quantity"
PROJECT: Final = "project"

ALL_FIELDS: Final[tuple[str, ...]] = (
    DRAWING_NUMBER,
    PART_NUMBER,
    TITLE,
    REVISION,
    REVISION_DATE,
    SCALE,
    SHEET,
    SHEET_SIZE,
    UNITS,
    MATERIAL,
    MASS,
    SURFACE_TREATMENT,
    HEAT_TREATMENT,
    GENERAL_TOLERANCE,
    SURFACE_ROUGHNESS,
    PROJECTION,
    DRAWN_BY,
    DRAWN_DATE,
    CHECKED_BY,
    APPROVED_BY,
    APPROVED_DATE,
    COMPANY,
    QUANTITY,
    PROJECT,
)

FIELD_LABELS: Final[dict[str, dict[str, str]]] = {
    "en": {
        DRAWING_NUMBER: "Drawing number",
        PART_NUMBER: "Part number",
        TITLE: "Title",
        REVISION: "Revision",
        REVISION_DATE: "Revision date",
        SCALE: "Scale",
        SHEET: "Sheet",
        SHEET_SIZE: "Sheet size",
        UNITS: "Units",
        MATERIAL: "Material",
        MASS: "Mass",
        SURFACE_TREATMENT: "Surface treatment",
        HEAT_TREATMENT: "Heat treatment",
        GENERAL_TOLERANCE: "General tolerance",
        SURFACE_ROUGHNESS: "Surface roughness",
        PROJECTION: "Projection method",
        DRAWN_BY: "Drawn by",
        DRAWN_DATE: "Drawn date",
        CHECKED_BY: "Checked by",
        APPROVED_BY: "Approved by",
        APPROVED_DATE: "Approval date",
        COMPANY: "Company",
        QUANTITY: "Quantity",
        PROJECT: "Project",
    },
    "tr": {
        DRAWING_NUMBER: "Resim numarası",
        PART_NUMBER: "Parça numarası",
        TITLE: "Parça adı",
        REVISION: "Revizyon",
        REVISION_DATE: "Revizyon tarihi",
        SCALE: "Ölçek",
        SHEET: "Sayfa",
        SHEET_SIZE: "Kağıt boyutu",
        UNITS: "Birim",
        MATERIAL: "Malzeme",
        MASS: "Ağırlık",
        SURFACE_TREATMENT: "Yüzey işlemi",
        HEAT_TREATMENT: "Isıl işlem",
        GENERAL_TOLERANCE: "Genel tolerans",
        SURFACE_ROUGHNESS: "Yüzey pürüzlülüğü",
        PROJECTION: "İzdüşüm yöntemi",
        DRAWN_BY: "Çizen",
        DRAWN_DATE: "Çizim tarihi",
        CHECKED_BY: "Kontrol eden",
        APPROVED_BY: "Onaylayan",
        APPROVED_DATE: "Onay tarihi",
        COMPANY: "Firma",
        QUANTITY: "Adet",
        PROJECT: "Proje",
    },
}

#: canonical name -> aliases as they appear on drawings (any language/case).
FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    DRAWING_NUMBER: ("drawing no", "drawing number", "dwg no", "dwg", "drw no", "resim no",
                     "teknik resim no", "çizim no", "cizim no", "plan no"),
    PART_NUMBER: ("part no", "part number", "pn", "parca no", "parça no", "malzeme no",
                  "stok no", "item no"),
    TITLE: ("title", "description", "part name", "denomination", "parça adı", "parca adi",
            "resim adı", "resim adi", "adı", "tanım", "tanim"),
    REVISION: ("rev", "revision", "rev no", "revizyon", "revizyon no", "değişiklik"),
    REVISION_DATE: ("rev date", "revision date", "revizyon tarihi", "değişiklik tarihi"),
    SCALE: ("scale", "ölçek", "olcek", "measure scale", "echelle", "maßstab", "massstab"),
    SHEET: ("sheet", "sht", "sayfa", "pafta", "blatt", "feuille", "page"),
    SHEET_SIZE: ("size", "format", "sheet size", "kağıt", "kagit", "kağıt boyutu", "format"),
    UNITS: ("unit", "units", "birim", "birimler", "dimensions in", "ölçü birimi"),
    MATERIAL: ("material", "matl", "malzeme", "werkstoff", "matiere", "matière"),
    MASS: ("mass", "weight", "ağırlık", "agirlik", "kütle", "gewicht", "poids"),
    SURFACE_TREATMENT: ("surface treatment", "finish", "coating", "yüzey işlemi",
                        "yuzey islemi", "kaplama", "yüzey kaplama"),
    HEAT_TREATMENT: ("heat treatment", "ht", "ısıl işlem", "isil islem", "sertleştirme"),
    GENERAL_TOLERANCE: ("general tolerance", "gen tol", "tolerance", "genel tolerans",
                        "tolerans", "allgemeintoleranz", "iso 2768"),
    SURFACE_ROUGHNESS: ("surface roughness", "roughness", "ra", "yüzey pürüzlülüğü",
                        "yuzey puruzlulugu", "pürüzlülük"),
    PROJECTION: ("projection", "projection method", "angle projection", "izdüşüm",
                 "izdusum", "görünüş yöntemi", "projeksiyon"),
    DRAWN_BY: ("drawn", "drawn by", "designed by", "author", "çizen", "cizen", "hazırlayan",
               "tasarlayan", "gezeichnet"),
    DRAWN_DATE: ("date", "drawn date", "tarih", "çizim tarihi", "datum"),
    CHECKED_BY: ("checked", "checked by", "control", "kontrol", "kontrol eden", "geprüft"),
    APPROVED_BY: ("approved", "approved by", "released by", "onay", "onaylayan", "onaylıyan"),
    APPROVED_DATE: ("approval date", "approved date", "onay tarihi"),
    COMPANY: ("company", "firm", "firma", "şirket", "sirket", "kurum"),
    QUANTITY: ("qty", "quantity", "adet", "miktar", "stück"),
    PROJECT: ("project", "proje", "program", "job"),
}

_TR_FOLD = str.maketrans(
    {
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
        "â": "a", "î": "i", "û": "u",
    }
)


def fold(text: str) -> str:
    """Case/diacritic-insensitive key used for alias matching."""
    folded = text.translate(_TR_FOLD).lower()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


_ALIAS_INDEX: Final[dict[str, str]] = {
    fold(alias): canonical
    for canonical, aliases in FIELD_ALIASES.items()
    for alias in (*aliases, canonical.replace("_", " "))
}


def canonical_field(label: str | None) -> str | None:
    """Map a raw title-block label onto a canonical field name."""
    if not label:
        return None
    key = fold(str(label)).strip(" :.-")
    if not key:
        return None
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    # tolerate labels such as "MALZEME / MATERIAL" or "SCALE:"
    for part in re.split(r"[/|,]", key):
        part = part.strip()
        if part in _ALIAS_INDEX:
            return _ALIAS_INDEX[part]
    # tolerate labels with trailing noise ("DRAWN BY (NAME)")
    for alias, canonical in _ALIAS_INDEX.items():
        if len(alias) >= 4 and key.startswith(alias):
            return canonical
    return None


def field_label(field: str, lang: str = "en") -> str:
    table = FIELD_LABELS.get(lang, FIELD_LABELS["en"])
    return table.get(field, field.replace("_", " ").title())
