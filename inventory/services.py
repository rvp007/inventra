"""
INVENTRA Autonomous Domain Services (inventory/services.py)
Enforces strict lab-scoped RBAC for Lab Assistants, live institutional email OTP,
row-locked borrow/return workflows, strict machine reservations, and Lab/Asset CRUD.
"""
import os
from datetime import timedelta
from typing import Any
import json
import urllib.request

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, F
from django.utils import timezone

from .models import EmailOTP, UserProfile, Lab, Asset, Transaction, Reservation, MaintenanceLog

ALLOWED_EMAIL_DOMAINS = [
    d.strip().lower()
    for d in os.getenv('ALLOWED_EMAIL_DOMAINS', 'chanakyauniversity.edu.in').split(',')
    if d.strip()
]
RESERVATION_MIN_ADVANCE_MINUTES = 60
RESERVATION_MAX_ADVANCE_MINUTES = 120
RESERVATION_MAX_DURATION_MINUTES = 120
RESERVATION_EXPIRY_MINUTES = 30
MIN_REASON_LENGTH = 5


# ==============================================================================
# 1. PROFILE, ROLE & STRICT LAB-SCOPE GUARD
# ==============================================================================

def get_user_profile(user: Any) -> UserProfile:
    profile, _ = UserProfile.objects.select_related('assigned_lab').get_or_create(
        user=user,
        defaults={
            'role': 'ADMIN' if getattr(user, 'is_superuser', False) else 'STUDENT',
            'is_email_verified': getattr(user, 'is_superuser', False),
        },
    )
    return profile


def get_user_role(user: Any) -> str:
    return get_user_profile(user).role


def assert_lab_access(staff_user: Any, target_lab: Lab) -> None:
    """
    CRITICAL RBAC GUARD:
    - ADMIN can manage all laboratories.
    - ASSISTANT is strictly restricted to their assigned_lab only.
    """
    profile = get_user_profile(staff_user)
    if profile.role == 'ADMIN':
        return
    if profile.role == 'ASSISTANT':
        if profile.assigned_lab is None:
            raise PermissionDenied(
                "Lab Scope Violation: Your Lab Assistant account has no assigned lab. Contact Admin."
            )
        if profile.assigned_lab.lab_id != target_lab.lab_id:
            raise PermissionDenied(
                f"Lab Scope Violation: You are assigned to '{profile.assigned_lab.lab_name}' "
                f"and cannot manage assets or requests belonging to '{target_lab.lab_name}'."
            )
        return
    raise PermissionDenied("Insufficient permissions for laboratory management actions.")


# ==============================================================================
# 2. LIVE COLLEGE EMAIL OTP REGISTRATION
# ==============================================================================

def validate_college_email(email: str) -> str:
    email = (email or '').strip().lower()
    if '@' not in email:
        raise ValidationError("Please enter a valid college email address.")
    domain = email.split('@')[-1]
    if ALLOWED_EMAIL_DOMAINS and '*' not in ALLOWED_EMAIL_DOMAINS and domain not in ALLOWED_EMAIL_DOMAINS:
        allowed_str = ', '.join(f"@{d}" for d in ALLOWED_EMAIL_DOMAINS)
        raise ValidationError(f"Registration requires a valid college email ({allowed_str}).")
    return email


