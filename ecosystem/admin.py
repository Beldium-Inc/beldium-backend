from django.contrib import admin

from ecosystem.models import Commitment, LogisticsMove, MaterialBatch, Rfq, Transaction, TransactionStageEvent

admin.site.register(Rfq)
admin.site.register(Transaction)
admin.site.register(TransactionStageEvent)
admin.site.register(MaterialBatch)
admin.site.register(LogisticsMove)
admin.site.register(Commitment)
