"""
INVENTRA Canonical Data Loader & RBAC Demo Seeder
Usage:
    python manage.py ingest_canonical --seed-users
    python manage.py ingest_canonical --labs data/labs_and_staff.csv --assets data/assets_clean.csv --seed-users
"""
import csv
from pathlib import Path
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from inventory.models import Lab, Asset, UserProfile

VALID_LAB_TYPES = {c[0] for c in Lab.LAB_TYPE_CHOICES}
VALID_ASSET_TYPES = {c[0] for c in Asset.ASSET_TYPE_CHOICES}
VALID_CONDITIONS = {c[0] for c in Asset.CONDITION_CHOICES}

DEFAULT_SIX_LABS = [
    ('CSUB44', 'CS Lab UB-44 (Computer Lab 7)', 'COMPUTER_SCIENCE', 'UB-44', 'Vinod Kalagi'),
    ('ELEC', 'Basic & Digital Electronics Lab', 'ELECTRONICS', 'LA-44', 'Vinay Umesh Pujary'),
    ('CHEM', 'Chemistry Lab', 'CHEMISTRY', 'Academic Block B', 'Lab Instructor (Chemistry)'),
    ('PHYS', 'Physics Lab', 'PHYSICS', 'Academic Block B', 'Lab Instructor (Physics)'),
    ('MECH', 'Mechanical & CNC Manufacturing Lab', 'MECHANICAL_MANUFACTURING', 'Workshop Block', 'Lab Instructor (Mechanical)'),
    ('IDEA', 'Ideation & Design Thinking Lab (IDT)', 'IDEATION_DESIGN', 'Innovation Center', 'Lab Instructor (IDT)'),
]


