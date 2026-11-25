import importlib.util
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from compass_agent.routing.dictionary import catalog

SCRIPT = Path(__file__).parents[1] / "scripts" / "export_routing_dictionary.py"
SPEC = importlib.util.spec_from_file_location("export_routing_dictionary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
read_workbook = getattr(MODULE, "read_workbook")  # noqa: B009

SHEET = "场景目录"
HEADERS = [
    "场景编号",
    "一级分类",
    "一级分类名称",
    "场景名称",
    "场景描述",
    "任务类型",
    "难度",
    "响应方式",
    "必需能力",
]


def build_workbook(target: Path, payload: dict) -> None:
    categories = {row["category_id"]: row["name"] for row in payload["categories"]}
    rows = [HEADERS]
    for row in payload["scenes"]:
        rows.append(
            [
                row["scene_id"],
                row["category_id"],
                categories[row["category_id"]],
                row["name"],
                row["description"],
                row["task_type"],
                row["difficulty"],
                row["response_mode"],
                ",".join(row["required_capabilities"]),
            ]
        )
    shared: list[str] = []
    index: dict[str, int] = {}
    for row in rows:
        for value in row:
            text = str(value)
            if text not in index:
                index[text] = len(shared)
                shared.append(text)
    columns = "ABCDEFGHI"
    sheet_rows = []
    for number, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{columns[position]}{number}" t="s"><v>{index[str(value)]}</v></c>'
            for position, value in enumerate(row)
        )
        sheet_rows.append(f'<row r="{number}">{cells}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{main}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{main}" count="{len(shared)}" uniqueCount="{len(shared)}">'
        + "".join(f"<si><t>{escape(value)}</t></si>" for value in shared)
        + "</sst>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{main}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{SHEET}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>'
    )
    package = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/sharedStrings.xml", strings)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def test_workbook_export_reproduces_runtime_dictionary(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "src" / "compass_agent" / "routing" / "scenes.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    workbook = tmp_path / "routing.xlsx"
    build_workbook(workbook, payload)
    exported = read_workbook(workbook)
    assert exported["version"] == catalog()[0]
    assert exported["version"] == payload["version"]
    assert exported["categories"] == payload["categories"]
    assert exported["scenes"] == payload["scenes"]
