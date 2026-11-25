"""
INVENTRA Universal Ingestion & University PR Template Export Engine
- Supports 12 input formats (.xlsx, .xls, .csv, .tsv, .json, .pdf, .docx, .html, .xml, .txt, .md)
- Blocks instructor/staff columns from being misclassified as equipment
- Exports exact Chanakya University PR Line Items Excel workbooks (Asset, Consumable, Service)
"""
import io
import json
import re
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill
import pandas as pd
from .models import Lab, Asset, MaintenanceLog

COLUMN_SYNONYMS = {
    'name': [
        'item', 'description', 'equipment', 'particulars', 'asset',
        'component', 'instrument', 'apparatus', 'product', 'material', 'device', 'title', 'name'
    ],
    'category': ['category', 'type', 'group', 'class', 'domain', 'make', 'brand'],
    'serial': ['serial', 'asset code', 'asset id', 'tag', 'barcode', 'part no', 'model no', 'sku'],
    'quantity': ['qty', 'quantity', 'stock', 'count', 'available', 'total', 'nos', 'units', 'balance'],
    'condition': ['condition', 'remarks', 'status', 'working', 'state', 'health'],
    'lab': ['lab_code', 'lab', 'laboratory', 'room', 'location'],
}

EXCLUDED_NAME_HEADERS = {'instructor', 'technician', 'foreman', 'faculty', 'staff', 'assistant', 'in-charge', 'lab name', 'laboratory name'}

IGNORED_NAMES = {
    'nan', 'none', 'null', 'na', 'n/a', 'total', 'grand total', 'sub total', 'description',
    'item name', 'particulars', 'equipment name', 'sl no', 's.no', 'sr no', ''
}


def detect_lab_from_name(source_name: str, parent_name: str = '') -> Tuple[str, str, str]:
    """Returns (lab_name, lab_code, lab_type)."""
    fname = Path(source_name).stem.upper()
    parent = (parent_name or '').upper()
    combined = f"{parent} {fname}"

    room_match = re.search(r'(UB[-_\s]?\d+)', combined)
    if room_match:
        room = re.sub(r'[-_\s]', '', room_match.group(1))
        return f"CS Lab {room}", f"CS{room}", 'COMPUTER_SCIENCE'
    if any(k in combined for k in ['DIGITAL', 'ELECTRONIC', 'ANALOG', 'ECE', 'LA44', 'LA-44']):
        return "Digital & Analog Electronics Lab", "ELEC", 'ELECTRONICS'
    if 'CHEM' in combined:
        return "Chemistry Lab", "CHEM", 'CHEMISTRY'
    if 'PHYS' in combined:
        return "Physics Lab", "PHYS", 'PHYSICS'
    if any(k in combined for k in ['MECH', 'AERO', 'CNC', 'MANUF', 'WORKSHOP']):
        return "Mechanical & Manufacturing Lab", "MECH", 'MECHANICAL_MANUFACTURING'
    if any(k in combined for k in ['IDEA', '3D', 'DESIGN', 'IDT']):
        return "Ideation & Design Thinking Lab", "IDEA", 'IDEATION_DESIGN'

    clean_code = re.sub(r'[^A-Z0-9]', '', fname)[:10] or 'GEN'
    clean_name = re.sub(r'[_-]+', ' ', Path(source_name).stem).strip().title() or "General Lab"
    return clean_name, clean_code, 'COMPUTER_SCIENCE'


