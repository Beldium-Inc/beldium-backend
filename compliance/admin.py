from django.contrib import admin

from compliance.models import ApprovalCondition, ConditionEvidence, MessageReadReceipt, ApplicationMessage, ComplianceApplication, ComplianceDocument, Personnel

admin.site.register(ComplianceApplication)
admin.site.register(Personnel)
admin.site.register(ComplianceDocument)
admin.site.register(ApplicationMessage)

admin.site.register(ApprovalCondition)
admin.site.register(ConditionEvidence)
admin.site.register(MessageReadReceipt)
