"""
URL configuration for orman project.

For each model that uses the new modal-form CRUD pattern there are four routes,
sharing a single view function:

    /admin_<thing>/                  list (GET) / create (POST)
    /admin_<thing>/<id>/             update (POST)
    /admin_<thing>/<id>/form/        form fragment for the modal (GET)
    /admin_<thing>/<id>/delete/      delete (POST)
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from . import views

_social_urls = [
    path("accounts/social/", include("allauth.urls")),
] if settings.SOCIAL_LOGIN_ENABLED else []


urlpatterns = [
    path("admin/", admin.site.urls),
    path("__reload__/", include("django_browser_reload.urls")),

    # Built-in Django auth: login/, logout/, password_change/, etc.
    path("accounts/", include("django.contrib.auth.urls")),

    # Passkeys (WebAuthn).
    path("auth/passkey/register/begin/", views.passkey_register_begin, name="passkey_register_begin"),
    path("auth/passkey/register/complete/", views.passkey_register_complete, name="passkey_register_complete"),
    path("auth/passkey/auth/begin/", views.passkey_auth_begin, name="passkey_auth_begin"),
    path("auth/passkey/auth/complete/", views.passkey_auth_complete, name="passkey_auth_complete"),
    path("auth/passkey/<int:passkey_id>/delete/", views.passkey_delete, name="passkey_delete"),

    # iCal feed (no session needed — UUID token is the credential).
    path("calendar/<uuid:token>/events.ics", views.calendar_ics, name="calendar_ics"),

    # Member-facing pages (top-left navbar dropdown).
    path("profile/", views.profile, name="profile"),
    path("announcement/<int:id>/dismiss/", views.dismiss_announcement, name="dismiss_announcement"),
    path("events/", views.events, name="events"),
    path("music/", views.music, name="music"),

    # Member-facing event detail pages.
    path("rehearsal/<int:rehearsal_id>/", views.rehearsal_detail, name="rehearsal_detail"),
    path("performance/<int:performance_id>/", views.performance_detail, name="performance_detail"),

    # RSVP submission. `kind` is "rehearsal" or "performance".
    path("rsvp/<str:kind>/<int:id>/", views.rsvp, name="rsvp"),

    # Member-facing polls + response submission.
    path("polls/", views.polls, name="polls"),
    path("polls/question/<int:question_id>/answer/", views.poll_answer, name="poll_answer"),

    # InstrumentFamily — original inline-edit pattern.
    path("admin_instrument_family/", views.admin_instrument_family, name="admin_instrument_family"),
    path("admin_instrument_family/<int:id>", views.admin_instrument_family, name="admin_instrument_family"),
    path("admin_instrument_family/<int:family_id>/members/", views.admin_instrument_family_members, name="admin_instrument_family_members"),

    # Instrument
    path("admin_instrument/", views.admin_instrument, name="admin_instrument"),
    path("admin_instrument/<int:id>/", views.admin_instrument, name="admin_instrument"),
    path("admin_instrument/<int:id>/form/", views.admin_instrument, name="admin_instrument_form"),
    path("admin_instrument/<int:id>/delete/", views.admin_instrument, name="admin_instrument_delete"),
    path("admin_instrument/<int:instrument_id>/members/", views.admin_instrument_members, name="admin_instrument_members"),

    # Person
    path("admin_person/", views.admin_person, name="admin_person"),
    path("admin_person/<int:id>/", views.admin_person, name="admin_person"),
    path("admin_person/<int:id>/form/", views.admin_person, name="admin_person_form"),
    path("admin_person/<int:id>/delete/", views.admin_person, name="admin_person_delete"),
    path("admin_person/<int:id>/set_password/", views.admin_set_password, name="admin_person_set_password"),

    # Venue
    path("admin_venue/", views.admin_venue, name="admin_venue"),
    path("admin_venue/<int:id>/", views.admin_venue, name="admin_venue"),
    path("admin_venue/<int:id>/form/", views.admin_venue, name="admin_venue_form"),
    path("admin_venue/<int:id>/delete/", views.admin_venue, name="admin_venue_delete"),

    # RentalContract
    path("admin_rental_contract/", views.admin_rental_contract, name="admin_rental_contract"),
    path("admin_rental_contract/<int:id>/", views.admin_rental_contract, name="admin_rental_contract"),
    path("admin_rental_contract/<int:id>/form/", views.admin_rental_contract, name="admin_rental_contract_form"),
    path("admin_rental_contract/<int:id>/delete/", views.admin_rental_contract, name="admin_rental_contract_delete"),

    # MusicItem
    path("admin_music_item/", views.admin_music_item, name="admin_music_item"),
    path("admin_music_item/<int:id>/", views.admin_music_item, name="admin_music_item"),
    path("admin_music_item/<int:id>/form/", views.admin_music_item, name="admin_music_item_form"),
    path("admin_music_item/<int:id>/delete/", views.admin_music_item, name="admin_music_item_delete"),
    path("admin_music_item/<int:music_item_id>/parts/", views.admin_music_item_parts, name="admin_music_item_parts"),
    path("admin_music_item/<int:music_item_id>/parts/<int:part_id>", views.admin_music_item_parts, name="admin_music_item_parts_item"),

    # RehearsalSeries
    path("admin_rehearsal_series/", views.admin_rehearsal_series, name="admin_rehearsal_series"),
    path("admin_rehearsal_series/<int:id>/", views.admin_rehearsal_series, name="admin_rehearsal_series"),
    path("admin_rehearsal_series/<int:id>/form/", views.admin_rehearsal_series, name="admin_rehearsal_series_form"),
    path("admin_rehearsal_series/<int:id>/delete/", views.admin_rehearsal_series, name="admin_rehearsal_series_delete"),
    path("admin_rehearsal_series/<int:series_id>/generate/", views.admin_rehearsal_series_generate, name="admin_rehearsal_series_generate"),

    # Rehearsal
    path("admin_rehearsal/", views.admin_rehearsal, name="admin_rehearsal"),
    path("admin_rehearsal/<int:id>/", views.admin_rehearsal, name="admin_rehearsal"),
    path("admin_rehearsal/<int:id>/form/", views.admin_rehearsal, name="admin_rehearsal_form"),
    path("admin_rehearsal/<int:id>/delete/", views.admin_rehearsal, name="admin_rehearsal_delete"),
    path("admin_rehearsal/<int:rehearsal_id>/attendance/", views.admin_rehearsal_attendance, name="admin_rehearsal_attendance"),
    path("admin_rehearsal/<int:rehearsal_id>/repertoire/", views.admin_rehearsal_repertoire, name="admin_rehearsal_repertoire"),
    path("admin_rehearsal/<int:rehearsal_id>/repertoire/<int:item_id>", views.admin_rehearsal_repertoire, name="admin_rehearsal_repertoire_item"),

    # Poll
    path("admin_poll/", views.admin_poll, name="admin_poll"),
    path("admin_poll/<int:id>/", views.admin_poll, name="admin_poll"),
    path("admin_poll/<int:id>/form/", views.admin_poll, name="admin_poll_form"),
    path("admin_poll/<int:id>/delete/", views.admin_poll, name="admin_poll_delete"),
    path("admin_poll/<int:id>/responses/", views.admin_poll_responses, name="admin_poll_responses"),
    path("admin_poll/<int:poll_id>/responses/table/", views.admin_poll_responses_table, name="admin_poll_responses_table"),
    path("admin_poll/<int:poll_id>/choice/<int:choice_id>/respondents/", views.admin_poll_choice_respondents, name="admin_poll_choice_respondents"),
    path("admin_poll/<int:poll_id>/questions/", views.admin_poll_questions, name="admin_poll_questions"),
    path("admin_poll/<int:poll_id>/questions/<int:id>/form/", views.admin_poll_questions, name="admin_poll_question_form"),
    path("admin_poll/<int:poll_id>/questions/<int:id>/delete/", views.admin_poll_questions, name="admin_poll_question_delete"),
    path("admin_poll/<int:poll_id>/questions/<int:question_id>/choices/", views.admin_poll_choices, name="admin_poll_choices"),
    path("admin_poll/<int:poll_id>/questions/<int:question_id>/choices/<int:id>", views.admin_poll_choices, name="admin_poll_choices"),

    # Home page content (singleton) + carousel images
    path("admin_site_content/", views.admin_site_content, name="admin_site_content"),
    path("admin_carousel_image/", views.admin_carousel_image, name="admin_carousel_image"),
    path("admin_carousel_image/<int:id>/", views.admin_carousel_image, name="admin_carousel_image"),
    path("admin_carousel_image/<int:id>/form/", views.admin_carousel_image, name="admin_carousel_image_form"),
    path("admin_carousel_image/<int:id>/delete/", views.admin_carousel_image, name="admin_carousel_image_delete"),

    # Announcement
    path("admin_announcement/", views.admin_announcement, name="admin_announcement"),
    path("admin_announcement/<int:id>/", views.admin_announcement, name="admin_announcement"),
    path("admin_announcement/<int:id>/form/", views.admin_announcement, name="admin_announcement_form"),
    path("admin_announcement/<int:id>/delete/", views.admin_announcement, name="admin_announcement_delete"),

    # Performance
    path("admin_performance/", views.admin_performance, name="admin_performance"),
    path("admin_performance/<int:id>/", views.admin_performance, name="admin_performance"),
    path("admin_performance/<int:id>/form/", views.admin_performance, name="admin_performance_form"),
    path("admin_performance/<int:id>/delete/", views.admin_performance, name="admin_performance_delete"),
    path("admin_performance/<int:performance_id>/attendance/", views.admin_performance_attendance, name="admin_performance_attendance"),
    path("admin_performance/<int:performance_id>/repertoire/", views.admin_performance_repertoire, name="admin_performance_repertoire"),
    path("admin_performance/<int:performance_id>/repertoire/<int:item_id>", views.admin_performance_repertoire, name="admin_performance_repertoire_item"),

    # Mailing lists
    path("admin_mailing_list/", views.admin_mailing_list, name="admin_mailing_list"),
    path("admin_mailing_list/<int:id>/", views.admin_mailing_list, name="admin_mailing_list"),
    path("admin_mailing_list/<int:id>/form/", views.admin_mailing_list, name="admin_mailing_list_form"),
    path("admin_mailing_list/<int:id>/delete/", views.admin_mailing_list, name="admin_mailing_list_delete"),
    path("admin_mailing_list/<int:list_id>/detail/", views.admin_mailing_list_detail, name="admin_mailing_list_detail"),

    # Social Auth App management
    path("admin_social_apps/", views.admin_social_apps, name="admin_social_apps"),
    path("admin_social_apps/<int:id>/", views.admin_social_apps, name="admin_social_apps"),
    path("admin_social_apps/<int:id>/form/", views.admin_social_apps, name="admin_social_apps_form"),
    path("admin_social_apps/<int:id>/delete/", views.admin_social_apps, name="admin_social_apps_delete"),

    path("", views.index, name="index"),
] + _social_urls + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