def _find_header_row(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if not all(isinstance(c, int) or str(c).startswith('Unnamed') for c in df.columns):
        return df

    keywords = ['name', 'item', 'description', 'qty', 'quantity', 'particulars', 'equipment', 'sl', 'serial', 'category']
    for pos, (_, row) in enumerate(df.head(15).iterrows()):
        row_str = ' '.join(str(v).lower() for v in row.values if pd.notna(v))
        if sum(1 for kw in keywords if kw in row_str) >= 2:
            df = df.copy()
            df.columns = [str(c).strip() if pd.notna(c) else f"col_{i}" for i, c in enumerate(row.values)]
            return df.iloc[pos + 1:].reset_index(drop=True)
    return df


def _map_columns(columns) -> Dict[str, Any]:
    mapping: Dict[str, Any] = {k: None for k in COLUMN_SYNONYMS}
    for col in columns:
        col_lower = str(col).lower().strip()
        for field, synonyms in COLUMN_SYNONYMS.items():
            if field == 'name' and any(bad in col_lower for bad in EXCLUDED_NAME_HEADERS):
                continue
            if field == 'serial' and col_lower in {'sl no', 'sl.no', 's.no', 's.no.', 'sr no', 'si no', 'sl. no.'}:
                continue
            if mapping[field] is None and any(syn in col_lower for syn in synonyms):
                mapping[field] = col
    return mapping


def _parse_condition(val) -> str:
    s = str(val).lower()
    if any(w in s for w in ['not working', 'broken', 'dead', 'unusable', 'scrap', 'condemned']):
        return 'UNUSABLE'
    if any(w in s for w in ['damage', 'fault', 'repair', 'defect', 'bad']):
        return 'DAMAGED'
    if any(w in s for w in ['minor', 'wear', 'scratch']):
        return 'MINOR_DAMAGE'
    return 'GOOD'


def _parse_quantity(val) -> int:
    if pd.isna(val):
        return 1
    nums = re.findall(r'\d+', str(val))
    return max(1, int(nums[0])) if nums else 1


def extract_dataframes_from_bytes(file_bytes: bytes, filename: str) -> List[pd.DataFrame]:
    ext = Path(filename).suffix.lower()
    buf = io.BytesIO(file_bytes)
    dfs: List[pd.DataFrame] = []

    if ext in ['.xlsx', '.xls']:
        sheets = pd.read_excel(buf, sheet_name=None, header=None)
        dfs.extend(sheets.values())
    elif ext in ['.csv', '.tsv']:
        text = file_bytes.decode('utf-8-sig', errors='replace')
        dfs.append(pd.read_csv(io.StringIO(text), sep=None, engine='python'))
    elif ext == '.json':
        raw = json.loads(file_bytes.decode('utf-8-sig', errors='replace'))
        if isinstance(raw, dict):
            list_vals = [v for v in raw.values() if isinstance(v, list)]
            raw = list_vals[0] if list_vals else [raw]
        dfs.append(pd.json_normalize(raw))
    elif ext == '.pdf':
        import pdfplumber
        with pdfplumber.open(buf) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for tbl in tables:
                    if tbl:
                        dfs.append(pd.DataFrame(tbl))
                if not tables:
                    lines = [ln.strip() for ln in (page.extract_text() or '').splitlines() if len(ln.strip()) > 3]
                    if lines:
                        dfs.append(pd.DataFrame({'name': lines}))
    elif ext == '.docx':
        from docx import Document
        doc = Document(buf)
        for tbl in doc.tables:
            rows = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
            if rows:
                dfs.append(pd.DataFrame(rows))
        if not doc.tables:
            lines = [p.text.strip() for p in doc.paragraphs if len(p.text.strip()) > 3]
            if lines:
                dfs.append(pd.DataFrame({'name': lines}))
    elif ext in ['.html', '.htm']:
        html_text = file_bytes.decode('utf-8-sig', errors='replace')
        dfs.extend(pd.read_html(io.StringIO(html_text)))
    elif ext == '.xml':
        dfs.append(pd.read_xml(buf))
    elif ext in ['.txt', '.md']:
        text = file_bytes.decode('utf-8-sig', errors='replace')
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith('#')]
        if lines and any(sep in lines[0] for sep in ['\t', '|', ',']):
            dfs.append(pd.read_csv(io.StringIO('\n'.join(lines)), sep=None, engine='python'))
        elif lines:
            dfs.append(pd.DataFrame({'name': lines}))
    else:
        raise ValueError(f"Unsupported format '{ext}'. Supported: .xlsx, .xls, .csv, .tsv, .json, .pdf, .docx, .html, .xml, .txt")

    return [df for df in dfs if df is not None and not df.empty]


