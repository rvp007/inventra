"""
INVENTRA Universal Data Ingestion & Export Engine
Accepts: .xlsx, .xls, .csv, .tsv, .json, .pdf, .docx, .html, .htm, .xml, .txt, .md
Preserves unmapped columns inside Asset.extra_metadata (JSONB).
"""
import io
import json
import re
from pathlib import Path
from typing import Tuple, Dict, Any, List

import pandas as pd
from .models import Lab, Asset

COLUMN_SYNONYMS = {
    'name': [
        'item', 'description', 'equipment', 'particulars', 'name', 'asset',
        'component', 'instrument', 'apparatus', 'product', 'material', 'device', 'title'
    ],
    'category': ['category', 'type', 'group', 'class', 'domain', 'make', 'brand', 'spec'],
    'serial': ['serial', 'asset code', 'asset id', 'tag', 'barcode', 'part no', 'model no', 'sku'],
    'quantity': ['qty', 'quantity', 'stock', 'count', 'available', 'total', 'nos', 'units', 'balance'],
    'condition': ['condition', 'remarks', 'status', 'working', 'state', 'health'],
    'lab': ['lab', 'laboratory', 'room', 'location', 'department'],
}

IGNORED_NAMES = {
    'nan', 'none', 'null', 'total', 'grand total', 'sub total', 'description',
    'item name', 'particulars', 'equipment name', 'sl no', 's.no', 'sr no', ''
}


def detect_lab_from_name(source_name: str, parent_name: str = '') -> Tuple[str, str]:
    """Derives standardized Lab Name and Lab Code prefix from filename or text."""
    fname = Path(source_name).stem.upper()
    parent = (parent_name or '').upper()
    combined = f"{parent} {fname}"

    room_match = re.search(r'(UB[-_\s]?\d+)', combined)
    if room_match:
        room = re.sub(r'[-_\s]', '', room_match.group(1))
        return f"CS Lab {room}", f"CS{room}"
    if any(k in combined for k in ['DIGITAL', 'ELECTRONIC', 'ANALOG', 'ECE']):
        return "Digital & Analog Electronics Lab", "ELEC"
    if 'CHEM' in combined:
        return "Chemistry Lab", "CHEM"
    if 'PHYS' in combined:
        return "Physics Lab", "PHYS"
    if any(k in combined for k in ['MECH', 'AERO']):
        return "Mechanical Science Lab", "MECH"
    if any(k in combined for k in ['CNC', 'MANUF', 'LASER']):
        return "Manufacturing & CNC Lab", "MFG"
    if any(k in combined for k in ['IDEA', '3D', 'DESIGN']):
        return "Ideation & Design Thinking Lab", "IDEA"
    if 'CONSUMABLE' in combined:
        return "Central Consumables Store", "CONS"

    clean_code = re.sub(r'[^A-Z0-9]', '', fname)[:8] or 'GEN'
    clean_name = re.sub(r'[_-]+', ' ', Path(source_name).stem).strip().title() or "General Lab"
    return clean_name, clean_code


def _find_header_row(df: pd.DataFrame) -> pd.DataFrame:
    """Locates the real table header row inside spreadsheets/PDFs that have title banners."""
    if df.empty:
        return df
    # If columns already look meaningful (not 0, 1, 2...), keep them
    if not all(isinstance(c, int) or str(c).startswith('Unnamed') for c in df.columns):
        return df

    keywords = ['name', 'item', 'description', 'qty', 'quantity', 'particulars', 'equipment', 'sl', 'serial', 'make']
    for idx, row in df.head(15).iterrows():
        row_str = ' '.join(str(v).lower() for v in row.values if pd.notna(v))
        if sum(1 for kw in keywords if kw in row_str) >= 2:
            df = df.copy()
            df.columns = [str(c).strip() if pd.notna(c) else f"col_{i}" for i, c in enumerate(row.values)]
            return df.iloc[idx + 1:].reset_index(drop=True)
    return df


