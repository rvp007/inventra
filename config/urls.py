from django.contrib import admin
from django.urls import path
from django.http import HttpResponse

def scale_home(request):
    return HttpResponse("""
        <div style="font-family: sans-serif; text-align: center; margin-top: 100px;">
            <h1>🚀 S.C.A.L.E. Backend is Live!</h1>
            <p>Connected to Supabase PostgreSQL.</p>
            <a href="/admin/" style="padding: 10px 20px; background: #2563eb; color: white; text-decoration: none; border-radius: 6px;">
                Open Admin Dashboard
            </a>
        </div>
    """)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', scale_home, name='home'),
]
