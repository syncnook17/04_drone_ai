"""
URL configuration for drone_system project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from app_core import views

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # DJI Handshake APIs
    path('dji/login/', views.dji_h5_login, name='dji_h5_login'),
    path('dji/mqtt-test/', views.dji_mqtt_test, name='dji_mqtt_test'),
    path('dji/api/v1/login/', views.dji_pilot_login, name='dji_pilot_login'),
    path('dji/api/v1/mqtt-config/', views.get_mqtt_config, name='get_mqtt_config'),
    path('dji/api/v1/livestream-config/', views.get_livestream_config, name='get_livestream_config'),

    # DJI Cloud API มาตรฐาน (Pilot API module เรียก path นี้)
    path('manage/api/v1/login/', views.manage_login, name='manage_login'),
    path('manage/api/v1/workspaces/current/', views.manage_workspace_current, name='manage_workspace_current'),
    path('manage/api/v1/devices/<str:workspace_id>/devices/', views.manage_devices_list, name='manage_devices_list'),
    path('manage/api/v1/devices/<str:workspace_id>/devices/bound/', views.manage_devices_bound, name='manage_devices_bound'),
    path('manage/api/v1/devices/<str:workspace_id>/devices/<str:device_sn>/', views.manage_device_detail, name='manage_device_detail'),
    
    # Web Dashboards
    path('dashboard/mission/', views.mission_control, name='mission_control'),
    path('dashboard/heatmap/', views.heatmap_view, name='heatmap_view'),
    path('dashboard/logs/', views.detection_logs, name='detection_logs'),
    
    # Internal APIs for Frontend
    path('api/drone-location/', views.api_latest_drone_location, name='api_latest_drone_location'),
    path('api/heatmap-data/', views.api_heatmap_data, name='api_heatmap_data'),

    # Background Services
    path('api/start-service/', views.start_backend_service, name='start_backend_service'),
    path('api/system-status/', views.api_system_status, name='api_system_status'),
]