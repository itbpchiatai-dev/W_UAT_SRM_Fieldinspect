"""Small XLSX writer for server-generated templates.

The app only needs simple worksheet exports here, so this avoids adding a
spreadsheet parser/writer dependency for a download-only template.

Round 8-6A — additive styled-cell support (CellStyle/StyledCell) so a
generated workbook can carry per-cell background/font styling (e.g. a yellow
"please check/edit" column, a red "example only" row) without touching the
plain `CellValue` contract every existing build_xlsx caller already uses. A
sheet built entirely from plain CellValue cells (no StyledCell anywhere)
renders byte-for-byte the same as before this round: no styles.xml, no `s=`
attribute on any cell, no new Content_Types/relationship entries.

Round 8-15A — additive per-sheet dropdown support (DataValidationRule) for
the crop/variety import template. Same non-breaking contract as StyledCell:
a caller that never passes `validations` gets byte-for-byte the same output
as before this round — `<dataValidations>` is only emitted for a sheet whose
name has an entry in the `validations` dict.

Round 8-15A.1 — additive `hidden_sheets`/`defined_names` support so a
DataValidationRule's formula1 can reference a workbook-scoped named range
instead of an inline literal list (Excel's inline list-validation formula is
capped around 255 characters — too small for a crop suggestion list once
there are more than a handful of crops). This module stays a GENERIC writer:
it has no idea "crop" or "variety" exist — the caller decides sheet names,
which sheet(s) to hide, and what a defined name is called/points at. A
caller that never passes `hidden_sheets`/`defined_names` (every pre-8-15A.1
caller, and every OTHER sheet in this same workbook) gets byte-for-byte the
same output as before: no `state="hidden"` attribute, no `<definedNames>`
block at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

CellValue = str | int | float | None


@dataclass(frozen=True)
class DataValidationRule:
    """One Excel "list" dropdown rule applied to a cell range.

    `sqref` is the target range (e.g. "C3:C1000"); `formula1` is a literal
    comma-separated option list INCLUDING the wrapping double quotes the
    OOXML list-formula syntax requires (e.g. '"เปิดใช้งาน,ปิดใช้งาน"') — never
    a worksheet range reference, so no defined-name/range plumbing is needed.

    `show_error_message=False` lets a user type a value outside the list
    without Excel blocking entry — used for a SUGGESTION dropdown (e.g. crop
    names) where free text must still be allowed; the default `True` blocks
    entry of anything not in the list (used for a closed set like
    active/inactive status). Either way this is a UX aid only — the backend
    always re-validates every cell itself; Excel-side enforcement is never
    trusted as the security/validation boundary.
    """

    sqref: str
    formula1: str
    allow_blank: bool = True
    show_error_message: bool = True


@dataclass(frozen=True)
class DefinedName:
    """One workbook-scoped named range (round 8-15A.1) — lets a
    DataValidationRule.formula1 reference `name` instead of an inline
    literal list, sidestepping Excel's ~255-character cap on an inline list
    formula.

    `ref` is the FULLY-QUALIFIED OOXML reference the caller builds itself,
    e.g. "'_reference'!$A$1:$A$50" (sheet name single-quoted, absolute cell
    range) — this module never assumes anything about sheet naming or
    layout; it just writes whatever ref string it's given into
    <definedNames>.
    """

    name: str
    ref: str


@dataclass(frozen=True)
class CellStyle:
    """A named cell style: solid background fill + optional font color/bold.

    Colors are 8-digit ARGB hex (e.g. "FFFFF9C4"), no leading '#'. Frozen +
    hashable so equal styles used in different places dedupe to the same
    styles.xml entry automatically."""

    bg: str | None = None
    font_color: str | None = None
    bold: bool = False


@dataclass(frozen=True)
class StyledCell:
    """Wraps a CellValue with an optional CellStyle. A build_xlsx caller that
    never constructs one of these keeps the exact pre-8-6A output."""

    value: CellValue
    style: CellStyle | None = None


Cell = CellValue | StyledCell


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _unwrap(cell: Cell) -> tuple[CellValue, CellStyle | None]:
    if isinstance(cell, StyledCell):
        return cell.value, cell.style
    return cell, None


def _collect_styles(sheets: list[tuple[str, list[list[Cell]]]]) -> dict[CellStyle, int]:
    """First pass over every sheet: every distinct CellStyle actually used,
    numbered from 1 in first-seen order. cellXfs index 0 is always the
    unstyled default, so a sheet with no styled cells never references any
    non-zero xf and registry stays empty (build_xlsx then skips styles.xml
    entirely)."""
    registry: dict[CellStyle, int] = {}
    for _name, rows in sheets:
        for row in rows:
            for cell in row:
                _value, style = _unwrap(cell)
                if style is not None and style not in registry:
                    registry[style] = len(registry) + 1
    return registry


def _data_validations_xml(rules: list[DataValidationRule] | None) -> str:
    if not rules:
        return ""
    entries = "".join(
        f'<dataValidation type="list" allowBlank="{1 if r.allow_blank else 0}" '
        f'showInputMessage="1" showErrorMessage="{1 if r.show_error_message else 0}" '
        f'sqref="{r.sqref}"><formula1>{escape(r.formula1)}</formula1></dataValidation>'
        for r in rules
    )
    return f'<dataValidations count="{len(rules)}">{entries}</dataValidations>'


def _sheet_xml(
    rows: list[list[Cell]],
    style_ids: dict[CellStyle, int] | None = None,
    validations: list[DataValidationRule] | None = None,
    *,
    column_widths: list[int] | None = None,
    freeze_header: bool = False,
    auto_filter: bool = False,
) -> str:
    style_ids = style_ids or {}
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, cell in enumerate(row, start=1):
            value, style = _unwrap(cell)
            # A plain None (no style at all) is omitted entirely — unchanged
            # pre-8-6A.1 behavior, and every existing build_xlsx caller only
            # ever produces this case. A StyledCell(None, style) is different:
            # the cell must still exist (as an empty, valueless <c>) so its
            # fill actually shows in Excel — e.g. a yellow "please fill in"
            # template cell with nothing typed in yet.
            if value is None and style is None:
                continue
            ref = f"{_col_name(col_index)}{row_index}"
            style_attr = f' s="{style_ids[style]}"' if style is not None else ""
            if value is None:
                cells.append(f'<c r="{ref}"{style_attr}/>')
                continue
            if isinstance(value, int | float):
                cells.append(f'<c r="{ref}"{style_attr}><v>{value}</v></c>')
                continue
            text = escape(str(value))
            cells.append(f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{text}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    # Round 8-20A — all three below are additive and emit NOTHING unless the
    # caller opts in, so every pre-8-20A sheet renders byte-for-byte as before.
    # Element order matters to Excel's schema: sheetViews → cols → sheetData →
    # autoFilter → dataValidations.
    views_xml = ""
    if freeze_header:
        views_xml = (
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            "</sheetView></sheetViews>"
        )
    cols_xml = ""
    if column_widths:
        cols_xml = "<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(column_widths)
        ) + "</cols>"
    filter_xml = ""
    if auto_filter and rows:
        last_col = _col_name(max(len(r) for r in rows))
        filter_xml = f'<autoFilter ref="A1:{last_col}{len(rows)}"/>'

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + views_xml
        + cols_xml
        + "<sheetData>"
        + "".join(row_xml)
        + "</sheetData>"
        + filter_xml
        + _data_validations_xml(validations)
        + "</worksheet>"
    )


def _build_styles_xml(registry: dict[CellStyle, int]) -> str:
    """styles.xml for exactly the styles in `registry` (1-based indices
    assigned by _collect_styles) plus the mandatory index-0 default and the
    two required built-in fills (none/gray125) no valid styles.xml can omit."""
    fonts = ['<font><sz val="11"/><name val="Calibri"/></font>']  # 0: default
    fills = [
        '<fill><patternFill patternType="none"/></fill>',
        '<fill><patternFill patternType="gray125"/></fill>',
    ]
    xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>']  # 0: default

    for style in sorted(registry, key=registry.get):  # type: ignore[arg-type]
        font_id = 0
        if style.font_color or style.bold:
            font_bits = ['<sz val="11"/>']
            if style.bold:
                font_bits.append("<b/>")
            if style.font_color:
                font_bits.append(f'<color rgb="{style.font_color}"/>')
            font_bits.append('<name val="Calibri"/>')
            fonts.append(f"<font>{''.join(font_bits)}</font>")
            font_id = len(fonts) - 1

        fill_id = 0
        if style.bg:
            fills.append(
                '<fill><patternFill patternType="solid">'
                f'<fgColor rgb="{style.bg}"/><bgColor indexed="64"/>'
                "</patternFill></fill>"
            )
            fill_id = len(fills) - 1

        xfs.append(
            f'<xf numFmtId="0" fontId="{font_id}" fillId="{fill_id}" borderId="0" '
            'xfId="0" applyFont="1" applyFill="1"/>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<fonts count="{len(fonts)}">{"".join(fonts)}</fonts>'
        f'<fills count="{len(fills)}">{"".join(fills)}</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        "</styleSheet>"
    )


def build_xlsx(
    sheets: list[tuple[str, list[list[Cell]]]],
    validations: dict[str, list[DataValidationRule]] | None = None,
    *,
    hidden_sheets: set[str] | None = None,
    defined_names: list[DefinedName] | None = None,
    column_widths: dict[str, list[int]] | None = None,
    freeze_header_sheets: set[str] | None = None,
    auto_filter_sheets: set[str] | None = None,
) -> bytes:
    """Return a minimal XLSX workbook with inline strings.

    Sheets may freely mix plain CellValue and StyledCell entries (round
    8-6A). styles.xml plus its Content_Types override and workbook
    relationship are added ONLY when at least one StyledCell carrying a style
    is present anywhere across `sheets` — a caller that never uses StyledCell
    gets exactly the same bytes-shape as every pre-8-6A build_xlsx call.

    `validations` (round 8-15A) is optional and keyed by sheet NAME — a
    caller that never passes it (every pre-8-15A caller) gets byte-for-byte
    the same output as before: no `<dataValidations>` block on any sheet.

    `hidden_sheets`/`defined_names` (round 8-15A.1) are both optional and
    additive — a caller that never passes them gets byte-for-byte the same
    output as before: no `state="hidden"` attribute on any `<sheet>`, no
    `<definedNames>` block at all. `hidden_sheets` is a set of sheet NAMES
    (must be a subset of the names in `sheets`) to mark hidden; sheet ORDER
    in `sheets` is unaffected — a caller wanting `read_first_sheet` (services/
    excel_reader.py) to keep landing on the same visible sheet as before must
    still list that sheet first, same as always.

    `column_widths`/`freeze_header_sheets`/`auto_filter_sheets` (round 8-20A)
    are optional and keyed by sheet NAME, and are additive in exactly the same
    way: a caller that never passes them gets byte-for-byte the same output as
    before — no `<cols>`, no frozen `<pane>`, no `<autoFilter>` on any sheet.
    `column_widths` values are 1-based column order (index 0 = column A).
    """
    style_registry = _collect_styles(sheets)
    has_styles = bool(style_registry)
    validations = validations or {}
    hidden_sheets = hidden_sheets or set()
    defined_names = defined_names or []
    column_widths = column_widths or {}
    freeze_header_sheets = freeze_header_sheets or set()
    auto_filter_sheets = auto_filter_sheets or set()

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        content_types_overrides = (
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        )
        if has_styles:
            content_types_overrides += (
                '<Override PartName="/xl/styles.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            )
        content_types_overrides += "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + content_types_overrides
            + "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        defined_names_xml = ""
        if defined_names:
            defined_names_xml = "<definedNames>" + "".join(
                f'<definedName name="{escape(dn.name)}">{escape(dn.ref)}</definedName>'
                for dn in defined_names
            ) + "</definedNames>"
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"'
                + (' state="hidden"' if name in hidden_sheets else "")
                + "/>"
                for i, (name, _) in enumerate(sheets, start=1)
            )
            + "</sheets>"
            + defined_names_xml
            + "</workbook>",
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(sheets) + 1)
            )
        )
        if has_styles:
            styles_rid = len(sheets) + 1
            rels += (
                f'<Relationship Id="rId{styles_rid}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                'Target="styles.xml"/>'
            )
        rels += "</Relationships>"
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        if has_styles:
            zf.writestr("xl/styles.xml", _build_styles_xml(style_registry))
        for i, (name, rows) in enumerate(sheets, start=1):
            zf.writestr(
                f"xl/worksheets/sheet{i}.xml",
                _sheet_xml(
                    rows, style_registry, validations.get(name),
                    column_widths=column_widths.get(name),
                    freeze_header=name in freeze_header_sheets,
                    auto_filter=name in auto_filter_sheets,
                ),
            )
    return buffer.getvalue()
