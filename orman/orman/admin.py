from django.contrib import admin

from .models import InstrumentFamily, Instrument, Person, Venue, RentalContract, MusicItem, MusicItemPart, Rehearsal, Performance, PerformanceItem, RehearsalItem
# Register your models here.

admin.site.register(InstrumentFamily)
admin.site.register(Instrument)
admin.site.register(Person)
admin.site.register(Venue)
admin.site.register(RentalContract)
admin.site.register(MusicItem)
admin.site.register(MusicItemPart)
admin.site.register(Rehearsal)
admin.site.register(Performance)
admin.site.register(PerformanceItem)
admin.site.register(RehearsalItem)
