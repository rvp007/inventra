"""
INVENTRA JSON REST API & Role-Based Views
Enforces Lab-Scoped Assistant RBAC, Live Institutional Email OTP,
Lab/Asset CRUD, Universal Search, Dynamic QR Generation, PR Template Exports, and Multi-Role Sync.
"""
import json
import os
from functools import wraps
from io import BytesIO

import qrcode
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import F, Count
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from datetime import timedelta
from django.db import IntegrityError
from django.db.models import F, Count, Q

from .models import Lab, Asset, Transaction, Reservation, MaintenanceLog
from .services import (
    get_user_profile,
    get_user_role,
    request_registration_otp,
    verify_otp_and_register,
    universal_asset_search,
    create_borrow_request,
    approve_and_issue_request,
    reject_borrow_request,
    process_equipment_return,
    create_time_slot_reservation,
    checkin_reservation,
    cancel_borrow_request,
    cancel_reservation,
    save_lab_record,
    delete_lab_record,
    save_asset_record,
    delete_asset_record,
    resolve_maintenance_ticket,
    expire_stale_reservations,
    report_asset_fault
)
from .ingestion import ingest_file_bytes, export_inventory_bytes


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8')) if request.body else {}
    except json.JSONDecodeError:
        return {}


def api_login_required(roles=None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({'error': 'Authentication required.'}, status=401)
            role = get_user_role(request.user)
            if roles and role not in roles:
                return JsonResponse(
                    {'error': f'Access denied. Required role: {roles}, your role: {role}.'},
                    status=403,
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _serialize_user(user) -> dict:
    profile = get_user_profile(user)
    role = profile.role
    full_name = f"{user.first_name} {user.last_name}".strip() or user.username
    assigned = profile.assigned_lab
    return {
        'id': user.pk,
        'username': user.username,
        'email': user.email,
        'full_name': full_name,
        'role': role,
        'department': profile.department,
        'penalty_flags': profile.penalty_flags,
        'assigned_lab': {
            'lab_id': str(assigned.lab_id),
            'lab_name': assigned.lab_name,
            'lab_code': assigned.lab_code,
        } if assigned else None,
        'permissions': {
            'can_approve_requests': role in ['ASSISTANT', 'ADMIN'],
            'can_manage_assets': role in ['FACULTY', 'ASSISTANT', 'ADMIN'],
            'can_manage_labs': role in ['FACULTY', 'ASSISTANT', 'ADMIN'],
            'can_delete_labs': role in ['FACULTY', 'ADMIN'],
            'can_ingest_data': role in ['FACULTY', 'ASSISTANT', 'ADMIN'],
            'can_export_reports': role in ['FACULTY', 'ASSISTANT', 'ADMIN'],
        },
    }


def _serialize_tx(t: Transaction) -> dict:
    return {
        'transaction_id': str(t.transaction_id),
        'asset_id': str(t.asset.asset_id),
        'asset_tag': t.asset.asset_tag,
        'asset_name': t.asset.name,
        'lab_id': str(t.asset.lab.lab_id),
        'lab': t.asset.lab.lab_name,
        'borrower': f"{t.borrower.first_name} {t.borrower.last_name}".strip() or t.borrower.username,
        'borrower_username': t.borrower.username,
        'borrower_email': t.borrower.email,
        'quantity': t.quantity,
        'borrow_reason': t.borrow_reason,
        'rejection_note': t.rejection_note,
        'request_status': t.request_status,
        'return_condition': t.return_condition,
        'requested_at': t.requested_at.isoformat() if t.requested_at else None,
        'issue_date': t.issue_date.isoformat() if t.issue_date else None,
        'due_date': t.due_date.isoformat() if t.due_date else None,
        'return_date': t.return_date.isoformat() if t.return_date else None,
    }


# ==============================================================================
# FRONTEND ENTRY & PUBLIC LAB LIST (FOR ASSISTANT OTP REGISTRATION)
# ==============================================================================

def frontend_index(request):
    return render(request, 'index.html')


@require_http_methods(["GET"])
def api_public_labs(request):
    labs = [
        {
            'lab_id': str(l.lab_id),
            'lab_name': l.lab_name,
            'lab_code': l.lab_code,
            'lab_type': l.lab_type,
            'location': l.location,
        }
        for l in Lab.objects.all().order_by('lab_name')
    ]
    return JsonResponse({'labs': labs})


# ==============================================================================
# AUTHENTICATION & LIVE COLLEGE EMAIL OTP REGISTRATION
# ==============================================================================

@csrf_exempt
@require_http_methods(["POST"])
def api_request_otp(request):
    data = _json_body(request)
    try:
        otp = request_registration_otp(
            email=data.get('email', ''),
            role=data.get('role', 'STUDENT'),
            department=data.get('department', ''),
            assigned_lab_id=data.get('assigned_lab_id', ''),
        )
        using_live_mail = bool((os.getenv('BREVO_API_KEY') or '').strip()) or (
            'smtp' in getattr(settings, 'EMAIL_BACKEND', '').lower()
        )
        payload = {
            'message': f'A 6-digit OTP has been sent to {otp.email}. Valid for 10 minutes.',
            'live_email': using_live_mail,
        }
        if settings.DEBUG and not using_live_mail:
            payload['dev_otp'] = otp.code
        return JsonResponse(payload, status=200)
    except ValidationError as e:
        return JsonResponse({'error': e.message}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Failed to dispatch OTP email: {e}'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_verify_otp_register(request):
    data = _json_body(request)
    try:
        user = verify_otp_and_register(
            email=data.get('email', ''),
            code=data.get('code', ''),
            password=data.get('password', ''),
            full_name=data.get('full_name', ''),
        )
        login(request, user)
        return JsonResponse({
            'message': 'Account verified and registered successfully.',
            'user': _serialize_user(user),
        }, status=201)
    except ValidationError as e:
        return JsonResponse({'error': e.message}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_login(request):
    data = _json_body(request)
    identifier = (data.get('email') or data.get('username') or '').strip()
    password = data.get('password', '')

    username = identifier
    if '@' in identifier:
        matched = User.objects.filter(email__iexact=identifier).first()
        if matched:
            username = matched.username

    user = authenticate(request, username=username, password=password)
    if not user:
        return JsonResponse({'error': 'Invalid college email/username or password.'}, status=401)

    login(request, user)
    return JsonResponse({'message': 'Logged in successfully.', 'user': _serialize_user(user)})


@csrf_exempt
@require_http_methods(["POST"])
def api_logout(request):
    logout(request)
    return JsonResponse({'message': 'Logged out.'})


@require_http_methods(["GET"])
@api_login_required()
def api_me(request):
    return JsonResponse(_serialize_user(request.user))


# ==============================================================================
# ROLE & LAB-SCOPED DASHBOARD, UNIVERSAL SEARCH & QR LOOKUP/IMAGE
# ==============================================================================

@require_http_methods(["GET"])
@api_login_required()
def api_dashboard(request):
    expire_stale_reservations()
    profile = get_user_profile(request.user)
    role = profile.role
    assigned_lab = profile.assigned_lab
    now = timezone.now()

    labs = [
        {
            'lab_id': str(lab.lab_id),
            'lab_name': lab.lab_name,
            'lab_code': lab.lab_code,
            'lab_type': lab.lab_type,
            'location': lab.location,
            'instructor_name': lab.instructor_name,
            'asset_count': lab.asset_count,
        }
        for lab in Lab.objects.annotate(asset_count=Count('assets')).order_by('lab_name')
    ]

    base_assets = Asset.objects.select_related('lab').all()
    if role == 'ASSISTANT' and assigned_lab is not None:
        scoped_assets = base_assets.filter(lab=assigned_lab)
        all_tx_qs = Transaction.objects.filter(asset__lab=assigned_lab).select_related('asset', 'borrower', 'asset__lab').order_by('-requested_at')
        reservations_qs = Reservation.objects.filter(asset__lab=assigned_lab).select_related('asset', 'user', 'asset__lab').order_by('-created_at')
        tickets_qs = MaintenanceLog.objects.filter(asset__lab=assigned_lab).select_related('asset', 'reported_by', 'asset__lab').order_by('-report_date')
    elif role == 'ASSISTANT' and assigned_lab is None:
        scoped_assets = base_assets.none()
        all_tx_qs = Transaction.objects.none()
        reservations_qs = Reservation.objects.none()
        tickets_qs = MaintenanceLog.objects.none()
    elif role == 'ADMIN':
        scoped_assets = base_assets
        all_tx_qs = Transaction.objects.select_related('asset', 'borrower', 'asset__lab').order_by('-requested_at')
        reservations_qs = Reservation.objects.select_related('asset', 'user', 'asset__lab').order_by('-created_at')
        tickets_qs = MaintenanceLog.objects.select_related('asset', 'reported_by', 'asset__lab').order_by('-report_date')
    else:
        scoped_assets = base_assets
        all_tx_qs = Transaction.objects.filter(borrower=request.user).select_related('asset', 'borrower', 'asset__lab').order_by('-requested_at')
        reservations_qs = Reservation.objects.filter(user=request.user).select_related('asset', 'user', 'asset__lab').order_by('-created_at')
        tickets_qs = MaintenanceLog.objects.filter(reported_by=request.user).select_related('asset', 'reported_by', 'asset__lab').order_by('-report_date')

    low_stock_qs = scoped_assets.filter(stock_quantity__lte=F('low_stock_threshold'))
    pending_qs = all_tx_qs.filter(request_status='PENDING')
    active_qs = all_tx_qs.filter(request_status='APPROVED')
    overdue_qs = active_qs.filter(due_date__lt=now)
    due_soon_qs = active_qs.filter(due_date__gte=now, due_date__lte=now + timedelta(hours=24))

    notifications = []
    for tx in overdue_qs[:5]:
        notifications.append({
            'title': f'Overdue return: {tx.asset.name} ({tx.asset.asset_tag})',
            'detail': f'Borrowed by {tx.borrower.username} from {tx.asset.lab.lab_name}.',
            'time': tx.due_date.strftime('%d %b %Y, %I:%M %p') if tx.due_date else '',
            'icon': 'bi-clock-fill',
            'tone': 'red',
        })

    for tx in due_soon_qs[:5]:
        notifications.append({
            'title': f'Due soon: {tx.asset.name} ({tx.asset.asset_tag})',
            'detail': f'Return due before {tx.due_date.strftime("%d %b %Y, %I:%M %p")} · {tx.asset.lab.lab_name}.',
            'time': 'Due reminder',
            'icon': 'bi-alarm-fill',
            'tone': 'amber',
        })


    for a in low_stock_qs[:5]:
        notifications.append({
            'title': f'Low stock: {a.name}',
            'detail': f'{a.lab.lab_name} has {a.stock_quantity} unit(s) remaining ({a.asset_tag}).',
            'time': 'Live alert',
            'icon': 'bi-exclamation-circle-fill',
            'tone': 'amber',
        })
    for tx in all_tx_qs[:8]:
        tone = 'green' if tx.request_status in ['APPROVED', 'RETURNED'] else ('red' if tx.request_status == 'REJECTED' else 'blue')
        icon = 'bi-check-circle-fill' if tone == 'green' else ('bi-x-circle-fill' if tone == 'red' else 'bi-hourglass-split')
        notifications.append({
            'title': f'{tx.request_status.title()}: {tx.asset.name}',
            'detail': f'{tx.borrower.username} · {tx.asset.lab.lab_name} · Reason: {tx.borrow_reason or "N/A"}',
            'time': tx.requested_at.strftime('%d %b %Y, %I:%M %p') if tx.requested_at else '',
            'icon': icon,
            'tone': tone,
        })

    return JsonResponse({
        'server_time': now.isoformat(),
        'role': role,
        'assigned_lab': {
            'lab_id': str(assigned_lab.lab_id),
            'lab_name': assigned_lab.lab_name,
            'lab_code': assigned_lab.lab_code,
        } if assigned_lab else None,
        'stats': {
            'total_assets': scoped_assets.count(),
            'available_assets': scoped_assets.filter(status='AVAILABLE').count(),
            'maintenance_assets': scoped_assets.filter(status='MAINTENANCE').count(),
            'low_stock_count': low_stock_qs.count(),
            'overdue_count': overdue_qs.count(),
            'pending_requests_count': pending_qs.count(),
            'active_loans_count': active_qs.count(),
            'active_reservations_count': reservations_qs.filter(status='ACTIVE').count(),
            'open_maintenance_tickets': tickets_qs.filter(resolution_status='OPEN').count(),
        },
        'labs': labs,
        'all_requests': [_serialize_tx(t) for t in all_tx_qs[:100]],
        'pending_requests': [_serialize_tx(t) for t in pending_qs[:50]],
        'active_loans': [_serialize_tx(t) for t in active_qs[:50]],
        'reservations': [
            {
                'reservation_id': str(r.reservation_id),
                'asset_tag': r.asset.asset_tag,
                'asset_name': r.asset.name,
                'lab': r.asset.lab.lab_name,
                'user': r.user.username,
                'purpose': r.purpose,
                'start_time': r.start_time.isoformat(),
                'end_time': r.end_time.isoformat(),
                'status': r.status,
            } for r in reservations_qs[:50]
        ],
        'maintenance_tickets': [
            {
                'ticket_id': str(m.ticket_id),
                'asset_tag': m.asset.asset_tag,
                'asset_name': m.asset.name,
                'lab': m.asset.lab.lab_name,
                'description': m.description,
                'resolution_status': m.resolution_status,
                'report_date': m.report_date.isoformat() if m.report_date else None,
            } for m in tickets_qs[:50]
        ],
        'notifications': notifications[:15],
    })


@require_http_methods(["GET"])
@api_login_required()
def api_universal_search(request):
    results = universal_asset_search(
        query=request.GET.get('q', ''),
        lab_id=request.GET.get('lab', ''),
        status=request.GET.get('status', ''),
        category=request.GET.get('category', ''),
        asset_type=request.GET.get('asset_type', ''),
    )
    return JsonResponse({'count': len(results), 'results': results})


@require_http_methods(["GET"])
def _resolve_qr_asset(raw_query: str):
    q = (raw_query or '').strip()
    if not q:
        return None
    # Extract asset tag if a full QR payload like "INVENTRA|INV-CHEM-0001|Name|Lab" was scanned
    if '|' in q:
        parts = [p.strip() for p in q.split('|') if p.strip()]
        q = parts[1] if len(parts) >= 2 and parts[0].upper() == 'INVENTRA' else parts[0]

    return Asset.objects.select_related('lab').filter(
        Q(asset_tag__iexact=q) | Q(serial_number__iexact=q)
    ).first()


@require_http_methods(["GET"])
@api_login_required()
def api_qr_lookup(request, asset_tag=''):
    raw = asset_tag or request.GET.get('q', '')
    asset = _resolve_qr_asset(raw)
    if not asset:
        return JsonResponse(
            {'error': f'No equipment found matching tag or serial "{raw}".'},
            status=404,
        )
    active_tx = Transaction.objects.filter(
        asset=asset, request_status='APPROVED'
    ).select_related('borrower').first()
    return JsonResponse({
        'asset_id': str(asset.asset_id),
        'asset_tag': asset.asset_tag,
        'name': asset.name,
        'category': asset.category,
        'asset_type': asset.asset_type,
        'serial_number': asset.serial_number,
        'lab': asset.lab.lab_name,
        'lab_id': str(asset.lab.lab_id),
        'status': asset.status,
        'condition': asset.condition,
        'stock_quantity': asset.stock_quantity,
        'total_quantity': asset.total_quantity,
        'qr_code_url': f'/api/qr/{asset.asset_tag}/image/',
        'extra_metadata': asset.extra_metadata,
        'current_borrower': active_tx.borrower.username if active_tx else None,
    })

@require_http_methods(["GET"])
def api_qr_image(request, asset_tag):
    asset = get_object_or_404(Asset.objects.select_related('lab'), asset_tag=asset_tag)
    qr = qrcode.QRCode(version=1, box_size=8, border=3)
    qr.add_data(f"INVENTRA|{asset.asset_tag}|{asset.name}|{asset.lab.lab_name}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format='PNG')
    return HttpResponse(buf.getvalue(), content_type='image/png')


# ==============================================================================
# LAB CRUD, ASSET CRUD (LAB-SCOPED) & MAINTENANCE RESOLUTION
# ==============================================================================

@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['FACULTY', 'ASSISTANT', 'ADMIN'])
def api_save_lab(request):
    data = _json_body(request)
    try:
        lab = save_lab_record(request.user, data)
        return JsonResponse({
            'message': f'Saved laboratory "{lab.lab_name}" ({lab.lab_code}).',
            'lab_id': str(lab.lab_id),
        }, status=201)
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Lab.DoesNotExist, Exception) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["DELETE", "POST"])
@api_login_required(roles=['FACULTY', 'ADMIN'])
def api_delete_lab(request, lab_id):
    try:
        name = delete_lab_record(lab_id, request.user)
        return JsonResponse({'message': f'Deleted laboratory "{name}".'})
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except Lab.DoesNotExist:
        return JsonResponse({'error': 'Laboratory not found.'}, status=404)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['FACULTY', 'ASSISTANT', 'ADMIN'])
def api_save_asset(request):
    data = _json_body(request)
    try:
        asset = save_asset_record(request.user, data)
        return JsonResponse({
            'message': f'Saved {asset.asset_tag} ({asset.name}).',
            'asset_id': str(asset.asset_id),
            'asset_tag': asset.asset_tag,
        }, status=201)
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, IntegrityError, Lab.DoesNotExist, Asset.DoesNotExist, ValueError) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["DELETE", "POST"])
@api_login_required(roles=['FACULTY', 'ASSISTANT', 'ADMIN'])
def api_delete_asset(request, asset_id):
    try:
        tag = delete_asset_record(asset_id, request.user)
        return JsonResponse({'message': f'Deleted asset {tag}.'})
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except Asset.DoesNotExist:
        return JsonResponse({'error': 'Asset not found.'}, status=404)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_resolve_maintenance(request, ticket_id):
    try:
        ticket = resolve_maintenance_ticket(ticket_id, request.user)
        return JsonResponse({'message': f'Ticket resolved. {ticket.asset.asset_tag} is back in service.'})
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, MaintenanceLog.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