def _map_columns(columns) -> Dict[str, Any]:
    mapping = {k: None for k in COLUMN_SYNONYMS}
    for col in columns:
        col_lower = str(col).lower().strip()
        for field, synonyms in COLUMN_SYNONYMS.items():
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
    """Universal format router: converts ANY supported file into normalized DataFrames."""
    ext = Path(filename).suffix.lower()
    buf = io.BytesIO(file_bytes)
    dfs: List[pd.DataFrame] = []

    if ext in ['.xlsx', '.xls']:
        sheets = pd.read_excel(buf, sheet_name=None, header=None)
        dfs.extend(sheets.values())

    elif ext in ['.csv', '.tsv']:
        text = file_bytes.decode('utf-8-sig', errors='replace')
        dfs.append(pd.read_csv(io.StringIO(text), sep=None, engine='python', header=None))

    elif ext == '.json':
        raw = json.loads(file_bytes.decode('utf-8-sig', errors='replace'))
        if isinstance(raw, dict):
            # Look for the first list value inside wrapper object (e.g. {"items": [...]})
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
                    # Fallback for borderless text PDFs
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
        # Try tab/pipe/comma separated first; otherwise treat each line as an item name
        if any(sep in lines[0] for sep in ['\t', '|', ',']) if lines else False:
            dfs.append(pd.read_csv(io.StringIO('\n'.join(lines)), sep=None, engine='python'))
        elif lines:
            dfs.append(pd.DataFrame({'name': lines}))

    else:
        raise ValueError(
            f"Unsupported file format '{ext}'. Supported: .xlsx, .xls, .csv, .tsv, .json, .pdf, .docx, .html, .xml, .txt"
        )

    return [df for df in dfs if df is not None and not df.empty]


def ingest_file_bytes(file_bytes: bytes, filename: str, default_lab_name: str = '', parent_folder: str = '') -> Dict[str, Any]:
    """Ingests any supported file into Supabase Assets with unique lab tags & JSONB metadata."""
    lab_name, lab_code = (
        detect_lab_from_name(default_lab_name) if default_lab_name
        else detect_lab_from_name(filename, parent_folder)
    )
    default_lab, _ = Lab.objects.get_or_create(lab_code=lab_code, defaults={'lab_name': lab_name})

    dfs = extract_dataframes_from_bytes(file_bytes, filename)
    imported = 0
    skipped = 0

    for raw_df in dfs:
        df = _find_header_row(raw_df)
        col_map = _map_columns(df.columns)

        if not col_map['name']:
            for col in df.columns:
                c_str = str(col).lower()
                if 'unnamed' not in c_str and not c_str.startswith('sl') and not c_str.startswith('col_'):
                    col_map['name'] = col
                    break
            if not col_map['name'] and len(df.columns) > 0:
                col_map['name'] = df.columns[0]

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

            # Resolve row-level lab if the file contains a 'Lab' column
            target_lab = default_lab
            if col_map['lab'] and pd.notna(row.get(col_map['lab'])):
                row_lab_str = str(row.get(col_map['lab'])).strip()
                if len(row_lab_str) >= 2 and row_lab_str.lower() not in IGNORED_NAMES:
                    r_name, r_code = detect_lab_from_name(row_lab_str)
                    target_lab, _ = Lab.objects.get_or_create(lab_code=r_code, defaults={'lab_name': r_name})

            category = str(row.get(col_map['category'], 'Lab Equipment')).strip() if col_map['category'] else 'Lab Equipment'
            if category.lower() in IGNORED_NAMES:
                category = 'Lab Equipment'

            qty = _parse_quantity(row.get(col_map['quantity'])) if col_map['quantity'] else 1
            cond = _parse_condition(row.get(col_map['condition'])) if col_map['condition'] else 'GOOD'
            raw_serial = str(row.get(col_map['serial'], '')).strip() if col_map['serial'] else ''
            if raw_serial.lower() in IGNORED_NAMES or Asset.objects.filter(serial_number=raw_serial).exists():
                raw_serial = ''

            # Detect asset_type heuristically
            asset_type = 'SHARED'
            lower_blob = f"{name} {category} {filename}".lower()
            if any(k in lower_blob for k in ['3d print', 'cnc', 'laser', 'lathe', 'milling']):
                asset_type = 'TIME_SLOTTED'
            elif any(k in lower_blob for k in ['consumable', 'acid', 'reagent', 'resistor', 'capacitor', 'chemical', 'filament']):
                asset_type = 'CONSUMABLE'

            # Store ALL remaining columns cleanly inside JSONB extra_metadata
            extras = {'source_file': filename}
            for col in df.columns:
                if col not in mapped_cols and pd.notna(row.get(col)):
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


def export_inventory_bytes(fmt: str = 'csv') -> Tuple[bytes, str, str]:
    """Exports the entire live Supabase inventory into csv, xlsx, json, or html."""
    rows = []
    for a in Asset.objects.select_related('lab').all().order_by('asset_tag'):
        row = {
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
    fmt = fmt.lower().strip()
    if fmt == 'json':
        return df.to_json(orient='records', indent=2).encode('utf-8'), 'application/json', 'inventra_inventory.json'
    if fmt == 'xlsx':
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'inventra_inventory.xlsx'
    if fmt == 'html':
        return df.to_html(index=False).encode('utf-8'), 'text/html', 'inventra_inventory.html'
    return df.to_csv(index=False).encode('utf-8'), 'text/csv', 'inventra_inventory.csv'
