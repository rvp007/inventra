"""
INVENTRA Autonomous Domain Services
All state mutations execute inside atomic PostgreSQL transactions with row-level locks.
"""
import os
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, F
from django.utils import timezone
from typing import Any

from .models import EmailOTP, UserProfile, Lab, Asset, Transaction, Reservation, MaintenanceLog

# Configurable business policy constants
ALLOWED_EMAIL_DOMAINS = [
    d.strip().lower()
    for d in os.getenv('ALLOWED_EMAIL_DOMAINS', 'chanakyauniversity.edu.in').split(',')
    if d.strip()
]
RESERVATION_MIN_ADVANCE_MINUTES = 60    # Must book at least 1 hour prior
RESERVATION_MAX_ADVANCE_MINUTES = 120   # Cannot book more than 2 hours ahead (prevents overflow)
RESERVATION_MAX_DURATION_MINUTES = 120  # Max 2 hours per machine slot
RESERVATION_EXPIRY_MINUTES = 30         # Auto-expire if uncollected 30 mins after start_time
MIN_REASON_LENGTH = 5                   # Minimum characters for mandatory borrow reason


# ==============================================================================
# 1. COLLEGE EMAIL OTP REGISTRATION & RBAC
# ==============================================================================

def validate_college_email(email: str) -> str:
    """Ensures every user registers with an institutional college email address."""
    email = (email or '').strip().lower()
    if '@' not in email:
        raise ValidationError("Please enter a valid college email address.")
    domain = email.split('@')[-1]
    if ALLOWED_EMAIL_DOMAINS and '*' not in ALLOWED_EMAIL_DOMAINS and domain not in ALLOWED_EMAIL_DOMAINS:
        allowed_str = ', '.join(f"@{d}" for d in ALLOWED_EMAIL_DOMAINS)
        raise ValidationError(f"Registration requires a valid college email ({allowed_str}).")
    return email


def request_registration_otp(email: str, role: str = 'STUDENT', department: str = '') -> EmailOTP:
    """Validates college email, generates a 6-digit OTP, and dispatches it via email."""
    clean_email = validate_college_email(email)
    valid_roles = {r[0] for r in UserProfile.ROLE_CHOICES}
    if role not in valid_roles:
        raise ValidationError(f"Invalid role '{role}'. Must be one of {sorted(valid_roles)}.")

    if User.objects.filter(email__iexact=clean_email).exists():
        raise ValidationError("An account with this college email already exists. Please log in.")

    otp = EmailOTP.generate(email=clean_email, role=role, department=department)

    send_mail(
        subject="INVENTRA — Your Campus Registration OTP",
        message=(
            f"Welcome to INVENTRA (Chanakya University Smart Lab Inventory).\n\n"
            f"Your 6-digit verification code is: {otp.code}\n"
            f"This code expires in 10 minutes.\n"
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@inventra.chanakyauniversity.edu.in'),
        recipient_list=[clean_email],
        fail_silently=False,
    )
    return otp


@transaction.atomic
def verify_otp_and_register(email: str, code: str, password: str, full_name: str = '') -> User:
    """Verifies OTP and atomically creates User + verified UserProfile."""
    clean_email = validate_college_email(email)
    if not password or len(password) < 8:
        raise ValidationError("Password must be at least 8 characters long.")

    otp = EmailOTP.objects.select_for_update().filter(
        email=clean_email, code=str(code).strip(), is_used=False
    ).order_by('-created_at').first()

    if not otp or not otp.is_valid():
        raise ValidationError("Invalid or expired OTP. Please request a new code.")

    otp.is_used = True
    otp.save(update_fields=['is_used'])

    username = clean_email.split('@')[0]
    base_username = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1

    parts = full_name.strip().split(' ', 1)
    first_name = parts[0] if parts else username
    last_name = parts[1] if len(parts) > 1 else ''

    user = User.objects.create_user(
        username=username,
        email=clean_email,
        password=password,
        first_name=first_name,
        last_name=last_name,
    )
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            'role': otp.role,
            'department': otp.department,
            'is_email_verified': True,
        }
    )
    return user


def get_user_profile(user: Any) -> UserProfile:
    """Safely fetches or creates the UserProfile without triggering Pylance reverse-attribute warnings."""
    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'role': 'ADMIN' if getattr(user, 'is_superuser', False) else 'STUDENT',
            'is_email_verified': getattr(user, 'is_superuser', False),
        }
    )
    return profile


def get_user_role(user: Any) -> str:
    return get_user_profile(user).role


# ==============================================================================
# 2. UNIVERSAL CROSS-LAB LIVE SEARCH
# ==============================================================================