# ==============================================================================
# BORROW / APPROVE / REJECT / RETURN WORKFLOWS
# ==============================================================================

@csrf_exempt
@require_http_methods(["POST"])
@api_login_required()
def api_request_borrow(request):
    data = _json_body(request)
    try:
        tx = create_borrow_request(
            user=request.user,
            asset_id=data.get('asset_id'),
            quantity=int(data.get('quantity', 1)),
            borrow_reason=data.get('borrow_reason', ''),
        )
        return JsonResponse({
            'message': 'Borrow request submitted.',
            'transaction_id': str(tx.transaction_id),
            'request_status': tx.request_status,
        }, status=201)
    except (ValidationError, Asset.DoesNotExist, ValueError) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_approve_transaction(request, transaction_id):
    data = _json_body(request)
    try:
        tx = approve_and_issue_request(
            transaction_id=transaction_id,
            issuer_user=request.user,
            loan_days=int(data.get('loan_days', 7)),
        )
        return JsonResponse({
            'message': f'Approved & issued {tx.asset.asset_tag} to {tx.borrower.username}.',
            'remaining_stock': tx.asset.stock_quantity,
            'asset_status': tx.asset.status,
            'due_date': tx.due_date.isoformat() if tx.due_date else None,
        })
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Transaction.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_reject_transaction(request, transaction_id):
    data = _json_body(request)
    try:
        tx = reject_borrow_request(transaction_id, request.user, data.get('rejection_note', ''))
        return JsonResponse({'message': 'Request rejected.', 'request_status': tx.request_status})
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Transaction.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_return_transaction(request, transaction_id):
    data = _json_body(request)
    try:
        tx = process_equipment_return(
            transaction_id=transaction_id,
            staff_user=request.user,
            return_condition=data.get('return_condition', 'GOOD'),
            notes=data.get('notes', ''),
        )
        return JsonResponse({
            'message': f'Return processed ({tx.return_condition}). Asset is now {tx.asset.status}.',
            'asset_status': tx.asset.status,
            'stock_quantity': tx.asset.stock_quantity,
        })
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Transaction.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


