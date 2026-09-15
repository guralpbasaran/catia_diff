"""Parsing of technical-drawing text into structured callouts.

Every ingestion path (DXF entities, PDF text spans, Claude Vision output)
ends up here: whatever the source, a drawing callout is ultimately a short
string such as ``⌀12,5 H7 ±0.05``, ``[30]``, ``2x R5`` or
``{\\Fgdt;j}%%v{\\Fgdt;m}0.1{\\Fgdt;n}%%vA%%vB``.  Centralising the grammar
means the checkers see exactly the same structures no matter where the
drawing came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from catia_diff.models.drawing import (
    DimensionKind,
    GDTCharacteristic,
    MaterialCondition,
    SurfaceSymbolKind,
    Tolerance,
    ToleranceKind,
    Units,
)

# --------------------------------------------------------------------------
# Symbol tables
# --------------------------------------------------------------------------
DIAMETER_CHARS = "⌀ØøΦφ"
#: AutoCAD ``gdt.shx`` letter -> characteristic.  DXF TOLERANCE entities and
#: MTEXT feature control frames encode symbols as letters in this font.
GDT_FONT_LETTERS: dict[str, GDTCharacteristic] = {
    "a": GDTCharacteristic.ANGULARITY,
    "b": GDTCharacteristic.PERPENDICULARITY,
    "c": GDTCharacteristic.FLATNESS,
    "d": GDTCharacteristic.PROFILE_SURFACE,
    "e": GDTCharacteristic.CIRCULARITY,
    "f": GDTCharacteristic.PARALLELISM,
    "g": GDTCharacteristic.CYLINDRICITY,
    "h": GDTCharacteristic.CIRCULAR_RUNOUT,
    "i": GDTCharacteristic.PROFILE_LINE,
    "j": GDTCharacteristic.POSITION,
    "k": GDTCharacteristic.CONCENTRICITY,
    "l": GDTCharacteristic.SYMMETRY,
    "r": GDTCharacteristic.TOTAL_RUNOUT,
    "u": GDTCharacteristic.STRAIGHTNESS,
}
#: Unicode symbol -> characteristic (PDF / vision path).
GDT_UNICODE: dict[str, GDTCharacteristic] = {
    "⏤": GDTCharacteristic.STRAIGHTNESS,
    "—": GDTCharacteristic.STRAIGHTNESS,
    "⏥": GDTCharacteristic.FLATNESS,
    "○": GDTCharacteristic.CIRCULARITY,
    "◯": GDTCharacteristic.CIRCULARITY,
    "⌭": GDTCharacteristic.CYLINDRICITY,
    "⌒": GDTCharacteristic.PROFILE_LINE,
    "⌓": GDTCharacteristic.PROFILE_SURFACE,
    "⊥": GDTCharacteristic.PERPENDICULARITY,
    "∠": GDTCharacteristic.ANGULARITY,
    "∥": GDTCharacteristic.PARALLELISM,
    "//": GDTCharacteristic.PARALLELISM,
    "⌖": GDTCharacteristic.POSITION,
    "◎": GDTCharacteristic.CONCENTRICITY,
    "⌯": GDTCharacteristic.SYMMETRY,
    "↗": GDTCharacteristic.CIRCULAR_RUNOUT,
    "⌰": GDTCharacteristic.TOTAL_RUNOUT,
}
#: Keyword fallback, used when a drawing spells the characteristic out.
GDT_KEYWORDS: dict[str, GDTCharacteristic] = {
    "straightness": GDTCharacteristic.STRAIGHTNESS,
    "flatness": GDTCharacteristic.FLATNESS,
    "circularity": GDTCharacteristic.CIRCULARITY,
    "roundness": GDTCharacteristic.CIRCULARITY,
    "cylindricity": GDTCharacteristic.CYLINDRICITY,
    "profile": GDTCharacteristic.PROFILE_SURFACE,
    "perpendicularity": GDTCharacteristic.PERPENDICULARITY,
    "angularity": GDTCharacteristic.ANGULARITY,
    "parallelism": GDTCharacteristic.PARALLELISM,
    "position": GDTCharacteristic.POSITION,
    "concentricity": GDTCharacteristic.CONCENTRICITY,
    "symmetry": GDTCharacteristic.SYMMETRY,
    "runout": GDTCharacteristic.CIRCULAR_RUNOUT,
    "total runout": GDTCharacteristic.TOTAL_RUNOUT,
}

_MATERIAL_CONDITIONS = {
    "m": MaterialCondition.MMC,
    "Ⓜ": MaterialCondition.MMC,
    "(m)": MaterialCondition.MMC,
    "l": MaterialCondition.LMC,
    "Ⓛ": MaterialCondition.LMC,
    "(l)": MaterialCondition.LMC,
    "s": MaterialCondition.RFS,
    "Ⓢ": MaterialCondition.RFS,
}

_NUM = r"[+-]?\d+(?:[.,]\d+)?"


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
_MTEXT_STACK_RE = re.compile(r"\\S([^^;]*)\^([^;]*);")
_MTEXT_FORMAT_RE = re.compile(r"\\[AaCcFfHhKkLlOoQqTtWwXx][^;\\]*;")
_MTEXT_FONT_RE = re.compile(r"\\[fF][^;]*;")
_MTEXT_BRACES_RE = re.compile(r"[{}]")


def normalize_drawing_text(text: str | None) -> str:
    """Turn DXF/MTEXT markup into plain Unicode drawing text."""
    if not text:
        return ""
    out = str(text)
    # Stacked tolerances: \S+0.2^-0.1;  ->  "+0.2 -0.1"
    out = _MTEXT_STACK_RE.sub(lambda m: f" {m.group(1).strip()} {m.group(2).strip()} ", out)
    # AutoCAD special glyph escapes.
    out = out.replace("%%c", "⌀").replace("%%C", "⌀")
    out = out.replace("%%d", "°").replace("%%D", "°")
    out = out.replace("%%p", "±").replace("%%P", "±")
    out = out.replace("%%%", "%")
    # Column/row separator used inside feature control frames.
    out = out.replace("%%v", "|").replace("%%V", "|")
    out = out.replace("\\P", "\n").replace("\\p", "\n")
    out = _MTEXT_FONT_RE.sub(lambda m: _font_marker(m.group(0)), out)
    out = _MTEXT_FORMAT_RE.sub("", out)
    out = out.replace("\\~", " ").replace("\\\\", "\\")
    out = _MTEXT_BRACES_RE.sub("", out)
    out = out.replace("\u2212", "-")  # unicode minus
    out = re.sub(r"[ \t\xa0]+", " ", out)
    return out.strip()


def _font_marker(token: str) -> str:
    """Keep a marker when MTEXT switches to the GD&T font, drop other fonts."""
    return "\x01gdt\x01" if "gdt" in token.lower() else ""


def to_float(token: str | None) -> float | None:
    """Parse a drawing number, accepting ``,`` as decimal separator."""
    if token is None:
        return None
    token = token.strip().replace(" ", "")
    if not token:
        return None
    if "," in token and "." not in token:
        token = token.replace(",", ".")
    elif "," in token and "." in token:  # 1,234.5 -> thousands separator
        token = token.replace(",", "")
    try:
        return float(token)
    except ValueError:
        return None


def count_decimals(token: str | None) -> int:
    if not token:
        return 0
    match = re.search(r"[.,](\d+)", token)
    return len(match.group(1)) if match else 0


# --------------------------------------------------------------------------
# Dimension text
# --------------------------------------------------------------------------
@dataclass
class ParsedDimension:
    raw: str
    text: str
    nominal: float | None = None
    prefix: str | None = None
    units: Units = Units.UNKNOWN
    tolerance: Tolerance = field(default_factory=Tolerance)
    decimals: int = 0
    multiplicity: int = 1
    is_reference: bool = False
    is_basic: bool = False
    thread: str | None = None
    kind_hint: DimensionKind = DimensionKind.UNKNOWN
    has_measurement_placeholder: bool = False

    @property
    def is_numeric(self) -> bool:
        return self.nominal is not None


_MULTIPLICITY_RE = re.compile(r"^\s*(\d+)\s*(?:x|X|×|-)\s*(?=[⌀ØøΦφRr\d])")
_THREAD_RE = re.compile(
    r"\b(M\d+(?:[.,]\d+)?(?:\s*[x×]\s*\d+(?:[.,]\d+)?)?|"
    r"(?:UNC|UNF|UN|NPT|G|BSP)\s*\d*(?:/\d+)?(?:-\d+)?|"
    r"\d+/\d+\s*-\s*\d+\s*(?:UNC|UNF))\b",
    re.IGNORECASE,
)
_FIT_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{1,2})(\d{1,2})(?![\d.,])")
_FIT_EXCLUDE = {"mm", "cm", "in", "m", "x", "no", "nr", "pcs", "adet", "rev", "ra", "rz"}


def parse_dimension_text(
    raw: str | None,
    *,
    default_units: Units = Units.MM,
    measured: float | None = None,
) -> ParsedDimension | None:
    """Parse a dimension callout.  Returns ``None`` when no dimension is found."""
    text = normalize_drawing_text(raw)
    if not text:
        return None
    parsed = ParsedDimension(raw=str(raw), text=text)
    work = text.replace("\x01gdt\x01", "")

    # "<>" is the DXF placeholder meaning "print the measured value".
    if "<>" in work:
        parsed.has_measurement_placeholder = True
        replacement = "" if measured is None else f"{measured:g}"
        work = work.replace("<>", replacement)

    # Reference / basic markers.
    stripped = work.strip()
    if re.fullmatch(r"\((.+)\)", stripped, re.DOTALL):
        parsed.is_reference = True
        work = stripped[1:-1]
    elif re.fullmatch(r"\[(.+)\]", stripped, re.DOTALL):
        parsed.is_basic = True
        work = stripped[1:-1]
    if re.search(r"\b(REF|REFERENCE|BİLGİ|BILGI)\b", work, re.IGNORECASE):
        parsed.is_reference = True
        work = re.sub(r"\b(REF|REFERENCE|BİLGİ|BILGI)\b", " ", work, flags=re.IGNORECASE)
    if re.search(r"\b(BASIC|BSC|TEORİK|TEORIK)\b", work, re.IGNORECASE):
        parsed.is_basic = True
        work = re.sub(r"\b(BASIC|BSC|TEORİK|TEORIK)\b", " ", work, flags=re.IGNORECASE)

    # Multiplicity: "4x ⌀6".
    mult = _MULTIPLICITY_RE.match(work)
    if mult:
        parsed.multiplicity = int(mult.group(1))
        work = work[mult.end():]

    # Threads are dimensions too, but their "nominal" is the thread diameter.
    thread = _THREAD_RE.search(work)
    if thread and thread.group(0).upper().startswith("M"):
        parsed.thread = thread.group(0).replace(" ", "")
        parsed.kind_hint = DimensionKind.THREAD

    # Prefix symbol.
    prefix_match = re.search(rf"(S?[{DIAMETER_CHARS}]|SR|R|□|SQ|DIA)\s*(?={_NUM})", work)
    if prefix_match:
        token = prefix_match.group(1)
        if token in {"DIA"} or any(ch in token for ch in DIAMETER_CHARS):
            parsed.prefix = "S⌀" if token.upper().startswith("S") and len(token) > 1 else "⌀"
            parsed.kind_hint = DimensionKind.DIAMETER
        elif token in {"R", "SR"}:
            parsed.prefix = token
            parsed.kind_hint = DimensionKind.RADIAL
        else:
            parsed.prefix = "□"
        work = work[: prefix_match.start(1)] + " " + work[prefix_match.end(1):]

    tolerance, work = _extract_tolerance(work)

    # Nominal value: the first standalone number left in the string.
    number = re.search(_NUM, work)
    if number is None:
        if parsed.thread is None:
            return None
    else:
        parsed.nominal = to_float(number.group(0))
        parsed.decimals = count_decimals(number.group(0))
        work = work[: number.start()] + " " + work[number.end():]

    # Units.
    if "°" in text or re.search(r"\bdeg\b", text, re.IGNORECASE):
        parsed.units = Units.DEG
        parsed.kind_hint = DimensionKind.ANGULAR
    elif re.search(r'(?<![A-Za-z])(mm)\b', text, re.IGNORECASE):
        parsed.units = Units.MM
    elif re.search(r'(?<![A-Za-z])(in|inch|")\b', text, re.IGNORECASE) or '"' in text:
        parsed.units = Units.INCH
    else:
        parsed.units = default_units

    if parsed.is_basic:
        tolerance = Tolerance(kind=ToleranceKind.BASIC, raw=tolerance.raw)
    elif parsed.is_reference:
        tolerance = Tolerance(kind=ToleranceKind.REFERENCE, raw=tolerance.raw)
    parsed.tolerance = tolerance
    return parsed


def _extract_tolerance(work: str) -> tuple[Tolerance, str]:
    """Pull a tolerance expression out of ``work``; returns (tolerance, rest)."""
    # 1) symmetric: ±0.05
    sym = re.search(rf"(?:±|\+/-|\+-)\s*({_NUM})", work)
    if sym:
        value = to_float(sym.group(1))
        rest = work[: sym.start()] + " " + work[sym.end():]
        if value is not None:
            return (
                Tolerance(
                    kind=ToleranceKind.SYMMETRIC,
                    upper=abs(value),
                    lower=-abs(value),
                    raw=sym.group(0).strip(),
                ),
                rest,
            )

    # 2) deviation: +0.2 -0.1 / +0.2/-0.1 / +0.2 0
    dev = re.search(
        r"\+\s*(\d+(?:[.,]\d+)?)\s*(?:/|\s)\s*(-\s*\d+(?:[.,]\d+)?|0(?:[.,]0+)?)", work
    )
    if dev is None:
        dev = re.search(
            r"(-\s*\d+(?:[.,]\d+)?)\s*(?:/|\s)\s*\+\s*(\d+(?:[.,]\d+)?)", work
        )
        if dev is not None:
            upper = to_float(dev.group(2))
            lower = to_float(dev.group(1).replace(" ", ""))
            rest = work[: dev.start()] + " " + work[dev.end():]
            return (
                Tolerance(
                    kind=ToleranceKind.DEVIATION,
                    upper=upper,
                    lower=lower,
                    raw=dev.group(0).strip(),
                ),
                rest,
            )
    else:
        upper = to_float(dev.group(1))
        lower = to_float(dev.group(2).replace(" ", ""))
        rest = work[: dev.start()] + " " + work[dev.end():]
        return (
            Tolerance(
                kind=ToleranceKind.DEVIATION, upper=upper, lower=lower, raw=dev.group(0).strip()
            ),
            rest,
        )

    # 3) ISO fit class: H7, g6, H7/g6, js9
    fit = _FIT_RE.search(work)
    if fit and fit.group(1).lower() not in _FIT_EXCLUDE:
        grade = int(fit.group(2))
        if 1 <= grade <= 18:
            raw = fit.group(0)
            rest = work[: fit.start()] + " " + work[fit.end():]
            second = _FIT_RE.search(rest)
            if second and second.group(1).lower() not in _FIT_EXCLUDE and 1 <= int(second.group(2)) <= 18:
                raw = f"{raw}/{second.group(0)}"
                rest = rest[: second.start()] + " " + rest[second.end():]
            return Tolerance(kind=ToleranceKind.FIT_CLASS, fit_class=raw, raw=raw), rest

    # 4) limits: 20.2/19.9  (both numbers, upper first)
    limits = re.search(rf"({_NUM})\s*/\s*({_NUM})", work)
    if limits:
        high, low = to_float(limits.group(1)), to_float(limits.group(2))
        if high is not None and low is not None and high != low:
            rest = work[: limits.start()] + f" {max(high, low):g} " + work[limits.end():]
            return (
                Tolerance(
                    kind=ToleranceKind.LIMITS,
                    upper=max(high, low),
                    lower=min(high, low),
                    raw=limits.group(0).strip(),
                ),
                rest,
            )
    return Tolerance(), work


# --------------------------------------------------------------------------
# Feature control frames (GD&T)
# --------------------------------------------------------------------------
@dataclass
class ParsedFCF:
    characteristic: GDTCharacteristic
    value: float | None
    diametral_zone: bool
    material_condition: MaterialCondition
    datums: list[tuple[str, MaterialCondition]]
    raw: str


def parse_feature_control_frame(raw: str | None) -> ParsedFCF | None:
    """Parse a feature control frame from MTEXT/TOLERANCE/PDF text."""
    if not raw:
        return None
    text = normalize_drawing_text(raw)
    if not text:
        return None
    gdt_mode = "\x01gdt\x01" in text
    plain = text.replace("\x01gdt\x01", "")

    characteristic = _find_characteristic(text, plain, gdt_mode)
    if characteristic is None:
        return None

    compartments = [c.strip() for c in plain.split("|") if c.strip()]
    if len(compartments) > 1:
        tolerance_part = compartments[1]
        datum_parts = compartments[2:]
    else:
        # No compartment separators (typical for flattened PDF text such as
        # "⊥ 0.1 A"): split on the tolerance value instead.
        tolerance_part, datum_parts = _split_flat_frame(plain)

    diametral = any(ch in tolerance_part for ch in DIAMETER_CHARS) or (
        gdt_mode and re.search(r"(?<![A-Za-z])m(?=\s*\d)", tolerance_part) is not None
    )
    value_match = re.search(_NUM, tolerance_part)
    value = to_float(value_match.group(0)) if value_match else None
    mc = _material_condition(tolerance_part, gdt_mode, after_value=True)

    datums: list[tuple[str, MaterialCondition]] = []
    for part in datum_parts:
        label = re.search(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])", part)
        if not label:
            continue
        datums.append((label.group(1), _material_condition(part, gdt_mode, after_value=False)))

    return ParsedFCF(
        characteristic=characteristic,
        value=value,
        diametral_zone=diametral,
        material_condition=mc,
        datums=datums,
        raw=text,
    )


def _split_flat_frame(plain: str) -> tuple[str, list[str]]:
    """Split a frame without ``|`` separators into tolerance and datum parts."""
    value_match = re.search(_NUM, plain)
    if value_match is None:
        return plain, []
    head = plain[: value_match.end()]
    tail = plain[value_match.end():]
    # A material modifier directly after the value still belongs to the zone.
    modifier = re.match(r"\s*(Ⓜ|Ⓛ|\(?[MLSmls]\)?)(?![A-Za-z])", tail)
    if modifier:
        head += modifier.group(0)
        tail = tail[modifier.end():]
    datums = [tok for tok in re.split(r"[\s,|]+", tail) if tok]
    return head, datums


def _find_characteristic(
    text: str, plain: str, gdt_mode: bool
) -> GDTCharacteristic | None:
    for symbol, characteristic in GDT_UNICODE.items():
        if symbol in plain:
            return characteristic
    lowered = plain.lower()
    for keyword, characteristic in GDT_KEYWORDS.items():
        if keyword in lowered:
            return characteristic
    if gdt_mode:
        # The letter right after a GD&T font switch encodes the symbol.
        marker = re.search(r"\x01gdt\x01\s*([a-z])", text)
        if marker:
            return GDT_FONT_LETTERS.get(marker.group(1))
    return None


def _material_condition(
    part: str, gdt_mode: bool, *, after_value: bool
) -> MaterialCondition:
    for token, condition in _MATERIAL_CONDITIONS.items():
        if len(token) > 1 and token in part:
            return condition
    if "Ⓜ" in part:
        return MaterialCondition.MMC
    if "Ⓛ" in part:
        return MaterialCondition.LMC
    if gdt_mode:
        tail = re.search(r"\d\s*([mlsMLS])\b", part) if after_value else re.search(
            r"[A-Z]\s*([mlsMLS])\b", part
        )
        if tail:
            return _MATERIAL_CONDITIONS.get(tail.group(1).lower(), MaterialCondition.RFS)
    return MaterialCondition.RFS


_DATUM_RE = re.compile(r"^\s*(?:-\s*([A-Z])\s*-|\[\s*([A-Z])\s*\]|([A-Z]))\s*$")


def parse_datum_feature(raw: str | None) -> str | None:
    """Recognise a datum feature symbol such as ``-A-`` or ``[B]``."""
    text = normalize_drawing_text(raw).replace("\x01gdt\x01", "").strip()
    if not text or len(text) > 5:
        return None
    match = _DATUM_RE.match(text)
    if not match:
        return None
    return next(group for group in match.groups() if group)


# --------------------------------------------------------------------------
# Surface finish / weld symbols
# --------------------------------------------------------------------------
@dataclass
class ParsedSurfaceFinish:
    symbol_kind: SurfaceSymbolKind
    ra: float | None
    rz: float | None
    process: str | None
    raw: str


_RA_RE = re.compile(rf"(?<![A-Za-z])Ra\s*=?\s*({_NUM})", re.IGNORECASE)
_RZ_RE = re.compile(rf"(?<![A-Za-z])Rz\s*=?\s*({_NUM})", re.IGNORECASE)
_SURFACE_GLYPHS = ("√", "∇", "⊽")
#: "Ra"/"Rz" only count as roughness parameters when they stand alone - as a
#: substring they hide inside ordinary words ("GENEL TOLE**RA**NSLAR").
_SURFACE_PARAM_RE = re.compile(r"(?<![A-Za-z])R[az](?![A-Za-z])", re.IGNORECASE)


def parse_surface_finish(raw: str | None) -> ParsedSurfaceFinish | None:
    text = normalize_drawing_text(raw).replace("\x01gdt\x01", "")
    if not text:
        return None
    if not (
        any(glyph in text for glyph in _SURFACE_GLYPHS) or _SURFACE_PARAM_RE.search(text)
    ):
        return None
    ra_match = _RA_RE.search(text)
    rz_match = _RZ_RE.search(text)
    ra = to_float(ra_match.group(1)) if ra_match else None
    rz = to_float(rz_match.group(1)) if rz_match else None
    if ra is None and rz is None and "√" in text:
        bare = re.search(rf"√\s*({_NUM})", text)
        ra = to_float(bare.group(1)) if bare else None
    kind = SurfaceSymbolKind.BASIC
    if "⊽" in text or re.search(r"\bmachin", text, re.IGNORECASE):
        kind = SurfaceSymbolKind.MACHINING_REQUIRED
    elif re.search(r"\b(as cast|as forged|talaş kald[ıi]r[ıi]lmayacak)\b", text, re.IGNORECASE):
        kind = SurfaceSymbolKind.MACHINING_PROHIBITED
    process = None
    process_match = re.search(
        r"\b(milled|turned|ground|honed|lapped|EDM|frezelenmiş|tornalanmış|taşlanmış)\b",
        text,
        re.IGNORECASE,
    )
    if process_match:
        process = process_match.group(1)
    return ParsedSurfaceFinish(symbol_kind=kind, ra=ra, rz=rz, process=process, raw=text)


@dataclass
class ParsedWeld:
    weld_type: str | None
    size: float | None
    length: float | None
    pitch: float | None
    all_around: bool
    field_weld: bool
    raw: str


#: ISO 2553 weld size: the letter and the value are written together ("a5",
#: "z6-50x100"). Allowing a space would swallow captions such as "DETAIL A 2".
_WELD_RE = re.compile(
    r"(?<![A-Za-z])([az])(\d+(?:[.,]\d+)?)(?:\s*-\s*(\d+)\s*[x×]\s*(\d+))?",
    re.IGNORECASE,
)
_WELD_WORDS = re.compile(
    r"\b(fillet|kaynak|weld|butt|square|bevel|köşe|alın)\b", re.IGNORECASE
)


def parse_weld_symbol(raw: str | None) -> ParsedWeld | None:
    text = normalize_drawing_text(raw).replace("\x01gdt\x01", "")
    if not text:
        return None
    match = _WELD_RE.search(text)
    words = _WELD_WORDS.search(text)
    if match is None and words is None:
        return None
    size = to_float(match.group(2)) if match else None
    length = to_float(match.group(3)) if match and match.group(3) else None
    pitch = to_float(match.group(4)) if match and match.group(4) else None
    weld_type = None
    if match:
        weld_type = "throat" if match.group(1).lower() == "a" else "leg"
    elif words:
        weld_type = words.group(1).lower()
    return ParsedWeld(
        weld_type=weld_type,
        size=size,
        length=length,
        pitch=pitch,
        all_around="○" in text or "all around" in text.lower(),
        field_weld="⚑" in text or "field" in text.lower(),
        raw=text,
    )


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------
#: A named standard is the informative answer; the generic phrase is the
#: fallback when a drawing only says "general tolerances" without a class.
_GENERAL_TOLERANCE_STANDARD_RE = re.compile(
    r"(ISO\s*2768\s*[-–]?\s*[a-zA-Z]{0,2}|DIN\s*7168\s*[-–]?\s*[a-zA-Z]{0,2}|"
    r"EN\s*22768\s*[-–]?\s*[a-zA-Z]{0,2}|TS\s*1990|ASME\s*Y14\.5)",
    re.IGNORECASE,
)
_GENERAL_TOLERANCE_PHRASE_RE = re.compile(
    r"((?:GENEL|GENERAL)\s+TOLERANS(?:LAR)?|GENERAL\s+TOLERANCES?)",
    re.IGNORECASE,
)
_REVISION_RE = re.compile(r"\b(REV(?:ISION)?|REVIZYON|REVİZYON)\b", re.IGNORECASE)
_PROJECTION_RE = re.compile(
    r"\b(FIRST\s*ANGLE|THIRD\s*ANGLE|1\.?\s*A[ÇC]I|3\.?\s*A[ÇC]I|E\s*METHOD|A\s*METHOD)\b",
    re.IGNORECASE,
)
_UNITS_NOTE_RE = re.compile(
    r"\b(ALL\s+DIMENSIONS?\s+IN\s+(MM|MILLIMET(?:RE|ER)S?|INCH(?:ES)?)|"
    r"(?:T[ÜU]M\s+)?[ÖO]L[ÇC][ÜU]LER\s+(MM|MİLİMETRE|MILIMETRE)"
    r"(?:\s+CİNSİNDENDİR|\s+CINSINDENDIR)?)\b",
    re.IGNORECASE,
)


def detect_general_tolerance(text: str | None) -> str | None:
    """Return the general tolerance class quoted in ``text``, if any.

    A named standard ("ISO 2768-mK") always wins over the bare phrase
    ("GENEL TOLERANSLAR"), whichever comes first in the text.
    """
    if not text:
        return None
    normalized = normalize_drawing_text(text)
    for pattern in (_GENERAL_TOLERANCE_STANDARD_RE, _GENERAL_TOLERANCE_PHRASE_RE):
        match = pattern.search(normalized)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def classify_annotation(text: str | None) -> str:
    normalized = normalize_drawing_text(text)
    if not normalized:
        return "note"
    if detect_general_tolerance(normalized):
        return "general_tolerance"
    if _REVISION_RE.search(normalized):
        return "revision"
    if _PROJECTION_RE.search(normalized):
        return "projection"
    if _UNITS_NOTE_RE.search(normalized):
        return "units"
    return "note"


def detect_units_note(text: str | None) -> Units | None:
    if not text:
        return None
    match = _UNITS_NOTE_RE.search(normalize_drawing_text(text))
    if not match:
        return None
    blob = match.group(0).upper()
    if "INCH" in blob:
        return Units.INCH
    return Units.MM


# --------------------------------------------------------------------------
# Blanket notes
# --------------------------------------------------------------------------
#: Categories a blanket note can cover.
BLANKET_RADII = "radii"
BLANKET_CHAMFERS = "chamfers"
BLANKET_GENERAL = "general"

_BLANKET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        BLANKET_RADII,
        re.compile(
            r"(?:T[ÜU]M|B[ÜU]T[ÜU]N|BEL[İI]RT[İI]LMEYEN|ALL|UNSPECIFIED|UNMARKED)\s+"
            r"(?:RADY[ÜU]S|YAR[İI][ÇC]AP|RADI[İI]|RADII|RADIUS(?:ES)?|FILLETS?|KÖ[ŞS]E)",
            re.IGNORECASE,
        ),
    ),
    (
        BLANKET_CHAMFERS,
        re.compile(
            r"(?:T[ÜU]M|B[ÜU]T[ÜU]N|BEL[İI]RT[İI]LMEYEN|ALL|UNSPECIFIED)\s+(?:PAH|CHAMFERS?)"
            r"|BREAK\s+(?:ALL\s+)?(?:SHARP\s+)?EDGES"
            r"|KESK[İI]N\s+K[ÖO][ŞS]ELER",
            re.IGNORECASE,
        ),
    ),
    (
        BLANKET_GENERAL,
        re.compile(
            r"UNLESS\s+OTHERWISE\s+(?:SPECIFIED|STATED|NOTED)"
            r"|AKS[İI]\s+BEL[İI]RT[İI]LMED[İI][ĞG][İI]\s+(?:S[ÜU]RECE|DURUMDA|TAKD[İI]RDE)"
            r"|BEL[İI]RT[İI]LMED[İI][ĞG][İI]\s+S[ÜU]RECE",
            re.IGNORECASE,
        ),
    ),
)


def detect_blanket_notes(text: str | None) -> frozenset[str]:
    """Categories covered by blanket notes such as "ALL FILLETS R3".

    A drawing may legitimately leave individual callouts off when a note
    already covers them; the checkers use this to stay quiet instead of
    reporting a defect the draughtsman deliberately avoided.
    """
    if not text:
        return frozenset()
    normalized = normalize_drawing_text(text)
    return frozenset(
        category for category, pattern in _BLANKET_PATTERNS if pattern.search(normalized)
    )


# --------------------------------------------------------------------------
# Reference integrity: captions, markers, note and sheet pointers
# --------------------------------------------------------------------------
#: "KESİT A-A", "SECTION A-A (2:1)", "DETAY B", "VIEW C".
_CAPTION_RE = re.compile(
    r"^\s*(?P<kind>KES[İI]T|SECTION|DETAY|DETAIL|G[ÖO]R[ÜU]N[ÜU][ŞS]|GORUNUS|VIEW)\s*"
    r"(?P<label>[A-Z])\s*(?:[-–—]\s*(?P<second>[A-Z]))?"
    # A title ends after its letter, give or take a scale.  Anything else
    # ("DETAY C'YE BAKINIZ") is prose pointing at a view, not the view's title.
    r"(?:\s*(?:SCALE|[ÖO]L[ÇC]EK)?\s*\(?\s*(?P<scale>\d{1,3}\s*:\s*\d{1,3})\s*\)?)?\s*$",
    re.IGNORECASE,
)

#: A bare cutting-plane letter pair: "A-A", "B–B".  A *single* letter is not
#: matched here: on its own it is indistinguishable from a datum symbol, so a
#: detail bubble is recognised from its geometry instead (rules/markers.py).
_MARKER_PAIR_RE = re.compile(r"^\s*([A-Z])\s*[-–—]\s*([A-Z])\s*$")

#: A bare single letter, the text half of a detail bubble.
_MARKER_SINGLE_RE = re.compile(r"^\s*([A-Z])\s*$")

#: "1. KESKİN KÖŞELER KIRILACAK" - a numbered note being *declared*.
_NOTE_DECLARATION_RE = re.compile(r"(?m)^\s*(\d{1,2})\s*[.)\-]\s+\S")

#: "BKZ NOT 3", "NOT 3'E BAKINIZ", "SEE NOTE 3", "NOTE 3".
_NOTE_REFERENCE_RE = re.compile(
    r"(?:BKZ\.?|BAKINIZ|BAK|SEE|PER|REF\.?)?\s*"
    r"(?:NOT|NOTE)\s*(?:NO\.?|NR\.?)?\s*(\d{1,2})\b",
    re.IGNORECASE,
)

#: "DETAY SAYFA 2", "SEE SHEET 3".  A sheet *number* written as "1/2" is the
#: title block stating which sheet this is, not a pointer to another one.
_SHEET_REFERENCE_RE = re.compile(
    r"\b(?:SAYFA|SHEET|SH)\.?\s*(\d{1,2})\b(?!\s*[/\\])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedCaption:
    """A view title: what kind of view it is and which letter it answers to."""

    kind: str  # "section" | "detail" | "view"
    label: str  # "A-A" for a section, "B" for a detail
    scale: str | None = None


def parse_view_caption(raw: str | None) -> ParsedCaption | None:
    """Read 'KESİT A-A (2:1)' into its kind, its letter and its scale."""
    text = normalize_drawing_text(raw)
    if not text:
        return None
    match = _CAPTION_RE.match(text)
    if not match:
        return None
    word = match.group("kind").upper()
    if word.startswith(("KES", "SEC")):
        kind = "section"
    elif word.startswith(("DETA", "DETAI")):
        kind = "detail"
    else:
        kind = "view"
    label = match.group("label").upper()
    second = match.group("second")
    if second:
        label = f"{label}-{second.upper()}"
    elif kind == "section":
        # A section is cut along a plane marked at both ends; "SECTION A" is
        # the same reference as "SECTION A-A" and has to match the marker.
        label = f"{label}-{label}"
    scale = match.group("scale")
    return ParsedCaption(kind=kind, label=label, scale=re.sub(r"\s+", "", scale) if scale else None)


def parse_marker_label(raw: str | None) -> str | None:
    """The letter pair of a cutting-plane marker ('A-A'), or ``None``."""
    text = normalize_drawing_text(raw)
    if not text:
        return None
    match = _MARKER_PAIR_RE.match(text)
    if not match:
        return None
    return f"{match.group(1).upper()}-{match.group(2).upper()}"


def parse_bubble_label(raw: str | None) -> str | None:
    """The single letter of a detail bubble ('B'), or ``None``.

    Only meaningful together with the geometry it sits on: on its own a bare
    letter is also how a datum feature symbol is written.
    """
    text = normalize_drawing_text(raw)
    if not text:
        return None
    match = _MARKER_SINGLE_RE.match(text)
    return match.group(1).upper() if match else None


def declared_notes(text: str | None) -> set[int]:
    """The numbers of the notes this sheet writes out."""
    normalized = normalize_drawing_text(text)
    if not normalized:
        return set()
    return {int(number) for number in _NOTE_DECLARATION_RE.findall(normalized)}


def referenced_notes(text: str | None) -> set[int]:
    """The note numbers this sheet points at ('BKZ NOT 3')."""
    normalized = normalize_drawing_text(text)
    if not normalized:
        return set()
    return {int(number) for number in _NOTE_REFERENCE_RE.findall(normalized)}


def referenced_sheets(text: str | None) -> set[int]:
    """The sheet numbers this sheet points at ('DETAY SAYFA 2')."""
    normalized = normalize_drawing_text(text)
    if not normalized:
        return set()
    return {int(number) for number in _SHEET_REFERENCE_RE.findall(normalized)}


#: A view named *inside* running text ("YÜZEY İÇİN BKZ KESİT A-A").  Unlike
#: :func:`parse_view_caption` this searches rather than anchors: a title starts
#: with the word, a mention does not.
_VIEW_MENTION_RE = re.compile(
    r"\b(?P<kind>KES[İI]T|SECTION|DETAY|DETAIL|G[ÖO]R[ÜU]N[ÜU][ŞS]|GORUNUS|VIEW)\s*"
    r"(?P<label>[A-Z])(?:\s*[-–—]\s*(?P<second>[A-Z]))?\b",
    re.IGNORECASE,
)


def view_references(raw: str | None) -> set[tuple[str, str]]:
    """``{(kind, label)}`` for every view this text points at.

    A text that *is* a view title declares one instead of pointing at one, so
    those are left to :func:`parse_view_caption` and reported as empty here.
    """
    text = normalize_drawing_text(raw)
    if not text or parse_view_caption(text) is not None:
        return set()
    out: set[tuple[str, str]] = set()
    for match in _VIEW_MENTION_RE.finditer(text):
        word = match.group("kind").upper()
        if word.startswith(("KES", "SEC")):
            kind = "section"
        elif word.startswith(("DETA", "DETAI")):
            kind = "detail"
        else:
            kind = "view"
        label = match.group("label").upper()
        second = match.group("second")
        if second:
            label = f"{label}-{second.upper()}"
        elif kind == "section":
            label = f"{label}-{label}"
        out.add((kind, label))
    return out