def request_registration_otp(
    email: str,
    role: str = 'STUDENT',
    department: str = '',
    assigned_lab_id: str = '',
) -> EmailOTP:
    clean_email = validate_college_email(email)
    valid_roles = {'STUDENT', 'FACULTY', 'ASSISTANT'}
    if role not in valid_roles:
        raise ValidationError(f"Invalid role '{role}'. Must be STUDENT, FACULTY, or ASSISTANT.")

    if User.objects.filter(email__iexact=clean_email).exists():
        raise ValidationError("An account with this college email already exists. Please sign in.")

    assigned_lab = None
    if role == 'ASSISTANT':
        if not assigned_lab_id:
            raise ValidationError("Lab Assistants must select their assigned laboratory during registration.")
        try:
            assigned_lab = Lab.objects.get(lab_id=assigned_lab_id)
        except Lab.DoesNotExist:
            raise ValidationError("Selected assigned laboratory does not exist.")

    otp = EmailOTP.generate(
        email=clean_email,
        role=role,
        department=department,
        assigned_lab=assigned_lab,
    )

    from_addr = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@inventra.chanakyauniversity.edu.in')
    lab_line_text = f"Assigned Lab: {assigned_lab.lab_name}\n" if assigned_lab else ""
    lab_line_html = (
        f'<p style="font-size:13px;"><strong>Assigned Lab:</strong> {assigned_lab.lab_name}</p>'
        if assigned_lab
        else ""
    )

    plain_body = (
        f"Welcome to INVENTRA (Chanakya University Smart Lab Inventory).\n\n"
        f"Your 6-digit verification code is: {otp.code}\n"
        f"Role requested: {role}\n"
        f"{lab_line_text}"
        f"This OTP expires in 10 minutes.\n"
    )

    html_body = (
        '<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;'
        'border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">'
        '<div style="background:#153b5b;color:#ffffff;padding:18px 24px;">'
        '<h2 style="margin:0;font-size:18px;">INVENTRA &middot; Chanakya University</h2>'
        '<p style="margin:4px 0 0;font-size:12px;color:#cbd5e1;">Smart Campus Lab Inventory Verification</p>'
        '</div>'
        '<div style="padding:24px;color:#1e293b;">'
        f'<p style="margin-top:0;">Use the 6-digit verification code below to complete your '
        f'<strong>{role}</strong> account registration:</p>'
        '<div style="font-size:32px;font-weight:800;letter-spacing:8px;text-align:center;'
        f'background:#f1f6fb;color:#153b5b;padding:16px;border-radius:8px;margin:18px 0;">{otp.code}</div>'
        f'{lab_line_html}'
        '<p style="font-size:12px;color:#64748b;margin-bottom:0;">'
        'This code expires in 10 minutes. Do not share it with anyone.</p>'
        '</div></div>'
    )

    brevo_key = (os.getenv('BREVO_API_KEY') or '').strip()
    brevo_sender = (os.getenv('BREVO_SENDER_EMAIL') or os.getenv('EMAIL_HOST_USER') or '').strip()

    if brevo_key and brevo_sender:
        payload = json.dumps({
            'sender': {'name': 'INVENTRA · Chanakya University', 'email': brevo_sender},
            'to': [{'email': clean_email}],
            'subject': 'INVENTRA — Your Chanakya University Verification OTP',
            'htmlContent': html_body,
            'textContent': plain_body,
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.brevo.com/v3/smtp/email',
            data=payload,
            headers={
                'accept': 'application/json',
                'api-key': brevo_key,
                'content-type': 'application/json',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Email API rejected request ({resp.status}).")
    else:
        send_mail(
            subject="INVENTRA — Your Chanakya University Verification OTP",
            message=plain_body,
            from_email=from_addr,
            recipient_list=[clean_email],
            html_message=html_body,
            fail_silently=False,
        )
    return otp


@transaction.atomic
def verify_otp_and_register(email: str, code: str, password: str, full_name: str = '') -> User:
    clean_email = validate_college_email(email)
    if not password or len(password) < 8:
        raise ValidationError("Password must be at least 8 characters long.")

    # Do NOT chain select_related('assigned_lab') here; PostgreSQL rejects FOR UPDATE on nullable outer joins
    otp = EmailOTP.objects.select_for_update().filter(
        email=clean_email,
        code=str(code).strip(),
        is_used=False,
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
            'assigned_lab': otp.assigned_lab,
            'is_email_verified': True,
        },
    )
    return user


# ==============================================================================
# 3. UNIVERSAL CROSS-LAB LIVE SEARCH
# ==============================================================================

def universal_asset_search(
    query: str = '',
    lab_id: str = '',
    status: str = '',
    category: str = '',
    asset_type: str = '',
):
    expire_stale_reservations()

    qs = Asset.objects.select_related('lab').all()
    if query:
        q = query.strip()
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(asset_tag__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(category__icontains=q)
            | Q(lab__lab_name__icontains=q)
            | Q(lab__lab_code__icontains=q)
            | Q(lab__location__icontains=q)
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
    for asset in qs.order_by('name', 'lab__lab_name')[:250]:
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
                'lab_type': asset.lab.lab_type,
                'location': asset.lab.location,
            },
            'status': asset.status,
            'condition': asset.condition,
            'stock_quantity': asset.stock_quantity,
            'total_quantity': asset.total_quantity,
            'is_low_stock': asset.is_low_stock,
            'qr_code_url': f'/api/qr/{asset.asset_tag}/image/',
            'extra_metadata': asset.extra_metadata,
        })
    return results


