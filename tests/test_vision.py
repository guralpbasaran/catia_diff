import json

import pytest
from conftest import make_sheet

from catia_diff.agents.base import AuditContext
from catia_diff.agents.extraction import ExtractionAgent
from catia_diff.config import AuditConfig, VisionConfig, VisionMode
from catia_diff.errors import VisionUnavailableError
from catia_diff.llm.anthropic_client import AnthropicVisionModel
from catia_diff.llm.base import ImageRef
from catia_diff.llm.merge import merge_extraction
from catia_diff.llm.mock import MockVisionModel, NullVisionModel
from catia_diff.llm.schemas import (
    VisionBox,
    VisionDimension,
    VisionSheetExtraction,
    VisionSymbol,
    VisionTitleBlockField,
)
from catia_diff.models.drawing import DimensionKind, GDTCharacteristic, ToleranceKind
from catia_diff.models.geometry import BBox
from catia_diff.models.messages import AgentStatus, AgentTask, TaskKind


def sample_extraction() -> VisionSheetExtraction:
    return VisionSheetExtraction(
        dimensions=[
            VisionDimension(
                text="⌀12,5 ±0,05", kind="diameter", box=VisionBox(x0=0.1, y0=0.1, x1=0.2, y1=0.15)
            ),
            VisionDimension(text="30°", kind="angular", box=VisionBox(x0=0.5, y0=0.5, x1=0.6, y1=0.55)),
        ],
        symbols=[
            VisionSymbol(
                kind="feature_control_frame",
                text="⌖|⌀0.2Ⓜ|A|B",
                box=VisionBox(x0=0.3, y0=0.3, x1=0.45, y1=0.35),
            ),
            VisionSymbol(kind="datum", text="-A-", box=VisionBox(x0=0.2, y0=0.7, x1=0.24, y1=0.74)),
            VisionSymbol(
                kind="surface_finish", text="Ra 3.2", box=VisionBox(x0=0.6, y0=0.2, x1=0.7, y1=0.25)
            ),
        ],
        title_block=[
            VisionTitleBlockField(
                label="ÖLÇEK", value="1:2", box=VisionBox(x0=0.8, y0=0.9, x1=0.95, y1=0.95)
            ),
            VisionTitleBlockField(
                label="MALZEME", value=None, box=VisionBox(x0=0.8, y0=0.85, x1=0.95, y1=0.9)
            ),
        ],
        observations=["title block frame is cut off at the right edge"],
    )


def test_merge_extraction_maps_everything_into_the_sheet():
    sheet = make_sheet(width=400.0, height=200.0)
    added = merge_extraction(sheet, sample_extraction())

    assert added == 7
    diameter = sheet.dimensions[0]
    assert diameter.nominal == 12.5
    assert diameter.kind is DimensionKind.DIAMETER
    assert diameter.tolerance.kind is ToleranceKind.SYMMETRIC
    assert diameter.confidence < 1.0
    assert diameter.bbox.as_tuple() == (40.0, 20.0, 80.0, 30.0)

    assert sheet.geometric_tolerances[0].characteristic is GDTCharacteristic.POSITION
    assert [d.label for d in sheet.datums] == ["A"]
    assert sheet.surface_finishes[0].ra == 3.2
    assert sheet.title_block.value("scale") == "1:2"
    assert sheet.title_block.get("material").is_empty
    assert any("cut off" in warning for warning in sheet.warnings)


def test_merge_extraction_projects_tile_boxes():
    sheet = make_sheet(width=400.0, height=200.0)
    extraction = VisionSheetExtraction(
        dimensions=[
            VisionDimension(text="20", kind="linear", box=VisionBox(x0=0.0, y0=0.0, x1=0.5, y1=1.0))
        ]
    )
    merge_extraction(
        sheet,
        extraction,
        tile_box=BBox(x0=100, y0=0, x1=200, y1=100),
        image_size=(200.0, 100.0),
    )
    # tile x [100,150] of a 200 px wide image -> [200, 300] on a 400 unit sheet
    assert sheet.dimensions[0].bbox.as_tuple() == (200.0, 0.0, 300.0, 200.0)