def ingest_file_bytes(
    file_bytes: bytes,
    filename: str,
    default_lab_name: str = '',
    parent_folder: str = '',
    forced_lab: Optional[Lab] = None,
) -> Dict[str, Any]:
    """Ingests equipment file into Supabase. If forced_lab is provided (Lab Assistant), all rows stay locked to it."""
    if forced_lab is not None:
        default_lab = forced_lab
    else:
        lab_name, lab_code, lab_type = (
            detect_lab_from_name(default_lab_name) if default_lab_name
            else detect_lab_from_name(filename, parent_folder)
        )
        default_lab, _ = Lab.objects.get_or_create(
            lab_code=lab_code,
            defaults={'lab_name': lab_name, 'lab_type': lab_type}
        )

    dfs = extract_dataframes_from_bytes(file_bytes, filename)
    imported = 0
    skipped = 0

    for raw_df in dfs:
        df = _find_header_row(raw_df)
        col_map = _map_columns(df.columns)
        if not col_map['name']:
            skipped += len(df)
            continue

        mapped_cols = {v for v in col_map.values() if v is not None}

        for _, row in df.iterrows():
            raw_name = row.get(col_map['name'])
            if pd.isna(raw_name):
                skipped += 1
                continue
            name = str(raw_name).strip()
            if len(name) < 2 or name.lower() in IGNORED_NAMES or name.isdigit():
                skipped += 1
                continue

            target_lab = default_lab
            if forced_lab is None and col_map['lab'] and pd.notna(row.get(col_map['lab'])):
                row_lab_str = str(row.get(col_map['lab'])).strip()
                if len(row_lab_str) >= 2 and row_lab_str.lower() not in IGNORED_NAMES:
                    existing = Lab.objects.filter(lab_code__iexact=row_lab_str).first() or Lab.objects.filter(lab_name__iexact=row_lab_str).first()
                    if existing:
                        target_lab = existing
                    else:
                        r_name, r_code, r_type = detect_lab_from_name(row_lab_str)
                        target_lab, _ = Lab.objects.get_or_create(
                            lab_code=r_code, defaults={'lab_name': r_name, 'lab_type': r_type}
                        )

            category = str(row.get(col_map['category'], 'Lab Equipment')).strip() if col_map['category'] else 'Lab Equipment'
            if category.lower() in IGNORED_NAMES:
                category = 'Lab Equipment'

            qty = _parse_quantity(row.get(col_map['quantity'])) if col_map['quantity'] else 1
            cond = _parse_condition(row.get(col_map['condition'])) if col_map['condition'] else 'GOOD'
            raw_serial = str(row.get(col_map['serial'], '')).strip() if col_map['serial'] else ''
            if raw_serial.lower() in IGNORED_NAMES or Asset.objects.filter(serial_number=raw_serial).exists():
                raw_serial = ''

            explicit_type = str(row.get('asset_type', '')).strip().upper()
            if explicit_type in {'SHARED', 'CONSUMABLE', 'TIME_SLOTTED'}:
                asset_type = explicit_type
            else:
                asset_type = 'SHARED'
                lower_blob = f"{name} {category} {filename}".lower()
                if any(k in lower_blob for k in ['3d print', 'cnc', 'laser', 'lathe', 'milling']):
                    asset_type = 'TIME_SLOTTED'
                elif any(k in lower_blob for k in ['consumable', 'acid', 'reagent', 'resistor', 'capacitor', 'chemical', 'filament']):
                    asset_type = 'CONSUMABLE'

            extras: Dict[str, Any] = {'source_file': filename}
            for col in df.columns:
                if col not in mapped_cols and str(col) != 'asset_type' and pd.notna(row.get(col)):
                    val = str(row.get(col)).strip()
                    if val and val.lower() not in {'nan', 'none'}:
                        extras[str(col)] = val

            Asset.objects.create(
                name=name[:250],
                category=category[:95],
                asset_type=asset_type,
                serial_number=raw_serial,
                lab=target_lab,
                stock_quantity=qty,
                total_quantity=qty,
                condition=cond,
                extra_metadata=extras,
            )
            imported += 1

    return {'filename': filename, 'lab': default_lab.lab_name, 'imported': imported, 'skipped': skipped}


# ==============================================================================
# EXACT UNIVERSITY PR LINE ITEMS TEMPLATE EXPORTERS + GENERAL EXPORTS
# ==============================================================================

