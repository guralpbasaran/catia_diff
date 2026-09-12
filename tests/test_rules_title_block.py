from pathlib import Path

from conftest import (
    make_context,
    make_dimension,
    make_document,
    make_sheet,
    titled,
)

from catia_diff.models.drawing import Annotation, ProjectionMethod, SourceFormat, TitleBlock, Units
from catia_diff.models.findings import Severity
from catia_diff.rules.base import get_rule


def run(rule_id: str, sheet=None, document=None, config=None):
    document = document or make_document(sheet or make_sheet())
    ctx = make_context(document, config)
    rule = get_rule(rule_id)
    target = document if rule.scope == "document" else document.sheets[0]
    return list(rule.check(target, ctx))


def full_block(**overrides):
    values = {
        "drawing_number": "TD-1001",
        "title": "BRACKET",
        "scale": "1:2",
        "material": "S235JR",
        "revision": "A",
        "drawn_by": "A. YILMAZ",
        "approved_by": "B. KAYA",
        "sheet": "1 / 1",
        "company": "ACME",
    }
    values.update(overrides)
    return titled(**values)


def test_tb001_missing_title_block():
    sheet = make_sheet(dimensions=[make_dimension()], title_block=TitleBlock())
    findings = run("TB001", sheet)
    assert len(findings) == 1 and findings[0].severity is Severity.CRITICAL


def test_tb001_silent_on_an_empty_sheet():
    assert run("TB001", make_sheet()) == []


def test_tb002_reports_each_missing_mandatory_field():
    sheet = make_sheet(title_block=titled(drawing_number="TD-1001", title="BRACKET"))
    findings = run("TB002", sheet)
    ids = {f.evidence.object_ids[0] for f in findings}
    assert "titleblock.material" in ids
    assert "titleblock.scale" in ids
    assert all(f.severity is not Severity.CRITICAL for f in findings)


def test_tb002_quiet_when_the_block_is_complete():
    assert run("TB002", make_sheet(title_block=full_block())) == []


def test_tb003_placeholder_values():
    sheet = make_sheet(title_block=full_block(material="TBD"))
    findings = run("TB003", sheet)
    assert len(findings) == 1 and "TBD" in findings[0].message


def test_tb004_scale_format():
    assert run("TB004", make_sheet(title_block=full_block(scale="yarim"))) != []
    assert run("TB004", make_sheet(title_block=full_block(scale="1:2.5"))) == []
    assert run("TB004", make_sheet(title_block=full_block(scale="NTS"))) == []


def test_tb005_revision_without_date():
    assert len(run("TB005", make_sheet(title_block=full_block(revision="B")))) == 1
    dated = full_block(revision="B", revision_date="2026-01-04")
    assert run("TB005", make_sheet(title_block=dated)) == []
    assert run("TB005", make_sheet(title_block=full_block(revision="0"))) == []


def test_tb006_same_person_draws_and_approves():
    same = full_block(drawn_by="G. BASARAN", approved_by="g. basaran")
    assert len(run("TB006", make_sheet(title_block=same))) == 1


def test_tb007_projection_method():
    sheet = make_sheet(
        title_block=full_block(),
        dimensions=[make_dimension(f"DIM{i}") for i in range(4)],
    )
    assert len(run("TB007", sheet)) == 1
    sheet.projection = ProjectionMethod.FIRST_ANGLE
    assert run("TB007", sheet) == []


def test_tb008_general_tolerance_note():
    sheet = make_sheet(title_block=full_block(), dimensions=[make_dimension()])
    assert len(run("TB008", sheet)) == 1

    with_note = make_sheet(
        title_block=full_block(),
        dimensions=[make_dimension()],
        annotations=[Annotation(id="N1", text="ISO 2768-mK", category="general_tolerance")],
    )
    assert run("TB008", with_note) == []


def test_tb009_units_not_stated():
    sheet = make_sheet(title_block=full_block(), dimensions=[make_dimension()])
    assert len(run("TB009", sheet)) == 1

    stated = make_sheet(title_block=full_block(units="mm"), dimensions=[make_dimension()])
    assert run("TB009", stated) == []


def test_tb010_sheet_numbering():
    first = make_sheet(index=0, title_block=full_block(sheet="1 / 3"))
    second = make_sheet(index=1, title_block=full_block(sheet="1 / 1"))
    document = make_document(first)
    document.sheets.append(second)
    findings = run("TB010", document=document)
    messages = " ".join(f.message for f in findings)
    assert "2 sheets" in messages or "declares" in messages
    assert len(findings) >= 2


def test_tb011_drawing_number_vs_filename():
    matching = make_document(make_sheet(title_block=full_block(drawing_number="TD-1001")))
    matching.source_path = Path("TD-1001_rev_a.dxf")
    assert run("TB011", document=matching) == []

    mismatch = make_document(make_sheet(title_block=full_block(drawing_number="TD-9999")))
    mismatch.source_path = Path("unrelated.dxf")
    assert len(run("TB011", document=mismatch)) == 1


def test_con001_mixed_units():
    sheet = make_sheet(
        dimensions=[
            make_dimension("DIM1", units=Units.MM),
            make_dimension("DIM2", units=Units.INCH),
        ]
    )
    findings = run("CON001", sheet)
    assert len(findings) == 1 and findings[0].severity is Severity.CRITICAL


def test_con002_scale_mismatch_only_for_vector_sources():
    dims = []
    for index in range(4):
        dim = make_dimension(f"DIM{index}", nominal=40.0)
        dim.measured = 20.0
        dims.append(dim)
    sheet = make_sheet(dimensions=dims, stated_scale="1:2", scale=0.5)
    assert len(run("CON002", sheet)) == 1

    document = make_document(sheet)
    document.source_format = SourceFormat.PDF_VECTOR
    assert run("CON002", document=document) == []


def test_con004_revision_mentioned_but_not_recorded():
    sheet = make_sheet(
        title_block=titled(drawing_number="TD-1001"),
        annotations=[Annotation(id="N1", text="REV B - ölçü güncellendi", category="revision")],
    )
    assert len(run("CON004", sheet)) == 1


def test_con005_degraded_extraction_when_vision_is_off():
    sheet = make_sheet(needs_vision=True)
    findings = run("CON005", sheet)
    assert len(findings) == 1

    document = make_document(make_sheet(needs_vision=True))
    document.vision_used = True
    assert run("CON005", document=document) == []


def test_con006_empty_document():
    assert len(run("CON006", make_sheet())) == 1
