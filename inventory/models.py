"""
INVENTRA Core Relational Schema
Supports 6 Lab Types, Lab-Scoped RBAC (UserProfile.assigned_lab),
Institutional Email OTP, Auto QR/Asset Tagging, JSONB Metadata,
Atomic Transactions, Strict Time-Slot Reservations, and Maintenance Logs.
"""
import uuid
import secrets
import qrcode
from io import BytesIO
from datetime import timedelta

from django.core.files import File
from django.db import models
from django.contrib.auth.models import User
from django.db.models import Q, CheckConstraint
from django.utils import timezone


class Lab(models.Model):
    """Physical campus laboratory room or store."""
    LAB_TYPE_CHOICES = [
        ('COMPUTER_SCIENCE', 'Computer Science Lab'),
        ('ELECTRONICS', 'Digital & Analog Electronics Lab'),
        ('CHEMISTRY', 'Chemistry Lab'),
        ('PHYSICS', 'Physics Lab'),
        ('MECHANICAL_MANUFACTURING', 'Mechanical & Manufacturing Lab'),
        ('IDEATION_DESIGN', 'Ideation & Design Thinking Lab'),
    ]

    lab_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lab_name = models.CharField(max_length=120, unique=True)
    lab_code = models.CharField(
        max_length=15, unique=True, default='GEN',
        help_text="Short prefix for unique asset tags, e.g., CSUB44, ELEC, PHYS, CHEM"
    )
    lab_type = models.CharField(max_length=30, choices=LAB_TYPE_CHOICES, default='COMPUTER_SCIENCE')
    location = models.CharField(max_length=100, blank=True, default='')
    instructor_name = models.CharField(max_length=150, blank=True, default='')
    in_charge = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_labs'
    )

    def save(self, *args, **kwargs):
        if not self.lab_code or self.lab_code == 'GEN':
            clean = ''.join(e for e in self.lab_name if e.isalnum()).upper()
            self.lab_code = clean[:10] or 'GEN'
        else:
            self.lab_code = ''.join(e for e in self.lab_code if e.isalnum()).upper()[:15]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.lab_name} ({self.lab_code})"