def _build_pr_workbook(headers: List[str], rows: List[List[Any]]) -> bytes:
    """Generates a styled Excel workbook with sheet name 'PR Lines' matching university templates."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = 'PR Lines'

    header_fill = PatternFill(start_color='153B5B', end_color='153B5B', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    for r in rows:
        ws.append(r)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_inventory_bytes(fmt: str = 'csv', lab_filter: Optional[Lab] = None) -> Tuple[bytes, str, str]:
    """
    Exports live Supabase data in:
    - pr_asset      -> Exact match for PR_Line_Items_Template_Asset.xlsx ('PR Lines')
    - pr_consumable -> Exact match for PR_Line_Items_Template_Consumable.xlsx ('PR Lines')
    - pr_service    -> Exact match for PR_Line_Items_Template_Service.xlsx ('PR Lines')
    - csv / xlsx / json / html -> Complete inventory export
    """
    fmt = fmt.lower().strip()
    xlsx_mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    asset_qs = Asset.objects.select_related('lab').all().order_by('asset_tag')
    if lab_filter is not None:
        asset_qs = asset_qs.filter(lab=lab_filter)

    if fmt == 'pr_asset':
        headers = ['Type', 'Category', 'Quantity', 'Unit Cost', 'UOM', 'Specification', 'GST Rate (%)']
        pr_rows = []
        for a in asset_qs.filter(asset_type__in=['SHARED', 'TIME_SLOTTED']):
            meta = a.extra_metadata if isinstance(a.extra_metadata, dict) else {}
            spec = f"[{a.asset_tag}] {a.name} ({a.lab.lab_name})"
            if meta.get('specifications'):
                spec += f" - {meta['specifications']}"
            pr_rows.append([
                'Asset',
                a.category,
                a.total_quantity,
                meta.get('unit_cost', ''),
                meta.get('uom', 'Nos'),
                spec,
                meta.get('gst_rate', 18),
            ])
        return _build_pr_workbook(headers, pr_rows), xlsx_mime, 'PR_Line_Items_Asset_Export.xlsx'

    if fmt == 'pr_consumable':
        headers = ['Type', 'Category', 'Item', 'Variant', 'Quantity', 'Unit Cost', 'UOM', 'Specification', 'GST Rate (%)']
        pr_rows = []
        for a in asset_qs.filter(asset_type='CONSUMABLE'):
            meta = a.extra_metadata if isinstance(a.extra_metadata, dict) else {}
            pr_rows.append([
                'Consumable',
                a.category,
                a.name,
                meta.get('variant', a.lab.lab_code),
                a.stock_quantity,
                meta.get('unit_cost', ''),
                meta.get('uom', 'Units'),
                meta.get('specifications', f"[{a.asset_tag}] {a.lab.lab_name}"),
                meta.get('gst_rate', 18),
            ])
        return _build_pr_workbook(headers, pr_rows), xlsx_mime, 'PR_Line_Items_Consumable_Export.xlsx'

    if fmt == 'pr_service':
        headers = ['Type', 'Category', 'Quantity', 'Unit Cost', 'UOM', 'Specification', 'GST Rate (%)']
        maint_qs = MaintenanceLog.objects.select_related('asset', 'asset__lab').all().order_by('-report_date')
        if lab_filter is not None:
            maint_qs = maint_qs.filter(asset__lab=lab_filter)
        pr_rows = []
        for m in maint_qs:
            pr_rows.append([
                'Service',
                f"Equipment Repair ({m.asset.category})",
                1,
                '',
                'Job',
                f"[{m.asset.asset_tag}] {m.asset.name} ({m.asset.lab.lab_name}) - {m.description} [{m.resolution_status}]",
                18,
            ])
        # Also include any DAMAGED/UNUSABLE assets without an open log
        logged_ids = {m.asset.asset_id for m in maint_qs}
        for a in asset_qs.filter(status='MAINTENANCE'):
            if a.asset_id not in logged_ids:
                pr_rows.append([
                    'Service',
                    f"Maintenance ({a.category})",
                    1,
                    '',
                    'Job',
                    f"[{a.asset_tag}] {a.name} ({a.lab.lab_name}) - Condition: {a.condition}",
                    18,
                ])
        return _build_pr_workbook(headers, pr_rows), xlsx_mime, 'PR_Line_Items_Service_Export.xlsx'

    rows = []
    for a in asset_qs:
        row: Dict[str, Any] = {
            'asset_tag': a.asset_tag,
            'name': a.name,
            'lab': a.lab.lab_name,
            'lab_code': a.lab.lab_code,
            'category': a.category,
            'asset_type': a.asset_type,
            'serial_number': a.serial_number,
            'status': a.status,
            'condition': a.condition,
            'stock_quantity': a.stock_quantity,
            'total_quantity': a.total_quantity,
        }
        if isinstance(a.extra_metadata, dict):
            for k, v in a.extra_metadata.items():
                if k not in row:
                    row[k] = v
        rows.append(row)

    df = pd.DataFrame(rows)
    if fmt == 'json':
        return df.to_json(orient='records', indent=2).encode('utf-8'), 'application/json', 'inventra_inventory.json'
    if fmt == 'xlsx':
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue(), xlsx_mime, 'inventra_inventory.xlsx'
    if fmt == 'html':
        return df.to_html(index=False).encode('utf-8'), 'text/html', 'inventra_inventory.html'
    return df.to_csv(index=False).encode('utf-8'), 'text/csv', 'inventra_inventory.csv'