# ==============================================================================
# 4. AUTONOMOUS BORROW / APPROVAL / RETURN ENGINE (LAB-SCOPED)
# ==============================================================================

@transaction.atomic
def create_borrow_request(user: Any, asset_id, quantity: int = 1, borrow_reason: str = '') -> Transaction:
    expire_stale_reservations()
    clean_reason = (borrow_reason or '').strip()
    if len(clean_reason) < MIN_REASON_LENGTH:
        raise ValidationError(
            f"A clear reason for borrowing is mandatory (at least {MIN_REASON_LENGTH} characters)."
        )

    asset = Asset.objects.select_for_update().select_related('lab').get(asset_id=asset_id)
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
    tx = Transaction.objects.select_for_update().select_related('asset', 'asset__lab').get(
        transaction_id=transaction_id
    )
    assert_lab_access(issuer_user, tx.asset.lab)

    asset = Asset.objects.select_for_update().get(asset_id=tx.asset.asset_id)
    if tx.request_status != 'PENDING':
        raise ValidationError("Only PENDING requests can be approved.")
    if not tx.borrow_reason or len(tx.borrow_reason.strip()) < MIN_REASON_LENGTH:
        raise ValidationError("Approval blocked: borrower did not specify a valid reason for borrowing.")
    if asset.status in ['MAINTENANCE', 'LOST']:
        raise ValidationError(f"Approval blocked: {asset.name} is currently {asset.status}.")
    if asset.stock_quantity < tx.quantity:
        raise ValidationError(f"Insufficient stock. Requested: {tx.quantity}, Available: {asset.stock_quantity}.")

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
    tx = Transaction.objects.select_for_update().select_related('asset', 'asset__lab').get(
        transaction_id=transaction_id
    )
    assert_lab_access(issuer_user, tx.asset.lab)

    if tx.request_status != 'PENDING':
        raise ValidationError("Only PENDING requests can be rejected.")

    tx.issuer = issuer_user
    tx.request_status = 'REJECTED'
    tx.rejection_note = (rejection_note or '').strip()
    tx.save()
    return tx

@transaction.atomic
def cancel_borrow_request(transaction_id, user: Any) -> Transaction:
    tx = Transaction.objects.select_for_update().select_related(
        'asset', 'asset__lab', 'borrower'
    ).get(transaction_id=transaction_id)

    is_owner = tx.borrower.pk == user.pk
    if not is_owner:
        assert_lab_access(user, tx.asset.lab)

    if tx.request_status == 'PENDING':
        tx.request_status = 'CANCELLED'
        tx.save(update_fields=['request_status'])
        return tx

    if tx.request_status == 'APPROVED':
        if is_owner and get_user_role(user) not in ['ASSISTANT', 'ADMIN']:
            raise PermissionDenied(
                "Already-issued items must be returned or cancelled by the Lab Assistant."
            )
        asset = Asset.objects.select_for_update().get(asset_id=tx.asset.asset_id)
        if asset.asset_type != 'CONSUMABLE':
            asset.stock_quantity += tx.quantity
        if asset.status != 'MAINTENANCE' and asset.stock_quantity > 0:
            asset.status = 'AVAILABLE'
        asset.save()
        tx.request_status = 'CANCELLED'
        tx.save(update_fields=['request_status'])
        return tx

    raise ValidationError(f"Cannot cancel a request that is already {tx.request_status}.")

@transaction.atomic
def process_equipment_return(transaction_id, staff_user: Any, return_condition: str, notes: str = '') -> Transaction:
    tx = Transaction.objects.select_for_update().select_related('asset', 'asset__lab', 'borrower').get(
        transaction_id=transaction_id
    )
    assert_lab_access(staff_user, tx.asset.lab)

    asset = Asset.objects.select_for_update().get(asset_id=tx.asset.asset_id)
    if tx.request_status != 'APPROVED':
        raise ValidationError("Only active (APPROVED) loans can be returned.")

    now = timezone.now()
    tx.return_date = now
    tx.return_condition = return_condition
    tx.request_status = 'RETURNED'
    tx.save()

    if tx.due_date and now > tx.due_date:
        UserProfile.objects.filter(user=tx.borrower).update(penalty_flags=F('penalty_flags') + 1)

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
# 5. STRICT TIME-SLOT MACHINE RESERVATIONS (LAB-SCOPED CHECK-IN)
# ==============================================================================

