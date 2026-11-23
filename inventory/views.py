"""
INVENTRA JSON REST API & Role-Based Views
Serves Sriraksha's frontend (index.html) and provides full RBAC endpoints.
"""
import json
from functools import wraps
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import F
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Lab, Asset, Transaction, Reservation, MaintenanceLog
from .services import (
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
    expire_stale_reservations,
)
from .ingestion import ingest_file_bytes, export_inventory_bytes


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8')) if request.body else {}
    except json.JSONDecodeError:
        return {}


def api_login_required(roles=None):
    """Strict RBAC decorator returning clean JSON 401/403 codes instead of HTML redirects."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({'error': 'Authentication required.'}, status=401)
            role = get_user_role(request.user)
            if roles and role not in roles:
                return JsonResponse(
                    {'error': f'Access denied. Required role: {roles}, your role: {role}.'},
                    status=403
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ==============================================================================
# FRONTEND ENTRY POINT
# ==============================================================================

def frontend_index(request):
    """Serves Sriraksha's frontend SPA (templates/index.html) directly from Django."""
    return render(request, 'index.html')


# ==============================================================================
# AUTHENTICATION & COLLEGE EMAIL OTP REGISTRATION
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
        )
        payload = {'message': f'OTP sent to {otp.email}. Valid for 10 minutes.'}
        if settings.DEBUG:
            payload['dev_otp'] = otp.code  # Visible in dev mode so local testing never stalls
        return JsonResponse(payload, status=200)
    except ValidationError as e:
        return JsonResponse({'error': e.message}, status=400)


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
            'user': {
                'username': user.username,
                'email': user.email,
                'role': get_user_role(user),
                'department': user.profile.department,
            }
        }, status=201)
    except ValidationError as e:
        return JsonResponse({'error': e.message}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_login(request):
    data = _json_body(request)
    identifier = (data.get('email') or data.get('username') or '').strip()
    password = data.get('password', '')

    # Allow logging in with either college email or username
    username = identifier
    if '@' in identifier:
        matched = User.objects.filter(email__iexact=identifier).first()
        if matched:
            username = matched.username

    user = authenticate(request, username=username, password=password)
    if not user:
        return JsonResponse({'error': 'Invalid credentials.'}, status=401)

    login(request, user)
    role = get_user_role(user)
    return JsonResponse({
        'message': 'Logged in successfully.',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': f"{user.first_name} {user.last_name}".strip() or user.username,
            'role': role,
            'department': user.profile.department,
            'penalty_flags': user.profile.penalty_flags,
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_logout(request):
    logout(request)
    return JsonResponse({'message': 'Logged out.'})


@require_http_methods(["GET"])
@api_login_required()
def api_me(request):
    role = get_user_role(request.user)
    return JsonResponse({
        'id': request.user.id,
        'username': request.user.username,
        'email': request.user.email,
        'full_name': f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username,
        'role': role,
        'department': request.user.profile.department,
        'penalty_flags': request.user.profile.penalty_flags,
        'permissions': {
            'can_approve_requests': role in ['ASSISTANT', 'ADMIN'],
            'can_ingest_data': role in ['ASSISTANT', 'ADMIN'],
            'can_export_reports': role in ['FACULTY', 'ASSISTANT', 'ADMIN'],
            'can_manage_labs': role == 'ADMIN',
        }
    })


# ==============================================================================
# ROLE-SPECIFIC DASHBOARD & UNIVERSAL CROSS-LAB SEARCH
# ==============================================================================

@require_http_methods(["GET"])
@api_login_required()
def api_dashboard(request):
    expire_stale_reservations()
    role = get_user_role(request.user)

    labs = list(Lab.objects.values('lab_id', 'lab_name', 'lab_code'))
    low_stock_qs = Asset.objects.filter(stock_quantity__lte=F('low_stock_threshold')).select_related('lab')
    overdue_qs = Transaction.objects.filter(
        request_status='APPROVED', due_date__lt=timezone.now()
    ).select_related('asset', 'borrower')

    if role in ['ASSISTANT', 'ADMIN']:
        pending_qs = Transaction.objects.filter(request_status='PENDING').select_related('asset', 'borrower', 'asset__lab')
        active_qs = Transaction.objects.filter(request_status='APPROVED').select_related('asset', 'borrower', 'asset__lab')
        reservations_qs = Reservation.objects.filter(status='ACTIVE').select_related('asset', 'user', 'asset__lab')
    else:
        pending_qs = Transaction.objects.filter(borrower=request.user, request_status='PENDING').select_related('asset', 'asset__lab')
        active_qs = Transaction.objects.filter(borrower=request.user, request_status='APPROVED').select_related('asset', 'asset__lab')
        reservations_qs = Reservation.objects.filter(user=request.user, status='ACTIVE').select_related('asset', 'asset__lab')

    def serialize_tx(t):
        return {
            'transaction_id': str(t.transaction_id),
            'asset_tag': t.asset.asset_tag,
            'asset_name': t.asset.name,
            'lab': t.asset.lab.lab_name,
            'borrower': t.borrower.username,
            'borrower_email': t.borrower.email,
            'quantity': t.quantity,
            'borrow_reason': t.borrow_reason,
            'request_status': t.request_status,
            'requested_at': t.requested_at.isoformat(),
            'due_date': t.due_date.isoformat() if t.due_date else None,
        }

    return JsonResponse({
        'role': role,
        'stats': {
            'total_assets': Asset.objects.count(),
            'available_assets': Asset.objects.filter(status='AVAILABLE').count(),
            'maintenance_assets': Asset.objects.filter(status='MAINTENANCE').count(),
            'low_stock_count': low_stock_qs.count(),
            'overdue_count': overdue_qs.count(),
            'pending_requests_count': pending_qs.count(),
            'open_maintenance_tickets': MaintenanceLog.objects.filter(resolution_status='OPEN').count(),
        },
        'labs': labs,
        'pending_requests': [serialize_tx(t) for t in pending_qs[:50]],
        'active_loans': [serialize_tx(t) for t in active_qs[:50]],
        'active_reservations': [
            {
                'reservation_id': str(r.reservation_id),
                'asset_tag': r.asset.asset_tag,
                'asset_name': r.asset.name,
                'lab': r.asset.lab.lab_name,
                'user': r.user.username,
                'start_time': r.start_time.isoformat(),
                'end_time': r.end_time.isoformat(),
                'status': r.status,
            } for r in reservations_qs[:50]
        ],
    })


@require_http_methods(["GET"])
@api_login_required()
def api_universal_search(request):
    """Universal cross-lab search: returns every matching asset across all labs with full stats."""
    results = universal_asset_search(
        query=request.GET.get('q', ''),
        lab_id=request.GET.get('lab', ''),
        status=request.GET.get('status', ''),
        category=request.GET.get('category', ''),
        asset_type=request.GET.get('asset_type', ''),
    )
    return JsonResponse({'count': len(results), 'results': results})


@require_http_methods(["GET"])
@api_login_required()
def api_qr_lookup(request, asset_tag):
    asset = get_object_or_404(Asset.objects.select_related('lab'), asset_tag=asset_tag)
    active_tx = Transaction.objects.filter(asset=asset, request_status='APPROVED').select_related('borrower').first()
    return JsonResponse({
        'asset_id': str(asset.asset_id),
        'asset_tag': asset.asset_tag,
        'name': asset.name,
        'lab': asset.lab.lab_name,
        'status': asset.status,
        'condition': asset.condition,
        'stock_quantity': asset.stock_quantity,
        'extra_metadata': asset.extra_metadata,
        'current_borrower': active_tx.borrower.username if active_tx else None,
    })


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
            'due_date': tx.due_date.isoformat(),
        })
    except (ValidationError, PermissionDenied, Transaction.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_reject_transaction(request, transaction_id):
    data = _json_body(request)
    try:
        tx = reject_borrow_request(transaction_id, request.user, data.get('rejection_note', ''))
        return JsonResponse({'message': 'Request rejected.', 'request_status': tx.request_status})
    except (ValidationError, PermissionDenied, Transaction.DoesNotExist) as e:
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
            'message': f'Return processed ({tx.return_condition}).',
            'asset_status': tx.asset.status,
            'stock_quantity': tx.asset.stock_quantity,
        })
    except (ValidationError, PermissionDenied, Transaction.DoesNotExist) as e:
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
        return JsonResponse({'error': 'Valid ISO-8601 start_time and end_time are required.'}, status=400)
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
            'message': 'Time slot reserved. Must check in within 30 mins of start time.',
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
    except (ValidationError, PermissionDenied, Reservation.DoesNotExist) as e:
        return JsonResponse({'error': getattr(e, 'message', str(e))}, status=400)


# ==============================================================================
# UNIVERSAL ANY-FORMAT IMPORT & MULTI-FORMAT EXPORT
# ==============================================================================

@csrf_exempt
@require_http_methods(["POST"])
@api_login_required(roles=['ASSISTANT', 'ADMIN'])
def api_ingest_any_file(request):
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file uploaded. Attach file under form key "file".'}, status=400)
    try:
        result = ingest_file_bytes(
            file_bytes=uploaded.read(),
            filename=uploaded.name,
            default_lab_name=request.POST.get('lab_name', ''),
        )
        return JsonResponse({'message': 'File ingested successfully.', **result}, status=201)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@require_http_methods(["GET"])
@api_login_required(roles=['FACULTY', 'ASSISTANT', 'ADMIN'])
def api_export_inventory(request):
    fmt = request.GET.get('format', 'csv')
    content, content_type, filename = export_inventory_bytes(fmt)
    resp = HttpResponse(content, content_type=content_type)
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
