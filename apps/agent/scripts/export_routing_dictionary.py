"""Export the runtime scene dictionary from the scenario workbook.

The workbook is the editing source; the checked-in scenes.json is the read-only
runtime copy. Only the scene directory and default features are exported: question
gold answers, expected models and evaluator notes never reach the Agent runtime.

Usage:
    uv run --locked python scripts/export_routing_dictionary.py --check
    uv run --locked python scripts/export_routing_dictionary.py --workbook <xlsx>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

COLUMNS = {
    "scene_id": ("场景编号", "scene_id"),
    "category_id": ("一级分类", "category_id"),
    "category_name": ("一级分类名称", "category_name"),
    "name": ("场景名称", "name"),
    "description": ("场景描述", "description"),
    "task_type": ("任务类型", "task_type"),
    "difficulty": ("难度", "difficulty"),
    "response_mode": ("响应方式", "response_mode"),
    "required_capabilities": ("必需能力", "required_capabilities"),
}
DEFAULT_OUTPUT = Path(__file__).parent.parent / "src" / "compass_agent" / "routing" / "scenes.json"


def digest(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def normalise(rows: list[dict]) -> dict:
    categories: dict[str, str] = {}
    scenes: list[dict] = []
    for row in rows:
        scene_id = str(row["scene_id"]).strip()
        category_id = str(row["category_id"]).strip()
        category_name = str(row["category_name"]).strip()
        if not scene_id or not category_id or not category_name:
            raise ValueError("scenario id, category id and category name are required")
        if categories.setdefault(category_id, category_name) != category_name:
            raise ValueError(f"category name mismatch for {category_id}")
        capabilities = row.get("required_capabilities") or []
        if isinstance(capabilities, str):
            capabilities = [item.strip() for item in capabilities.split(",") if item.strip()]
        scenes.append(
            {
                "scene_id": scene_id,
                "category_id": category_id,
                "name": str(row["name"]).strip(),
                "description": str(row["description"]).strip(),
                "task_type": str(row["task_type"]).strip(),
                "difficulty": str(row["difficulty"]).strip(),
                "response_mode": str(row["response_mode"]).strip(),
                "required_capabilities": list(capabilities),
            }
        )
    if len(scenes) != 40:
        raise ValueError(f"the workbook must contain 40 scenes, found {len(scenes)}")
    if len({row["scene_id"] for row in scenes}) != len(scenes):
        raise ValueError("duplicate scene ids")
    payload = {
        "categories": [{"category_id": key, "name": value} for key, value in categories.items()],
        "scenes": scenes,
    }
    return {
        "version": "sha256:" + digest(payload),
        "source": "任务场景与分流标准答案（工作簿派生，仅含场景与默认特征）",
        **payload,
    }


def _column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    index = 0
    for character in letters:
        index = index * 26 + (ord(character.upper()) - 64)
    return max(0, index - 1)


def read_xlsx(path: Path, sheet_name: str = "场景目录") -> list[list]:
    """Read one worksheet with the standard library only (no openpyxl dependency)."""

    import zipfile
    from xml.etree import ElementTree

    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.findall(f".//{{{main}}}sheet")
        selected = next((row for row in sheets if row.get("name") == sheet_name), sheets[0])
        relationship = selected.get(f"{{{rel}}}id")
        links = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(row.get("Target") for row in links if row.get("Id") == relationship)
        if target is None:
            raise ValueError("worksheet relationship is missing")
        target = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in strings.findall(f"{{{main}}}si"):
                shared.append("".join(text.text or "" for text in item.findall(f".//{{{main}}}t")))
        sheet = ElementTree.fromstring(archive.read(target))
        rows: list[list] = []
        for row in sheet.findall(f".//{{{main}}}sheetData/{{{main}}}row"):
            cells: dict[int, object] = {}
            for cell in row.findall(f"{{{main}}}c"):
                reference = cell.get("r") or "A1"
                kind = cell.get("t")
                value = cell.find(f"{{{main}}}v")
                if kind == "s" and value is not None and value.text is not None:
                    cells[_column_index(reference)] = shared[int(value.text)]
                elif kind == "inlineStr":
                    cells[_column_index(reference)] = "".join(
                        text.text or "" for text in cell.findall(f".//{{{main}}}t")
                    )
                else:
                    cells[_column_index(reference)] = value.text if value is not None else None
            rows.append([cells.get(index) for index in range(max(cells) + 1)] if cells else [])
        return rows


def read_workbook(path: Path) -> dict:
    values = iter(read_xlsx(path))
    header = [str(cell).strip() if cell is not None else "" for cell in next(values)]
    indexes: dict[str, int] = {}
    for field, aliases in COLUMNS.items():
        for alias in aliases:
            if alias in header:
                indexes[field] = header.index(alias)
                break
        else:
            raise ValueError(f"missing workbook column: {aliases[0]}")
    rows: list[dict] = []
    for line in values:
        if not line or line[indexes["scene_id"]] is None:
            continue
        rows.append({field: line[index] for field, index in indexes.items()})
    return normalise(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, help="scenario workbook (.xlsx)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the checked-in runtime dictionary without a workbook",
    )
    args = parser.parse_args()

    if args.workbook is not None:
        payload = read_workbook(args.workbook)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.output} ({len(payload['scenes'])} scenes, {payload['version']})")
        return

    from compass_agent.routing.dictionary import catalog

    version, scenes = catalog()
    categories = {row["category_id"] for row in scenes.values()}
    print(f"version={version} scenes={len(scenes)} categories={len(categories)}")
    if args.check:
        print("routing dictionary is consistent")


if __name__ == "__main__":
    main()
