from django.conf.urls.defaults import *

from notification.views import (notices, mark_all_seen, single,
                                notice_settings, unsubscribe, view_sender)

urlpatterns = patterns("",
    url(r"^$", notices, name="notification_notices"),
    url(r"^all/?$", notices, {'alln': True}, name="notification_notices_all"),
    url(r"^settings/?$", notice_settings, name="notification_notice_settings"),
    url(r"^(\d+)/$", single, name="notification_notice"),
    url(r"^view/(\d+)/.?$", view_sender, name="notification_view_sender"),
    url(r"^mark_all_seen/$", mark_all_seen, name="notification_mark_all_seen"),
    url(r'^unsubscribe/(\w+)/(.+)/$', unsubscribe, name="notificaton_unsubscribe"),
)