def expire_stale_reservations() -> int:
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
    if asset.asset_type != 'TIME_SLOTTED':
        raise ValidationError(
            "Machine slot booking is only available for time-slotted lab machines "
            "(e.g., 3D Printers, Laser Cutters, CNC machines)."
        )
    if asset.status in ['MAINTENANCE', 'LOST']:
        raise ValidationError(f"{asset.name} is currently {asset.status} and cannot be booked.")

    if Reservation.objects.filter(user=user, status='ACTIVE', end_time__gt=now).exists():
        raise ValidationError("You already have an active machine reservation. Complete or cancel it first.")

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
    expire_stale_reservations()
    res = Reservation.objects.select_for_update().select_related('asset', 'asset__lab').get(
        reservation_id=reservation_id
    )
    assert_lab_access(staff_user, res.asset.lab)

    if res.status != 'ACTIVE':
        raise ValidationError(f"Cannot check in: reservation is {res.status}.")

    res.checked_in_at = timezone.now()
    res.status = 'FULFILLED'
    res.save(update_fields=['checked_in_at', 'status'])
    return res


@transaction.atomic
def cancel_reservation(reservation_id, user: Any) -> Reservation:
    res = Reservation.objects.select_for_update().select_related('asset', 'asset__lab', 'user').get(
        reservation_id=reservation_id
    )
    if res.user.pk != user.pk:
        assert_lab_access(user, res.asset.lab)
    if res.status != 'ACTIVE':
        raise ValidationError(f"Cannot cancel a reservation that is already {res.status}.")
    res.status = 'CANCELLED'
    res.save(update_fields=['status'])
    if res.asset.status == 'RESERVED':
        res.asset.status = 'AVAILABLE'
        res.asset.save(update_fields=['status'])
    return res


# ==============================================================================
# 6. LAB CRUD, ASSET CRUD (LAB-SCOPED) & MAINTENANCE RESOLUTION
# ==============================================================================