# ==============================================================================
# STRICT TIME-SLOT MACHINE RESERVATIONS
# ==============================================================================

@csrf_exempt
@require_http_methods(["POST"])
@api_login_required()
def api_book_reservation(request):
    data = _json_body(request)
    start_dt = parse_datetime(data.get('start_time', ''))
    end_dt = parse_datetime(data.get('end_time', ''))
    if not start_dt or not end_dt:
        return JsonResponse({'error': 'Valid start_time and end_time are required.'}, status=400)
    if timezone.is_naive(start_dt):
        start_dt = timezone.make_aware(start_dt)
    if timezone.is_naive(end_dt):
        end_dt = timezone.make_aware(end_dt)

    try:
        res = create_time_slot_reservation(
            user=request.user,
            asset_id=data.get('asset_id'),
            start_time=start_dt,
            end_time=end_dt,
            purpose=data.get('purpose', ''),
        )
        return JsonResponse({
            'message': 'Time slot reserved! Remember to check in within 30 mins of start time.',
            'reservation_id': str(res.reservation_id),
            'start_time': res.start_time.isoformat(),
            'end_time': res.end_time.isoformat(),
        }, status=201)
    except (ValidationError, Asset.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_checkin_reservation(request, reservation_id):
    try:
        res = checkin_reservation(reservation_id, request.user)
        return JsonResponse({'message': 'Reservation checked in.', 'status': res.status})
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Reservation.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required()
def api_cancel_reservation(request, reservation_id):
    try:
        res = cancel_reservation(reservation_id, request.user)
        return JsonResponse({'message': 'Reservation cancelled.', 'status': res.status})
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Reservation.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


# ==============================================================================
# UNIVERSAL ANY-FORMAT IMPORT (LAB-SCOPED) & EXACT PR TEMPLATE EXPORTS
# ==============================================================================

@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['FACULTY', 'ASSISTANT', 'ADMIN'])
def api_ingest_any_file(request):
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file uploaded. Attach file under form key "file".'}, status=400)
    profile = get_user_profile(request.user)
    forced_lab = None
    if profile.role == 'ASSISTANT':
        if profile.assigned_lab is None:
            return JsonResponse({'error': 'Lab Scope Violation: Your assistant account has no assigned lab.'}, status=403)
        forced_lab = profile.assigned_lab

    try:
        result = ingest_file_bytes(
            file_bytes=uploaded.read(),
            filename=uploaded.name,
            default_lab_name=request.POST.get('lab_name', ''),
            forced_lab=forced_lab,
        )
        return JsonResponse({'message': 'File ingested successfully.', **result}, status=201)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_http_methods(["GET"])
