"""
INVENTRA Universal Multi-Format Folder Ingestion Command
Usage:
    python manage.py ingest_inventory --folder data/clean_uploads
"""
import os
from pathlib import Path
from django.core.management.base import BaseCommand
from inventory.ingestion import ingest_file_bytes

SUPPORTED_EXTS = {
    '.xlsx', '.xls', '.csv', '.tsv', '.json',
    '.pdf', '.docx', '.html', '.htm', '.xml', '.txt', '.md',
}

EXCLUDED_FILE_KEYWORDS = (
    'pr_line_items_template',
    'labs_and_staff',
    'lab report',
)


class Command(BaseCommand):
    help = "Bulk-ingests any supported inventory file format from a directory into Supabase."

    def add_arguments(self, parser):
        parser.add_argument('--folder', type=str, default='data', help='Directory containing inventory files')

    def handle(self, *args, **options):
        data_dir = Path(options['folder'])
        if not data_dir.exists():
            self.stderr.write(self.style.ERROR(f"Folder '{data_dir}' not found."))
            return

        total_imported = 0
        for root, _, files in os.walk(data_dir):
            for fname in sorted(files):
                lower_name = fname.lower()
                if fname.startswith('~$') or any(k in lower_name for k in EXCLUDED_FILE_KEYWORDS):
                    self.stdout.write(self.style.WARNING(f"[SKIP-EXCLUDED] {fname}"))
                    continue

                fpath = Path(root) / fname
                if fpath.suffix.lower() not in SUPPORTED_EXTS:
                    continue

                try:
                    res = ingest_file_bytes(fpath.read_bytes(), fname, parent_folder=fpath.parent.name)
                    total_imported += res['imported']
                    self.stdout.write(self.style.SUCCESS(
                        f"[OK] {fname} -> {res['lab']} | Imported: {res['imported']} (Skipped: {res['skipped']})"
                    ))
                except Exception as exc:
                    self.stderr.write(self.style.WARNING(f"[ERR] {fname}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"\nDONE! Total assets ingested into Supabase: {total_imported}"))
