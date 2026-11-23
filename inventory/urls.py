from django.urls import path
from . import views

urlpatterns = [
    # Frontend SPA entry
    path('', views.frontend_index, name='frontend_index'),

    # Auth & College Email OTP
    path('api/auth/request-otp/', views.api_request_otp, name='api_request_otp'),
    path('api/auth/verify-otp/', views.api_verify_otp_register, name='api_verify_otp'),
    path('api/auth/login/', views.api_login, name='api_login'),
    path('api/auth/logout/', views.api_logout, name='api_logout'),
    path('api/auth/me/', views.api_me, name='api_me'),

    # Dashboard, Universal Search & QR Lookup
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),
    path('api/search/', views.api_universal_search, name='api_universal_search'),
    path('api/qr/<str:asset_tag>/', views.api_qr_lookup, name='api_qr_lookup'),

    # Autonomous Borrow / Issue / Return
    path('api/transactions/request/', views.api_request_borrow, name='api_request_borrow'),
    path('api/transactions/<uuid:transaction_id>/approve/', views.api_approve_transaction, name='api_approve_transaction'),
    path('api/transactions/<uuid:transaction_id>/reject/', views.api_reject_transaction, name='api_reject_transaction'),
    path('api/transactions/<uuid:transaction_id>/return/', views.api_return_transaction, name='api_return_transaction'),

    # Strict Time-Slot Reservations
    path('api/reservations/book/', views.api_book_reservation, name='api_book_reservation'),
    path('api/reservations/<uuid:reservation_id>/checkin/', views.api_checkin_reservation, name='api_checkin_reservation'),

    # Universal Import & Export
    path('api/inventory/ingest/', views.api_ingest_any_file, name='api_ingest_any_file'),
    path('api/inventory/export/', views.api_export_inventory, name='api_export_inventory'),
]
