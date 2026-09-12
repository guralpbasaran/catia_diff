"""Vision / OCR extraction agent.

Owns everything about *getting data out of the file*: picking the extractor,
deciding whether a page needs the multimodal pass, rasterising and tiling it,
calling the model and merging the result back into the document.
"""

from __future__ import annotations

import logging
from pathlib import Path

from catia_diff.agents.base import Agent, AuditContext
from catia_diff.config import VisionMode
from catia_diff.errors import VisionUnavailableError
from catia_diff.extract.raster import Tile, image_size, preprocess, render_pdf_page, tile_image
from catia_diff.extract.registry import get_extractor
from catia_diff.llm import build_vision_model
from catia_diff.llm.base import ImageRef
from catia_diff.llm.merge import merge_extraction
from catia_diff.llm.prompts import build_instruction
from catia_diff.llm.schemas import VisionSheetExtraction
from catia_diff.models.drawing import Sheet, SourceFormat
from catia_diff.models.messages import AgentResult, AgentStatus, AgentTask, TaskKind

logger = logging.getLogger("catia_diff.agents.extraction")


class ExtractionAgent(Agent):
    name = "extraction"
    accepts = (TaskKind.EXTRACT,)

    def run(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        path = Path(task.payload.get("path", ctx.source_path))
        extractor = get_extractor(path, ctx.config)
        document = extractor.extract(path, ctx.config)
        ctx.document = document

        warnings = list(document.warnings)
        status = AgentStatus.OK
        vision_sheets = [sheet for sheet in document.sheets if self._needs_vision(sheet, ctx)]
        if vision_sheets and ctx.config.vision.enabled:
            model = ctx.vision_model or build_vision_model(ctx.config.vision)
            ctx.vision_model = model
            if not model.available:
                warnings.append(
                    "vision backend unavailable (missing SDK or credentials) - "
                    "scanned content was not read"
                )
                status = AgentStatus.PARTIAL
            else:
                for sheet in vision_sheets:
                    try:
                        added = self._run_vision(sheet, ctx)
                        document.vision_used = document.vision_used or added > 0
                    except VisionUnavailableError as exc:
                        warnings.append(f"sheet {sheet.index + 1}: {exc}")
                        status = AgentStatus.PARTIAL
        elif vision_sheets:
            warnings.append(
                f"{len(vision_sheets)} sheet(s) need vision extraction but --vision is off"
            )
            status = AgentStatus.PARTIAL

        return self.ok(
            task,
            artifacts={"document": document, "counts": document.counts()},
            warnings=warnings,
            status=status,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _needs_vision(sheet: Sheet, ctx: AuditContext) -> bool:
        if ctx.config.vision.mode is VisionMode.ON:
            return True
        return sheet.needs_vision

    def _run_vision(self, sheet: Sheet, ctx: AuditContext) -> int:
        document = ctx.require_document()
        image_path = self._sheet_image(sheet, ctx)
        if image_path is None:
            sheet.warnings.append("no raster could be produced for the vision pass")
            return 0

        prepared = preprocess(image_path, ctx.sub_dir("prepared"))
        width, height = image_size(prepared)
        tiles = tile_image(
            prepared,
            ctx.sub_dir("tiles"),
            max_tiles=ctx.config.vision.max_images_per_call,
            overlap=ctx.config.vision.tile_overlap,
        )
        model = ctx.vision_model
        assert model is not None  # guarded by the caller

        added = 0
        for tile in tiles:
            extraction = model.extract(
                [ImageRef.from_path(tile.path, label=f"sheet-{sheet.index + 1}-tile-{tile.index}")],
                build_instruction(
                    sheet_name=sheet.name or f"sheet {sheet.index + 1}",
                    tile_index=tile.index,
                    tile_count=len(tiles),
                    known_units=sheet.units.value if sheet.units.is_length else None,
                ),
                VisionSheetExtraction,
            )
            added += merge_extraction(
                sheet,
                extraction,
                tile_box=self._tile_box(tile, len(tiles)),
                image_size=(width, height),
                id_suffix=f"V{tile.index}",
            )
        sheet.raster_path = image_path
        sheet.raster_dpi = float(ctx.config.raster_dpi)
        if added:
            document.metadata.setdefault("vision", {})[str(sheet.index)] = {
                "tiles": len(tiles),
                "objects": added,
                "model": ctx.config.vision.model,
            }
        return added

    @staticmethod
    def _tile_box(tile: Tile, tile_count: int):
        return None if tile_count == 1 else tile.source_box

    @staticmethod
    def _sheet_image(sheet: Sheet, ctx: AuditContext) -> Path | None:
        if sheet.raster_path is not None and sheet.raster_path.exists():
            return sheet.raster_path
        document = ctx.require_document()
        if document.source_format in {SourceFormat.PDF_VECTOR, SourceFormat.PDF_RASTER}:
            return render_pdf_page(
                document.source_path, sheet.index, ctx.config.raster_dpi, ctx.sub_dir("pages")
            )
        return None