def universal_asset_search(query: str = '', lab_id: str = '', status: str = '', category: str = '', asset_type: str = ''):
    """
    Searches across ALL campus labs simultaneously.
    If an item (e.g., 'Oscilloscope') exists in both Physics and Chemistry labs,
    both entries are returned with their respective lab details, stock, status, and extra_metadata.
    """
    expire_stale_reservations()

    qs = Asset.objects.select_related('lab').all()
    if query:
        q = query.strip()
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(asset_tag__icontains=q) |
            Q(serial_number__icontains=q) |
            Q(category__icontains=q) |
            Q(lab__lab_name__icontains=q) |
            Q(lab__lab_code__icontains=q)
        )
    if lab_id:
        qs = qs.filter(lab__lab_id=lab_id)
    if status:
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category__icontains=category)
    if asset_type:
        qs = qs.filter(asset_type=asset_type)

    results = []
    for asset in qs.order_by('name', 'lab__lab_name')[:200]:
        results.append({
            'asset_id': str(asset.asset_id),
            'asset_tag': asset.asset_tag,
            'name': asset.name,
            'category': asset.category,
            'asset_type': asset.asset_type,
            'serial_number': asset.serial_number,
            'lab': {
                'lab_id': str(asset.lab.lab_id),
                'lab_name': asset.lab.lab_name,
                'lab_code': asset.lab.lab_code,
            },
            'status': asset.status,
            'condition': asset.condition,
            'stock_quantity': asset.stock_quantity,
            'total_quantity': asset.total_quantity,
            'is_low_stock': asset.is_low_stock,
            'qr_code_url': asset.qr_code.url if asset.qr_code else None,
            'extra_metadata': asset.extra_metadata,
        })
    return results


# ==============================================================================
# 3. AUTONOMOUS BORROW / APPROVAL / RETURN ENGINE (MANDATORY REASON)
# ==============================================================================

@transaction.atomic
def create_borrow_request(user: Any, asset_id, quantity: int = 1, borrow_reason: str = '') -> Transaction:
    """Submits a borrow request. Rejects empty/vague reasons immediately."""
    expire_stale_reservations()

    clean_reason = (borrow_reason or '').strip()
    if len(clean_reason) < MIN_REASON_LENGTH:
        raise ValidationError(
            f"A clear reason for borrowing is mandatory (at least {MIN_REASON_LENGTH} characters)."
        )

    asset = Asset.objects.select_for_update().get(asset_id=asset_id)
    if quantity <= 0:
        raise ValidationError("Quantity must be at least 1.")
    if asset.status in ['MAINTENANCE', 'LOST']:
        raise ValidationError(f"{asset.name} is currently {asset.status} and unavailable.")
    if asset.stock_quantity < quantity:
        raise ValidationError(f"Insufficient stock in {asset.lab.lab_name}. Available: {asset.stock_quantity}.")

    return Transaction.objects.create(
        asset=asset,
        borrower=user,
        quantity=quantity,
        borrow_reason=clean_reason,
        request_status='PENDING',
    )


@transaction.atomic
def approve_and_issue_request(transaction_id, issuer_user: Any, loan_days: int = 7) -> Transaction:
    """
    Strict Lab Assistant / Admin approval gate:
    1. Blocks approval if borrow_reason is missing.
    2. Autonomously deducts stock and updates Asset status under a row lock.
    """
    role = get_user_role(issuer_user)
    if role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Lab Assistants or Admins can approve borrow requests.")

    tx = Transaction.objects.select_for_update().select_related('asset').get(transaction_id=transaction_id)
    asset = Asset.objects.select_for_update().get(asset_id=tx.asset.asset_id)

    if tx.request_status != 'PENDING':
        raise ValidationError("Only PENDING requests can be approved.")
    if not tx.borrow_reason or len(tx.borrow_reason.strip()) < MIN_REASON_LENGTH:
        raise ValidationError("Approval blocked: borrower did not specify a valid reason for borrowing.")
    if asset.status in ['MAINTENANCE', 'LOST']:
        raise ValidationError(f"Approval blocked: {asset.name} is currently {asset.status}.")
    if asset.stock_quantity < tx.quantity:
        raise ValidationError(f"Insufficient stock. Requested: {tx.quantity}, Available: {asset.stock_quantity}.")

    # Autonomous quantity & state transition
    asset.stock_quantity -= tx.quantity
    asset.status = 'ISSUED' if asset.stock_quantity == 0 else 'AVAILABLE'
    asset.save()

    now = timezone.now()
    tx.issuer = issuer_user
    tx.request_status = 'APPROVED'
    tx.issue_date = now
    tx.due_date = now + timedelta(days=max(1, int(loan_days)))
    tx.save()
    return tx