class Command(BaseCommand):
    help = "Ingests clean labs_and_staff.csv & assets_clean.csv, ensures all 6 lab types exist, and seeds RBAC demo accounts."

    def add_arguments(self, parser):
        parser.add_argument('--labs', type=str, default='data/labs_and_staff.csv', help='Path to labs_and_staff.csv')
        parser.add_argument('--assets', type=str, default='data/assets_clean.csv', help='Path to assets_clean.csv')
        parser.add_argument(
            '--seed-users',
            action='store_true',
            help='Create/reset verified Admin, Faculty, Lab-Scoped Assistants, and Student accounts',
        )

    def handle(self, *args, **options):
        labs_path = Path(options['labs'])
        assets_path = Path(options['assets'])

        # 1. Sync labs from canonical labs_and_staff.csv if present
        labs_synced = 0
        if labs_path.exists():
            with labs_path.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    code = ''.join(c for c in (row.get('lab_code') or '') if c.isalnum()).upper()[:15]
                    name = (row.get('lab_name') or '').strip()
                    if not code or not name:
                        continue
                    raw_type = (row.get('lab_type') or 'COMPUTER_SCIENCE').strip().upper()
                    lab_type = raw_type if raw_type in VALID_LAB_TYPES else 'COMPUTER_SCIENCE'

                    Lab.objects.update_or_create(
                        lab_code=code,
                        defaults={
                            'lab_name': name[:120],
                            'lab_type': lab_type,
                            'location': (row.get('location') or '').strip()[:100],
                            'instructor_name': (row.get('in_charge_name') or '').strip()[:150],
                        },
                    )
                    labs_synced += 1
            self.stdout.write(self.style.SUCCESS(f"[OK] Synced {labs_synced} labs from {labs_path}"))
        else:
            self.stdout.write(self.style.WARNING(f"[INFO] {labs_path} not found — ensuring default 6 campus labs exist."))

        # 2. Guarantee all 6 campus lab types exist in Supabase
        existing_types = set(Lab.objects.values_list('lab_type', flat=True))
        for code, name, ltype, loc, instr in DEFAULT_SIX_LABS:
            if ltype not in existing_types:
                Lab.objects.get_or_create(
                    lab_code=code,
                    defaults={
                        'lab_name': name,
                        'lab_type': ltype,
                        'location': loc,
                        'instructor_name': instr,
                    },
                )
        self.stdout.write(self.style.SUCCESS(f"[OK] Total Labs in Supabase: {Lab.objects.count()} (All 6 lab types covered)"))

        # 3. Load clean assets from canonical assets_clean.csv if present
        assets_created = 0
        assets_skipped = 0
        if assets_path.exists():
            with assets_path.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    code = ''.join(c for c in (row.get('lab_code') or '') if c.isalnum()).upper()[:15]
                    name = (row.get('name') or '').strip()
                    if not code or not name:
                        assets_skipped += 1
                        continue

                    lab = Lab.objects.filter(lab_code=code).first()
                    if not lab:
                        assets_skipped += 1
                        continue

                    try:
                        qty = max(1, int(float(row.get('stock_quantity') or 1)))
                    except (ValueError, TypeError):
                        qty = 1

                    raw_asset_type = (row.get('asset_type') or 'SHARED').strip().upper()
                    asset_type = raw_asset_type if raw_asset_type in VALID_ASSET_TYPES else 'SHARED'

                    raw_cond = (row.get('condition') or 'GOOD').strip().upper()
                    condition = raw_cond if raw_cond in VALID_CONDITIONS else 'GOOD'

                    serial = (row.get('serial_number') or '').strip()[:150]
                    if serial and Asset.objects.filter(serial_number=serial).exists():
                        serial = ''

                    specs = (row.get('specifications') or '').strip()
                    Asset.objects.create(
                        name=name[:250],
                        category=(row.get('category') or 'Lab Equipment').strip()[:95],
                        asset_type=asset_type,
                        serial_number=serial,
                        lab=lab,
                        stock_quantity=qty,
                        total_quantity=qty,
                        condition=condition,
                        extra_metadata={'specifications': specs} if specs else {},
                    )
                    assets_created += 1
            self.stdout.write(self.style.SUCCESS(
                f"[OK] Imported {assets_created} clean assets from {assets_path} (Skipped: {assets_skipped})"
            ))
        else:
            self.stdout.write(self.style.WARNING(f"[INFO] {assets_path} not found — drop your cleaned CSV there and re-run."))

        # 4. Seed verified RBAC demo accounts
        if options['seed_users']:
            cs_lab = Lab.objects.filter(lab_type='COMPUTER_SCIENCE').first()
            elec_lab = (
                Lab.objects.filter(lab_type='ELECTRONICS').first()
                or Lab.objects.exclude(pk=cs_lab.pk if cs_lab else None).first()
            )

            accounts = [
                ('admin', 'admin@chanakyauniversity.edu.in', 'Inventra@2026', 'System', 'Admin', 'ADMIN', 'Administration', None, True),
                ('faculty.demo', 'faculty@chanakyauniversity.edu.in', 'Inventra@2026', 'Dr. Meera', 'Nair', 'FACULTY', 'CSE', None, False),
                ('assistant.cs', 'vinod.cs@chanakyauniversity.edu.in', 'Inventra@2026', 'Vinod', 'Kalagi', 'ASSISTANT', 'CSE', cs_lab, False),
                ('assistant.elec', 'vinay.elec@chanakyauniversity.edu.in', 'Inventra@2026', 'Vinay', 'Pujary', 'ASSISTANT', 'ECE', elec_lab, False),
                ('student.demo', 'student@chanakyauniversity.edu.in', 'Inventra@2026', 'Rakesh', 'V P', 'STUDENT', 'CSE', None, False),
            ]
            for uname, email, pwd, fn, ln, role, dept, alab, is_super in accounts:
                user, _ = User.objects.get_or_create(
                    username=uname,
                    defaults={
                        'email': email,
                        'first_name': fn,
                        'last_name': ln,
                        'is_staff': is_super,
                        'is_superuser': is_super,
                    },
                )
                user.set_password(pwd)
                user.email = email
                user.first_name = fn
                user.last_name = ln
                user.is_staff = is_super
                user.is_superuser = is_super
                user.save()

                UserProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        'role': role,
                        'department': dept,
                        'assigned_lab': alab,
                        'is_email_verified': True,
                    },
                )

            self.stdout.write(self.style.SUCCESS(
                "[OK] Seeded 5 demo logins (Password for all: Inventra@2026):\n"
                "  - Admin:          admin@chanakyauniversity.edu.in\n"
                "  - Faculty:        faculty@chanakyauniversity.edu.in\n"
                "  - CS Assistant:   vinod.cs@chanakyauniversity.edu.in (Scoped to CS Lab)\n"
                "  - ECE Assistant:  vinay.elec@chanakyauniversity.edu.in (Scoped to Electronics Lab)\n"
                "  - Student:        student@chanakyauniversity.edu.in"
            ))
