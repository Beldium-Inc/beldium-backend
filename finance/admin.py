from django.contrib import admin

from finance.models import Invoice, Payment

admin.site.register(Invoice)
admin.site.register(Payment)
