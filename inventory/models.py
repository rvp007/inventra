import uuid
import qrcode
from io import BytesIO
from django.core.files import File
from django.db import models
from django.contrib.auth.models import User
from django.db.models import Q, CheckConstraint


# 1. USER PROFILE (Role-Based Access Control - FR13)
class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('STUDENT', 'Student / Researcher'),
        ('FACULTY', 'Faculty'),
        ('ASSISTANT', 'Lab Assistant / Technician'),
        ('ADMIN', 'Admin (Lab In-charge / HOD)'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STUDENT')
    department = models.CharField(max_length=100, blank=True)
    penalty_flags = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


# 2. LABS TABLE (Supports multiple labs & departments - Scalability NFR)
class Lab(models.Model):
    lab_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lab_name = models.CharField(max_length=100, unique=True)
    in_charge = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_labs')

    def __str__(self):
        return self.lab_name


# 3. ASSETS / INVENTORY TABLE (FR1, FR2, FR3, FR10, FR17)
class Asset(models.Model):
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

    asset_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=100, help_text="e.g., Microcontroller, Glassware, Heavy Machinery")
    serial_number = models.CharField(max_length=100, unique=True, help_text="Unique manufacturer serial no. (TC6)")
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name='assets')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE')
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='GOOD')

    stock_quantity = models.PositiveIntegerField(default=1, help_text="Available units (Cannot be negative - FR17)")
    low_stock_threshold = models.PositiveIntegerField(default=2, help_text="Triggers alert when stock drops below this (FR10)")
    purchase_date = models.DateField(null=True, blank=True)

    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)

    class Meta:
        constraints = [
            CheckConstraint(condition=Q(stock_quantity__gte=0), name='stock_quantity_non_negative')
        ]

    def save(self, *args, **kwargs):
        # Automatically move Damaged or Unusable items to Under Maintenance (FR8, TC15)
        if self.condition in ['DAMAGED', 'UNUSABLE']:
            self.status = 'MAINTENANCE'

        # Automatically generate QR code on first save (FR2)
        if not self.qr_code:
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(f"SCALE-ASSET:{self.asset_id}|{self.serial_number}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            file_name = f"qr_{self.serial_number}.png"
            self.qr_code.save(file_name, File(buffer), save=False)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.serial_number}) - {self.get_status_display()}"


# 4. TRANSACTIONS / AUDIT TRAIL TABLE (FR5, FR6, FR7, FR8, FR11)
class Transaction(models.Model):
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
    request_status = models.CharField(max_length=20, choices=REQUEST_STATUS, default='PENDING')

    requested_at = models.DateTimeField(auto_now_add=True)
    issue_date = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    return_date = models.DateTimeField(null=True, blank=True)
    return_condition = models.CharField(max_length=20, choices=Asset.CONDITION_CHOICES, null=True, blank=True)

    def __str__(self):
        return f"{self.asset.name} -> {self.borrower.username} ({self.request_status})"


# 5. RESERVATIONS TABLE (FR15, FR16, TC25, TC26)
class Reservation(models.Model):
    RESERVATION_STATUS = [
        ('ACTIVE', 'Active'),
        ('FULFILLED', 'Fulfilled'),
        ('EXPIRED', 'Expired (Uncollected > 30 mins)'),
        ('CANCELLED', 'Cancelled'),
    ]

    reservation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='reservations')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reservations')

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=RESERVATION_STATUS, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.asset.name} reserved by {self.user.username} ({self.start_time})"


# 6. MAINTENANCE LOGS TABLE (FR12, TC20)
class MaintenanceLog(models.Model):
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
    resolution_status = models.CharField(max_length=20, choices=RESOLUTION_STATUS, default='OPEN')

    def __str__(self):
        return f"Ticket {str(self.ticket_id)[:8]} - {self.asset.name} ({self.resolution_status})"
