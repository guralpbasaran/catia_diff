import json

import pytest

from catia_diff.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, main


def test_rules_command_lists_the_catalogue(capsys):
    assert main(["rules"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "DIM001" in out and "GDT005" in out and "TB001" in out
    assert out.count("\n") > 40


def test_rules_command_filters_by_category_and_language(capsys):
    main(["rules", "--category", "gdt", "--lang", "tr"])
    out = capsys.readouterr().out
    assert "GDT001" in out and "Kritik" in out
    assert "DIM001" not in out


def test_formats_command(capsys):
    assert main(["formats"]) == EXIT_OK
    out = capsys.readouterr().out
    assert ".dxf" in out and ".pdf" in out and "DWG" in out


def test_audit_exits_with_one_when_findings_remain(sample_dxf, tmp_path, capsys):
    code = main(
        [
            "audit", str(sample_dxf),
            "--lang", "en",
            "--out", str(tmp_path / "reports"),
            "--format", "json",
            "--vision", "off",
            "--no-overlay",
        ]
    )
    assert code == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "Critical" in out and "DIM004" in out

    payload = json.loads((tmp_path / "reports" / f"{sample_dxf.stem}_audit.json").read_text())
    assert payload["profile"] == "ISO"


def test_audit_can_be_told_not_to_fail(sample_dxf, tmp_path, capsys):
    code = main(
        [
            "audit", str(sample_dxf),
            "--out", str(tmp_path / "r2"),
            "--format", "",
            "--fail-on", "never",
            "--vision", "off",
            "--no-overlay",
            "--quiet",
        ]
    )
    assert code == EXIT_OK
    assert "DIM004" not in capsys.readouterr().out  # --quiet prints only the summary


def test_audit_reports_a_missing_file(tmp_path, capsys):
    assert main(["audit", str(tmp_path / "nope.dxf")]) == EXIT_ERROR
    assert "not found" in capsys.readouterr().err


def test_audit_rejects_an_unsupported_format(tmp_path, capsys):
    path = tmp_path / "model.step"
    path.write_text("ISO-10303-21;")
    assert main(["audit", str(path)]) == EXIT_ERROR
    assert "No extractor" in capsys.readouterr().err


def test_only_filter_limits_the_run(sample_dxf, tmp_path, capsys):
    main(
        [
            "audit", str(sample_dxf),
            "--lang", "en",
            "--out", str(tmp_path / "r3"),
            "--format", "json",
            "--only", "DIM001",
            "--vision", "off",
            "--no-overlay",
        ]
    )
    payload = json.loads((tmp_path / "r3" / f"{sample_dxf.stem}_audit.json").read_text())
    assert {f["rule_id"] for f in payload["findings"]} == {"DIM001"}


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "catia-diff" in capsys.readouterr().out