@transaction.atomic
def reject_borrow_request(transaction_id, issuer_user: Any, rejection_note: str = '') -> Transaction:
    role = get_user_role(issuer_user)
    if role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Lab Assistants or Admins can reject borrow requests.")

    tx = Transaction.objects.select_for_update().get(transaction_id=transaction_id)
    if tx.request_status != 'PENDING':
        raise ValidationError("Only PENDING requests can be rejected.")

    tx.issuer = issuer_user
    tx.request_status = 'REJECTED'
    tx.rejection_note = (rejection_note or '').strip()
    tx.save()
    return tx


@transaction.atomic
def process_equipment_return(transaction_id, staff_user: Any, return_condition: str, notes: str = '') -> Transaction:
    """
    Autonomously restores stock, tracks overdue penalties, and opens a
    MaintenanceLog ticket if returned DAMAGED or UNUSABLE.
    """
    role = get_user_role(staff_user)
    if role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Lab Assistants or Admins can log equipment returns.")

    tx = Transaction.objects.select_for_update().select_related('asset', 'borrower').get(transaction_id=transaction_id)
    asset = Asset.objects.select_for_update().get(asset_id=tx.asset.asset_id)

    if tx.request_status != 'APPROVED':
        raise ValidationError("Only active (APPROVED) loans can be returned.")

    now = timezone.now()
    tx.return_date = now
    tx.return_condition = return_condition
    tx.request_status = 'RETURNED'
    tx.save()

    # Autonomous overdue penalty tracking
    if tx.due_date and now > tx.due_date:
        UserProfile.objects.filter(user=tx.borrower).update(penalty_flags=F('penalty_flags') + 1)

    # Autonomous stock restoration & maintenance routing (consumables don't return stock)
    if asset.asset_type != 'CONSUMABLE':
        asset.stock_quantity += tx.quantity
    asset.condition = return_condition

    if return_condition in ['DAMAGED', 'UNUSABLE']:
        asset.status = 'MAINTENANCE'
        MaintenanceLog.objects.create(
            asset=asset,
            reported_by=staff_user,
            description=notes or f"Auto-flagged on return by {tx.borrower.username} ({return_condition}).",
            resolution_status='OPEN',
        )
    else:
        asset.status = 'AVAILABLE' if asset.stock_quantity > 0 else 'ISSUED'

    asset.save()
    return tx


# ==============================================================================
# 4. STRICT TIME-SLOT MACHINE RESERVATIONS (1-2 HR ADVANCE + 30-MIN EXPIRY)
# ==============================================================================

def expire_stale_reservations() -> int:
    """
    Autonomously expires ACTIVE reservations uncollected 30 minutes past start_time
    and frees the machine back to AVAILABLE.
    """
    cutoff = timezone.now() - timedelta(minutes=RESERVATION_EXPIRY_MINUTES)
    stale = Reservation.objects.select_related('asset').filter(
        status='ACTIVE',
        checked_in_at__isnull=True,
        start_time__lte=cutoff,
    )
    count = 0
    for res in stale:
        with transaction.atomic():
            res.status = 'EXPIRED'
            res.save(update_fields=['status'])
            if res.asset.status == 'RESERVED':
                res.asset.status = 'AVAILABLE'
                res.asset.save(update_fields=['status'])
            count += 1
    return count


@transaction.atomic
def create_time_slot_reservation(user: Any, asset_id, start_time, end_time, purpose: str = '') -> Reservation:
    """
    Strict machine slot booking:
    - Must be booked between 1 and 2 hours in advance (prevents request overflow).
    - Slot duration capped at 2 hours.
    - Blocks overlapping bookings atomically.
    - Auto-expires 30 minutes after start_time if user does not check in.
    """
    expire_stale_reservations()
    now = timezone.now()

    if start_time >= end_time:
        raise ValidationError("End time must be after start time.")

    lead_time = start_time - now
    if lead_time < timedelta(minutes=RESERVATION_MIN_ADVANCE_MINUTES):
        raise ValidationError("Strict booking rule: slots must be booked at least 1 hour before start time.")
    if lead_time > timedelta(minutes=RESERVATION_MAX_ADVANCE_MINUTES):
        raise ValidationError("Strict booking rule: to prevent overflow, slots can only be booked 1 to 2 hours in advance.")

    if (end_time - start_time) > timedelta(minutes=RESERVATION_MAX_DURATION_MINUTES):
        raise ValidationError("Single booking duration cannot exceed 2 hours.")

    asset = Asset.objects.select_for_update().get(asset_id=asset_id)
    if asset.status in ['MAINTENANCE', 'LOST']:
        raise ValidationError(f"{asset.name} is currently {asset.status} and cannot be booked.")

    # Prevent user from hoarding multiple active slots simultaneously
    if Reservation.objects.filter(user=user, status='ACTIVE', end_time__gt=now).exists():
        raise ValidationError("You already have an active machine reservation. Complete or cancel it first.")

    # Overlap check: (existing_start < new_end) AND (existing_end > new_start)
    if Reservation.objects.filter(
        asset=asset, status='ACTIVE', start_time__lt=end_time, end_time__gt=start_time
    ).exists():
        raise ValidationError("This machine is already reserved during the selected time slot.")

    return Reservation.objects.create(
        asset=asset,
        user=user,
        start_time=start_time,
        end_time=end_time,
        purpose=(purpose or '').strip(),
        status='ACTIVE',
    )


