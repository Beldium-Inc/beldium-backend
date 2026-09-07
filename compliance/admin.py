from django.contrib import admin

from compliance.models import ApplicationMessage, ComplianceApplication, ComplianceDocument, Personnel

admin.site.register(ComplianceApplication)
admin.site.register(Personnel)
admin.site.register(ComplianceDocument)
admin.site.register(ApplicationMessage)
