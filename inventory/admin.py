from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import UserProfile, Lab, Asset, Transaction, Reservation, MaintenanceLog


admin.site.site_header = "INVENTRA — Smart Campus Lab & Equipment Administration"
admin.site.site_title = "INVENTRA Admin Portal"
admin.site.index_title = "Welcome to INVENTRA Inventory Management"


@admin.register(UserProfile)
class UserProfileAdmin(ImportExportModelAdmin):
    list_display = ('user', 'role', 'department', 'penalty_flags')
    list_filter = ('role', 'department')
    search_fields = ('user__username', 'user__email', 'department')

@admin.register(Lab)
class LabAdmin(ImportExportModelAdmin):
    list_display = ('lab_name', 'in_charge', 'lab_id')
    search_fields = ('lab_name',)

@admin.register(Asset)
class AssetAdmin(ImportExportModelAdmin):
    list_display = ('name', 'serial_number', 'category', 'lab', 'status', 'condition', 'stock_quantity')
    list_filter = ('status', 'condition', 'lab', 'category')
    search_fields = ('name', 'serial_number', 'category')

@admin.register(Transaction)
class TransactionAdmin(ImportExportModelAdmin):
    list_display = ('asset', 'borrower', 'issuer', 'request_status', 'issue_date', 'due_date', 'return_date')
    list_filter = ('request_status', 'return_condition')
    search_fields = ('asset__name', 'borrower__username')

@admin.register(Reservation)
class ReservationAdmin(ImportExportModelAdmin):
    list_display = ('asset', 'user', 'start_time', 'end_time', 'status')
    list_filter = ('status',)

@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(ImportExportModelAdmin):
    list_display = ('asset', 'reported_by', 'report_date', 'resolution_status')
    list_filter = ('resolution_status',)