def test_mock_and_null_backends():
    mock = MockVisionModel([sample_extraction()])
    result = mock.extract([ImageRef("image/png", "x")], "go", VisionSheetExtraction)
    assert result.dimensions
    assert mock.calls == [(1, "go")]

    with pytest.raises(VisionUnavailableError):
        NullVisionModel().extract([], "go", VisionSheetExtraction)


# --------------------------------------------------------------- API client
class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    stop_reason = "end_turn"
    stop_details = None

    def __init__(self, payload: dict) -> None:
        self.content = [_Block(json.dumps(payload))]


class _Messages:
    def __init__(self, payload: dict, record: list) -> None:
        self._payload = payload
        self._record = record

    def create(self, **kwargs):
        self._record.append(kwargs)
        return _Response(self._payload)


class _Beta:
    def __init__(self, messages) -> None:
        self.messages = messages


class _Client:
    """Stand-in for anthropic.Anthropic whose beta path is unavailable."""

    def __init__(self, payload: dict) -> None:
        self.requests: list[dict] = []
        self.messages = _Messages(payload, self.requests)
        self.beta = _Beta(_BetaMessages())


class _BetaMessages:
    def create(self, **kwargs):
        raise TypeError("unexpected keyword argument 'fallbacks'")


def test_anthropic_client_builds_a_structured_request():
    pytest.importorskip("anthropic")
    payload = sample_extraction().model_dump(mode="json")
    client = _Client(payload)
    model = AnthropicVisionModel(VisionConfig(model="claude-opus-5", effort="high"), client=client)

    result = model.extract(
        [ImageRef("image/png", "BASE64DATA", "sheet-1")], "transcribe", VisionSheetExtraction
    )
    assert isinstance(result, VisionSheetExtraction)
    assert len(result.dimensions) == 2

    request = client.requests[0]
    assert request["model"] == "claude-opus-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["effort"] == "high"
    schema = request["output_config"]["format"]["schema"]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    image_block = request["messages"][0]["content"][0]
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["data"] == "BASE64DATA"
    assert request["messages"][0]["content"][-1]["text"] == "transcribe"


def test_anthropic_client_reports_a_refusal():
    pytest.importorskip("anthropic")

    class _Refusal(_Response):
        stop_reason = "refusal"

    class _RefusingMessages(_Messages):
        def create(self, **kwargs):
            return _Refusal({})

    client = _Client({})
    client.messages = _RefusingMessages({}, client.requests)
    model = AnthropicVisionModel(VisionConfig(), client=client)
    with pytest.raises(VisionUnavailableError, match="declined"):
        model.extract([ImageRef("image/png", "x")], "go", VisionSheetExtraction)


# ------------------------------------------------------------------- agent
def test_extraction_agent_runs_vision_for_raster_input(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "scan.png"
    Image.new("RGB", (1200, 800), "white").save(path)

    config = AuditConfig(vision=VisionConfig(mode=VisionMode.AUTO, max_images_per_call=1))
    mock = MockVisionModel([sample_extraction()])
    ctx = AuditContext(config=config, source_path=path, workdir=tmp_path / "work", vision_model=mock)

    result = ExtractionAgent().execute(AgentTask(kind=TaskKind.EXTRACT), ctx)
    assert result.status is AgentStatus.OK
    document = ctx.require_document()
    assert document.vision_used
    assert document.sheets[0].dimensions
    assert mock.calls


def test_extraction_agent_degrades_when_vision_is_off(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "scan.png"
    Image.new("RGB", (600, 400), "white").save(path)

    config = AuditConfig(vision=VisionConfig(mode=VisionMode.OFF))
    ctx = AuditContext(config=config, source_path=path, workdir=tmp_path / "work")
    result = ExtractionAgent().execute(AgentTask(kind=TaskKind.EXTRACT), ctx)

    assert result.status is AgentStatus.PARTIAL
    assert any("vision" in warning for warning in result.warnings)
    assert not ctx.require_document().vision_used