class EmailOTP(models.Model):
    """Stores 6-digit time-limited OTPs for institutional email registration."""
    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    role = models.CharField(max_length=20, default='STUDENT')
    department = models.CharField(max_length=100, blank=True, default='')
    assigned_lab = models.ForeignKey(Lab, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    @classmethod
    def generate(
        cls,
        email: str,
        role: str = 'STUDENT',
        department: str = '',
        assigned_lab=None,
        ttl_minutes: int = 10,
    ):
        cls.objects.filter(email=email.lower(), is_used=False).update(is_used=True)
        otp_code = f"{secrets.randbelow(900000) + 100000:06d}"
        return cls.objects.create(
            email=email.lower(),
            code=otp_code,
            role=role,
            department=department,
            assigned_lab=assigned_lab,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )

    def is_valid(self) -> bool:
        return (not self.is_used) and (timezone.now() <= self.expires_at)

    def __str__(self):
        return f"OTP({self.email}, used={self.is_used})"


class UserProfile(models.Model):
    """Role-Based Access Control (RBAC) profile with strict Lab Assistant scoping."""
    ROLE_CHOICES = [
        ('STUDENT', 'Student / Researcher'),
        ('FACULTY', 'Faculty'),
        ('ASSISTANT', 'Lab Assistant / Technician'),
        ('ADMIN', 'Admin (Lab In-charge / HOD)'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STUDENT')
    department = models.CharField(max_length=100, blank=True, default='')
    assigned_lab = models.ForeignKey(
        Lab, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_assistants',
        help_text="Strict lab scope for Lab Assistants"
    )
    is_email_verified = models.BooleanField(default=False)
    penalty_flags = models.PositiveIntegerField(default=0)

    def __str__(self):
        role_label = dict(self.ROLE_CHOICES).get(self.role, self.role)
        lab_str = f" @ {self.assigned_lab.lab_code}" if self.assigned_lab else ""
        return f"{self.user.username} ({role_label}{lab_str})"


class Asset(models.Model):
    """Universal equipment/consumable/machine record with auto-tagging, QR, and JSONB metadata."""
    STATUS_CHOICES = [
        ('AVAILABLE', 'Available'),
        ('RESERVED', 'Reserved'),
        ('ISSUED', 'Issued'),
        ('MAINTENANCE', 'Under Maintenance'),
        ('LOST', 'Lost'),
    ]
    CONDITION_CHOICES = [
        ('GOOD', 'Good'),
        ('MINOR_DAMAGE', 'Minor Damage'),
        ('DAMAGED', 'Damaged'),
        ('UNUSABLE', 'Unusable'),
    ]
    ASSET_TYPE_CHOICES = [
        ('SHARED', 'High-Value Shared Equipment'),
        ('CONSUMABLE', 'Consumable'),
        ('TIME_SLOTTED', 'Time-Slotted Fixed Machine'),
    ]

    asset_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset_tag = models.CharField(max_length=40, unique=True, blank=True)
    name = models.CharField(max_length=255, db_index=True)
    category = models.CharField(max_length=100, default='General Equipment', db_index=True)
    asset_type = models.CharField(max_length=20, choices=ASSET_TYPE_CHOICES, default='SHARED')
    serial_number = models.CharField(max_length=150, unique=True)
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name='assets')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE', db_index=True)
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='GOOD')

    stock_quantity = models.PositiveIntegerField(default=1)
    total_quantity = models.PositiveIntegerField(default=1)
    low_stock_threshold = models.PositiveIntegerField(default=2)
    purchase_date = models.DateField(null=True, blank=True)
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)
    extra_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            CheckConstraint(condition=Q(stock_quantity__gte=0), name='stock_quantity_non_negative')
        ]

    def save(self, *args, **kwargs):
        if not self.asset_tag and self.lab:
            prefix = f"INV-{self.lab.lab_code.upper()}-"
            last_asset = Asset.objects.filter(asset_tag__startswith=prefix).order_by('-asset_tag').first()
            next_num = 1
            if last_asset:
                try:
                    next_num = int(last_asset.asset_tag.split('-')[-1]) + 1
                except ValueError:
                    next_num = Asset.objects.filter(lab=self.lab).count() + 1
            self.asset_tag = f"{prefix}{next_num:04d}"

        if not self.serial_number or str(self.serial_number).strip().lower() in ['nan', 'none', 'na', 'n/a', '-', '']:
            self.serial_number = self.asset_tag

        if self.total_quantity < self.stock_quantity:
            self.total_quantity = self.stock_quantity

        if self.condition in ['DAMAGED', 'UNUSABLE']:
            self.status = 'MAINTENANCE'
        elif self.stock_quantity == 0 and self.status == 'AVAILABLE':
            self.status = 'ISSUED'
        elif self.stock_quantity > 0 and self.status == 'ISSUED':
            self.status = 'AVAILABLE'

        if not self.qr_code:
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(f"INVENTRA|{self.asset_tag}|{self.name}|{self.lab.lab_name}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer)
            self.qr_code.save(f"qr_{self.asset_tag}.png", File(buffer), save=False)

        super().save(*args, **kwargs)

    @property
    def is_low_stock(self) -> bool:
        return self.stock_quantity <= self.low_stock_threshold

    def __str__(self):
        status_label = dict(self.STATUS_CHOICES).get(self.status, self.status)
        return f"[{self.asset_tag}] {self.name} ({status_label})"


class Transaction(models.Model):
    """Borrow/Return audit trail with mandatory borrow_reason enforcement."""
    REQUEST_STATUS = [
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Approved & Issued'),
        ('REJECTED', 'Rejected'),
        ('RETURNED', 'Returned'),
        ('CANCELLED', 'Cancelled'),
    ]

    transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='transactions')
    borrower = models.ForeignKey(User, on_delete=models.CASCADE, related_name='borrowed_items')
    issuer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='issued_items')

    quantity = models.PositiveIntegerField(default=1)
    borrow_reason = models.TextField(default='', help_text="Mandatory justification required before approval")
    rejection_note = models.TextField(blank=True, default='')
    request_status = models.CharField(max_length=20, choices=REQUEST_STATUS, default='PENDING', db_index=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    issue_date = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    return_date = models.DateTimeField(null=True, blank=True)
    return_condition = models.CharField(max_length=20, choices=Asset.CONDITION_CHOICES, null=True, blank=True)

    def __str__(self):
        return f"{self.asset.asset_tag} -> {self.borrower.username} ({self.request_status})"


class Reservation(models.Model):
    """Strict advance-window machine booking with 30-minute no-show auto-expiry."""
    RESERVATION_STATUS = [
        ('ACTIVE', 'Active'),
        ('FULFILLED', 'Fulfilled / Checked In'),
        ('EXPIRED', 'Expired (Uncollected > 30 mins)'),
        ('CANCELLED', 'Cancelled'),
    ]

    reservation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='reservations')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reservations')

    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    purpose = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=RESERVATION_STATUS, default='ACTIVE', db_index=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.asset.asset_tag} reserved by {self.user.username} ({self.status})"


class MaintenanceLog(models.Model):
    """Automated and manual fault/repair ticket tracker."""
    RESOLUTION_STATUS = [
        ('OPEN', 'Open'),
        ('IN_PROGRESS', 'In Progress'),
        ('RESOLVED', 'Resolved'),
    ]

    ticket_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='maintenance_logs')
    reported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='reported_faults')

    description = models.TextField()
    report_date = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_status = models.CharField(max_length=20, choices=RESOLUTION_STATUS, default='OPEN', db_index=True)

    def __str__(self):
        return f"Ticket {str(self.ticket_id)[:8]} - {self.asset.asset_tag} ({self.resolution_status})"