@transaction.atomic
def save_lab_record(staff_user: Any, data: dict) -> Lab:
    role = get_user_role(staff_user)
    if role not in ['FACULTY', 'ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Faculty, Lab Assistants, or Admins can manage laboratories.")

    lab_name = (data.get('lab_name') or '').strip()
    if not lab_name:
        raise ValidationError("Laboratory name is required.")

    lab_code = (data.get('lab_code') or '').strip().upper()
    lab_type = data.get('lab_type') or 'COMPUTER_SCIENCE'
    location = (data.get('location') or '').strip()
    instructor_name = (data.get('instructor_name') or '').strip()
    lab_id = data.get('lab_id')

    if lab_id:
        lab = Lab.objects.select_for_update().get(lab_id=lab_id)
        if role == 'ASSISTANT':
            assert_lab_access(staff_user, lab)
        lab.lab_name = lab_name
        if lab_code:
            lab.lab_code = lab_code
        lab.lab_type = lab_type
        lab.location = location
        lab.instructor_name = instructor_name
        lab.save()
        return lab

    if role == 'ASSISTANT':
        raise PermissionDenied("Lab Assistants can only edit their assigned laboratory, not create new labs.")
    if not lab_code:
        lab_code = ''.join(c for c in lab_name if c.isalnum()).upper()[:10] or 'GEN'

    return Lab.objects.create(
        lab_name=lab_name,
        lab_code=lab_code,
        lab_type=lab_type,
        location=location,
        instructor_name=instructor_name,
        in_charge=staff_user,
    )


@transaction.atomic
def delete_lab_record(lab_id, staff_user: Any) -> str:
    role = get_user_role(staff_user)
    if role not in ['FACULTY', 'ADMIN']:
        raise PermissionDenied("Only Admins or Subject Faculty can delete a laboratory.")
    lab = Lab.objects.select_for_update().get(lab_id=lab_id)
    name = lab.lab_name
    lab.delete()
    return name


@transaction.atomic
def save_asset_record(staff_user: Any, data: dict) -> Asset:
    role = get_user_role(staff_user)
    if role not in ['FACULTY', 'ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Staff or Admins can add or edit equipment.")

    lab_id = data.get('lab_id')
    if not lab_id:
        raise ValidationError("A laboratory must be selected.")
    lab = Lab.objects.get(lab_id=lab_id)

    if role == 'ASSISTANT':
        assert_lab_access(staff_user, lab)

    qty = max(0, int(data.get('stock_quantity', 1)))
    asset_id = data.get('asset_id')

    clean_serial = (data.get('serial_number') or '').strip()
    if clean_serial:
        dup_qs = Asset.objects.filter(serial_number__iexact=clean_serial)
        if asset_id:
            dup_qs = dup_qs.exclude(asset_id=asset_id)
        if dup_qs.exists():
            raise ValidationError(f"Duplicate serial number '{clean_serial}'. Save rejected.")

    if asset_id:
        asset = Asset.objects.select_for_update().select_related('lab').get(asset_id=asset_id)
        if clean_serial:
            asset.serial_number = clean_serial
        if role == 'ASSISTANT':
            assert_lab_access(staff_user, asset.lab)
        asset.name = (data.get('name') or asset.name).strip()
        asset.category = (data.get('category') or asset.category).strip()
        asset.asset_type = data.get('asset_type') or asset.asset_type
        asset.lab = lab
        asset.stock_quantity = qty
        if qty > asset.total_quantity:
            asset.total_quantity = qty
        asset.condition = data.get('condition') or asset.condition
        if data.get('specifications'):
            extras = dict(asset.extra_metadata or {})
            extras['specifications'] = str(data.get('specifications')).strip()
            asset.extra_metadata = extras
        asset.save()
        return asset

    extras = {}
    if data.get('specifications'):
        extras['specifications'] = str(data.get('specifications')).strip()
    if data.get('unit_cost'):
        extras['unit_cost'] = str(data.get('unit_cost')).strip()

    return Asset.objects.create(
        name=(data.get('name') or '').strip(),
        category=(data.get('category') or 'General Equipment').strip(),
        asset_type=data.get('asset_type') or 'SHARED',
        serial_number=(data.get('serial_number') or '').strip(),
        lab=lab,
        stock_quantity=qty,
        total_quantity=max(1, qty),
        condition=data.get('condition') or 'GOOD',
        extra_metadata=extras,
    )


@transaction.atomic
def delete_asset_record(asset_id, staff_user: Any) -> str:
    role = get_user_role(staff_user)
    if role not in ['FACULTY', 'ASSISTANT', 'ADMIN']:
        raise PermissionDenied("Only Staff or Admins can delete equipment.")
    asset = Asset.objects.select_for_update().select_related('lab').get(asset_id=asset_id)
    if role == 'ASSISTANT':
        assert_lab_access(staff_user, asset.lab)
    tag = asset.asset_tag
    asset.delete()
    return tag


@transaction.atomic
def resolve_maintenance_ticket(ticket_id, staff_user: Any) -> MaintenanceLog:
    ticket = MaintenanceLog.objects.select_for_update().select_related('asset', 'asset__lab').get(
        ticket_id=ticket_id
    )
    assert_lab_access(staff_user, ticket.asset.lab)

    ticket.resolution_status = 'RESOLVED'
    ticket.resolved_at = timezone.now()
    ticket.save(update_fields=['resolution_status', 'resolved_at'])

    asset = ticket.asset
    asset.condition = 'GOOD'
    asset.status = 'AVAILABLE' if asset.stock_quantity > 0 else 'ISSUED'
    asset.save()
    return ticket

@transaction.atomic
def report_asset_fault(asset_id, user: Any, description: str, mark_maintenance: bool = False) -> MaintenanceLog:
    clean_desc = (description or '').strip()
    if len(clean_desc) < 5:
        raise ValidationError("Please describe the fault in at least 5 characters.")

    asset = Asset.objects.select_for_update().select_related('lab').get(asset_id=asset_id)
    ticket = MaintenanceLog.objects.create(
        asset=asset,
        reported_by=user,
        description=clean_desc,
        resolution_status='OPEN',
    )
    if mark_maintenance and get_user_role(user) in ['FACULTY', 'ASSISTANT', 'ADMIN']:
        asset.condition = 'DAMAGED'
        asset.status = 'MAINTENANCE'
        asset.save()
    return ticket