@transaction.atomic
def checkin_reservation(reservation_id, staff_user: Any) -> Reservation:
    """Marks a reservation as checked in so the 30-minute expiry timer stops."""
    expire_stale_reservations()
    role = get_user_role(staff_user)
    if role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Lab Assistants or Admins can check in machine bookings.")

    res = Reservation.objects.select_for_update().get(reservation_id=reservation_id)
    if res.status != 'ACTIVE':
        raise ValidationError(f"Cannot check in: reservation is {res.status}.")

    res.checked_in_at = timezone.now()
    res.status = 'FULFILLED'
    res.save(update_fields=['checked_in_at', 'status'])
    return res

@transaction.atomic
def cancel_reservation(reservation_id, user: Any) -> Reservation:
    res = Reservation.objects.select_for_update().select_related('asset', 'user').get(reservation_id=reservation_id)
    role = get_user_role(user)
    if res.user.pk != user.pk and role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("You can only cancel your own reservations.")
    if res.status != 'ACTIVE':
        raise ValidationError(f"Cannot cancel a reservation that is already {res.status}.")
    res.status = 'CANCELLED'
    res.save(update_fields=['status'])
    if res.asset.status == 'RESERVED':
        res.asset.status = 'AVAILABLE'
        res.asset.save(update_fields=['status'])
    return res


@transaction.atomic
def save_asset_record(staff_user: Any, data: dict) -> Asset:
    """Creates or updates an Asset (Admin / Lab Assistant only)."""
    role = get_user_role(staff_user)
    if role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Admins or Lab Assistants can manage inventory records.")

    lab_id = data.get('lab_id')
    lab_name = (data.get('lab_name') or '').strip()
    if lab_id:
        lab = Lab.objects.get(lab_id=lab_id)
    elif lab_name:
        clean_code = ''.join(c for c in lab_name if c.isalnum()).upper()[:8] or 'GEN'
        lab, _ = Lab.objects.get_or_create(lab_name=lab_name, defaults={'lab_code': clean_code})
    else:
        raise ValidationError("A laboratory must be selected.")

    qty = max(0, int(data.get('stock_quantity', 1)))
    asset_id = data.get('asset_id')

    if asset_id:
        asset = Asset.objects.select_for_update().get(asset_id=asset_id)
        asset.name = (data.get('name') or asset.name).strip()
        asset.category = (data.get('category') or asset.category).strip()
        asset.asset_type = data.get('asset_type') or asset.asset_type
        asset.lab = lab
        asset.stock_quantity = qty
        if qty > asset.total_quantity:
            asset.total_quantity = qty
        asset.condition = data.get('condition') or asset.condition
        asset.save()
        return asset

    return Asset.objects.create(
        name=(data.get('name') or '').strip(),
        category=(data.get('category') or 'General Equipment').strip(),
        asset_type=data.get('asset_type') or 'SHARED',
        serial_number=(data.get('serial_number') or '').strip(),
        lab=lab,
        stock_quantity=qty,
        total_quantity=max(1, qty),
        condition=data.get('condition') or 'GOOD',
        extra_metadata={'notes': data.get('description', '')} if data.get('description') else {},
    )


@transaction.atomic
def resolve_maintenance_ticket(ticket_id, staff_user: Any) -> MaintenanceLog:
    role = get_user_role(staff_user)
    if role not in ['ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Admins or Lab Assistants can resolve maintenance tickets.")
    ticket = MaintenanceLog.objects.select_for_update().select_related('asset').get(ticket_id=ticket_id)
    ticket.resolution_status = 'RESOLVED'
    ticket.resolved_at = timezone.now()
    ticket.save(update_fields=['resolution_status', 'resolved_at'])

    asset = ticket.asset
    asset.condition = 'GOOD'
    asset.status = 'AVAILABLE' if asset.stock_quantity > 0 else 'ISSUED'
    asset.save()
    return ticket
