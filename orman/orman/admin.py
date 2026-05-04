from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    InstrumentFamily, Instrument, Person, Venue, RentalContract,
    MusicItem, MusicItemPart, Rehearsal, Performance,
    PerformanceItem, RehearsalItem, PerformanceSeats,
)


class PersonAdmin(UserAdmin):
    model = Person
    list_display = ("email", "firstNames", "lastName", "member", "is_admin", "is_active")
    list_filter = ("member", "is_admin", "is_active")
    search_fields = ("email", "firstNames", "lastName")
    ordering = ("lastName", "firstNames")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("firstNames", "lastName", "phone", "instruments")}),
        ("Membership", {"fields": ("member", "is_admin", "is_active")}),
        ("Permissions", {"fields": ("is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "firstNames", "lastName", "password1", "password2"),
        }),
    )


admin.site.register(Person, PersonAdmin)
admin.site.register(InstrumentFamily)
admin.site.register(Instrument)
admin.site.register(Venue)
admin.site.register(RentalContract)
admin.site.register(MusicItem)
admin.site.register(MusicItemPart)
admin.site.register(Rehearsal)
admin.site.register(Performance)
admin.site.register(PerformanceItem)
admin.site.register(RehearsalItem)
admin.site.register(PerformanceSeats)
