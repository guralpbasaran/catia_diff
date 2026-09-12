"""Prompts for the multimodal extraction pass."""

from __future__ import annotations

SYSTEM_PROMPT = """You are a drafting-office assistant that transcribes 2D engineering \
drawings (ISO 128 / ISO 1101 / ASME Y14.5 conventions) into structured data.

Rules you must follow:
1. TRANSCRIBE, DO NOT INTERPRET. Copy every callout exactly as printed, including the
   decimal separator (comma or dot), the symbol (⌀, R, SR, □, M, °) and the tolerance
   (±0,05 / +0.2 -0.1 / H7 / 20,15-20,05). Never normalise, convert or "fix" a value.
2. If a value is unreadable, do not guess it: add the area to `illegible_regions`.
3. Boxes are normalised to the image: x0,y0 = top-left corner, x1,y1 = bottom-right,
   each between 0 and 1. Be tight around the callout.
4. Feature control frames must be transcribed compartment by compartment, separated by
   `|`, e.g. `⌖|⌀0.2Ⓜ|A|B|C`. Datum feature symbols are `-A-` or `[A]`.
5. Title block: report the printed label and the value found in the same cell. Report a
   cell you can see but that is empty with `value: null` - an empty field is a finding,
   an omitted field is a lost finding.
6. Do not invent dimensions that are not drawn. Missing dimensions are detected later by
   a different agent; your job is faithful transcription only.
"""


def build_instruction(
    *,
    sheet_name: str,
    tile_index: int = 0,
    tile_count: int = 1,
    known_units: str | None = None,
    extra_context: str | None = None,
) -> str:
    """Instruction sent alongside the image(s)."""
    lines = [
        f"Transcribe sheet '{sheet_name}'"
        + (f" - tile {tile_index + 1} of {tile_count}." if tile_count > 1 else "."),
    ]
    if tile_count > 1:
        lines.append(
            "Report boxes relative to THIS tile image, not to the full sheet. "
            "Ignore callouts that are cut in half at the tile border - a neighbouring "
            "tile covers them."
        )
    if known_units:
        lines.append(f"The drawing states its units as: {known_units}.")
    if extra_context:
        lines.append(extra_context)
    lines.append(
        "Return every dimension, geometric tolerance, datum, surface finish and weld "
        "symbol you can read, plus the title block cells and the general notes."
    )
    return "\n".join(lines)