@api_login_required(roles=['FACULTY', 'ASSISTANT', 'ADMIN'])
def api_export_inventory(request):
    fmt = request.GET.get('format', 'csv')
    profile = get_user_profile(request.user)
    lab_filter = profile.assigned_lab if profile.role == 'ASSISTANT' else None
    content, content_type, filename = export_inventory_bytes(fmt, lab_filter=lab_filter)
    resp = HttpResponse(content, content_type=content_type)
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp



@csrf_exempt
@require_http_methods(["POST"])
@api_login_required()
def api_cancel_transaction(request, transaction_id):
    try:
        tx = cancel_borrow_request(transaction_id, request.user)
        return JsonResponse({
            'message': 'Request cancelled successfully!',
            'request_status': tx.request_status,
            'asset_status': tx.asset.status,
            'stock_quantity': tx.asset.stock_quantity,
        })
    except PermissionDenied as e:
        return JsonResponse({'error': str(e)}, status=403)
    except (ValidationError, Transaction.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required()
def api_report_fault(request):
    data = _json_body(request)
    try:
        ticket = report_asset_fault(
            asset_id=data.get('asset_id'),
            user=request.user,
            description=data.get('description', ''),
            mark_maintenance=bool(data.get('mark_maintenance', False)),
        )
        return JsonResponse({
            'message': f'Fault ticket created for {ticket.asset.asset_tag}.',
            'ticket_id': str(ticket.ticket_id),
            'resolution_status': ticket.resolution_status,
        }, status=201)
    except (ValidationError, Asset.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@require_http_methods(["GET"])
@api_login_required()
def api_user_profile(request, username=''):
    target_username = (username or '').strip() or request.user.username
    requester_role = get_user_role(request.user)

    if target_username != request.user.username and requester_role not in ['FACULTY', 'ASSISTANT', 'ADMIN']:
        return JsonResponse({'error': 'Access denied. You can only view your own profile.'}, status=403)

    target = get_object_or_404(User, username__iexact=target_username)
    profile_data = _serialize_user(target)
    tx_qs = Transaction.objects.filter(borrower=target).select_related('asset', 'asset__lab').order_by('-requested_at')
    res_qs = Reservation.objects.filter(user=target).select_related('asset', 'asset__lab').order_by('-created_at')

    return JsonResponse({
        'user': profile_data,
        'joined_at': target.date_joined.isoformat() if target.date_joined else None,
        'stats': {
            'total_requests': tx_qs.count(),
            'active_loans': tx_qs.filter(request_status='APPROVED').count(),
            'returned_count': tx_qs.filter(request_status='RETURNED').count(),
            'active_reservations': res_qs.filter(status='ACTIVE').count(),
        },
        'recent_requests': [_serialize_tx(t) for t in tx_qs[:10]],
        'recent_reservations': [
            {
                'reservation_id': str(r.reservation_id),
                'asset_tag': r.asset.asset_tag,
                'asset_name': r.asset.name,
                'lab': r.asset.lab.lab_name,
                'start_time': r.start_time.isoformat(),
                'end_time': r.end_time.isoformat(),
                'status': r.status,
            } for r in res_qs[:10]
        ],
    })
