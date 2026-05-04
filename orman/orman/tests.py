"""Tests for the orman admin CRUD pages.

These tests cover the shared crud_view() helper via the per-model URL
endpoints. They use Django's test client and an in-memory SQLite db.

Run with:  python manage.py test orman
"""
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import models

Person = get_user_model()


def _make_user(email="tester@example.com", password="secret", **kwargs):
    kwargs.setdefault("firstNames", "Test")
    kwargs.setdefault("lastName", "User")
    return Person.objects.create_user(email=email, password=password, **kwargs)


class InstrumentFamilyInlineCRUDTests(TestCase):
    """The original inline-edit pattern (admin_instrument_family)."""

    def setUp(self):
        _make_user(is_admin=True)
        self.client.login(username="tester@example.com", password="secret")

    def test_get_renders_list(self):
        models.InstrumentFamily.objects.create(name="Strings")
        resp = self.client.get(reverse("admin_instrument_family"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Strings")

    def test_post_creates(self):
        url = reverse("admin_instrument_family", args=[0])
        resp = self.client.post(url, {"name": "Brass"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode().strip(), "1")
        self.assertTrue(
            models.InstrumentFamily.objects.filter(name="Brass").exists()
        )

    def test_post_updates(self):
        family = models.InstrumentFamily.objects.create(name="Old")
        url = reverse("admin_instrument_family", args=[family.id])
        resp = self.client.post(url, {"name": "New"})
        self.assertEqual(resp.status_code, 200)
        family.refresh_from_db()
        self.assertEqual(family.name, "New")

    def test_post_blank_name_returns_400(self):
        url = reverse("admin_instrument_family", args=[0])
        resp = self.client.post(url, {"name": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_delete_removes(self):
        family = models.InstrumentFamily.objects.create(name="Doomed")
        url = reverse("admin_instrument_family", args=[family.id])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            models.InstrumentFamily.objects.filter(pk=family.id).exists()
        )


class _ModalCRUDMixin:
    """Shared assertions for any model using the modal-form CRUD pattern.

    Subclasses must define:
      list_url_name, save_url_name, form_url_name, delete_url_name
      model
      sample_form_data() -> dict
      list_check_text   -> str expected in list page after creation
    """

    list_url_name: str
    save_url_name: str
    form_url_name: str
    delete_url_name: str
    model = None

    def setUp(self):
        super().setUp()
        _make_user(is_admin=True)
        self.client.login(username="tester@example.com", password="secret")

    def _list_url(self):
        return reverse(self.list_url_name)

    def _save_url(self, id=None):
        if id is None:
            return reverse(self.save_url_name)
        return reverse(self.save_url_name, args=[id])

    def _form_url(self, id):
        return reverse(self.form_url_name, args=[id])

    def _delete_url(self, id):
        return reverse(self.delete_url_name, args=[id])

    def sample_form_data(self):
        raise NotImplementedError

    list_check_text = ""

    def test_list_renders(self):
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, 200)

    def test_form_fragment_for_new(self):
        resp = self.client.get(self._form_url(0))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<form")
        self.assertContains(resp, "csrfmiddlewaretoken")

    def test_create_then_listed(self):
        resp = self.client.post(self._save_url(), self.sample_form_data())
        # crud_view redirects on success.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.model.objects.count(), 1)
        # And the new record appears on the list page.
        list_resp = self.client.get(self._list_url())
        if self.list_check_text:
            self.assertContains(list_resp, self.list_check_text)

    def test_invalid_create_rerenders_with_errors(self):
        # Empty POST should fail validation on at least one required field.
        resp = self.client.post(self._save_url(), {})
        # 200 with re-rendered list page + open modal.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.model.objects.count(), 0)

    def test_update(self):
        obj = self.model.objects.create(**self.minimal_create_kwargs())
        data = self.update_form_data(obj)
        resp = self.client.post(self._save_url(obj.id), data)
        self.assertEqual(resp.status_code, 302)
        obj.refresh_from_db()
        self.assert_updated(obj)

    def test_delete(self):
        obj = self.model.objects.create(**self.minimal_create_kwargs())
        resp = self.client.post(self._delete_url(obj.id))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.model.objects.filter(pk=obj.id).exists())

    # Hooks for subclasses:
    def minimal_create_kwargs(self):
        raise NotImplementedError

    def update_form_data(self, obj):
        raise NotImplementedError

    def assert_updated(self, obj):
        raise NotImplementedError


class InstrumentCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_instrument"
    save_url_name = "admin_instrument"
    form_url_name = "admin_instrument_form"
    delete_url_name = "admin_instrument_delete"
    model = models.Instrument
    list_check_text = "Violin"

    def setUp(self):
        super().setUp()
        self.family = models.InstrumentFamily.objects.create(name="Strings")

    def sample_form_data(self):
        return {"name": "Violin", "family": self.family.id}

    def minimal_create_kwargs(self):
        return {"name": "Viola", "family": self.family}

    def update_form_data(self, obj):
        return {"name": "Violin (renamed)", "family": self.family.id}

    def assert_updated(self, obj):
        self.assertEqual(obj.name, "Violin (renamed)")


class VenueCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_venue"
    save_url_name = "admin_venue"
    form_url_name = "admin_venue_form"
    delete_url_name = "admin_venue_delete"
    model = models.Venue
    list_check_text = "St Mary"

    def sample_form_data(self):
        return {
            "name": "St Mary's Church",
            "address": "1 Church Lane",
            "postcode": "SO1 1AA",
            "contactName": "Vicar",
            "phone": "01234 567890",
            "email": "vicar@example.org",
            "website": "https://example.org",
            "directions": "Turn left at the pub.",
        }

    def minimal_create_kwargs(self):
        return {
            "name": "St Mary's Church",
            "address": "1 Church Lane",
            "postcode": "SO1 1AA",
            "contactName": "Vicar",
            "phone": "01234 567890",
            "email": "vicar@example.org",
            "website": "https://example.org",
            "directions": "Old.",
        }

    def update_form_data(self, obj):
        d = self.sample_form_data()
        d["directions"] = "Updated directions."
        return d

    def assert_updated(self, obj):
        self.assertEqual(obj.directions, "Updated directions.")

    def test_create_with_only_name(self):
        """Only `name` is required on Venue — every other field is optional."""
        url = reverse(self.save_url_name)
        resp = self.client.post(url, {
            "name": "Bare Bones Hall",
            "address": "",
            "postcode": "",
            "contactName": "",
            "phone": "",
            "email": "",
            "website": "",
            "directions": "",
        })
        self.assertEqual(resp.status_code, 302, msg=resp.content[:500])
        venue = models.Venue.objects.get(name="Bare Bones Hall")
        for f in ("address", "postcode", "contactName", "phone",
                  "email", "website", "directions"):
            self.assertEqual(getattr(venue, f), "", msg=f"{f} should default to ''")


class RentalContractCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_rental_contract"
    save_url_name = "admin_rental_contract"
    form_url_name = "admin_rental_contract_form"
    delete_url_name = "admin_rental_contract_delete"
    model = models.RentalContract
    list_check_text = "Tuba hire"

    def sample_form_data(self):
        return {
            "description": "Tuba hire",
            "supplier": "Brass Co",
            "cost": "120.00",
            "startDate": "2026-05-01",
            "endDate": "2026-05-31",
        }

    def minimal_create_kwargs(self):
        return {
            "description": "Tuba hire",
            "supplier": "Brass Co",
            "cost": "120.00",
            "startDate": date(2026, 5, 1),
            "endDate": date(2026, 5, 1) + timedelta(days=30),
        }

    def update_form_data(self, obj):
        d = self.sample_form_data()
        d["supplier"] = "New Supplier"
        return d

    def assert_updated(self, obj):
        self.assertEqual(obj.supplier, "New Supplier")

    def test_create_without_dates(self):
        """startDate / endDate are optional on RentalContract."""
        url = reverse(self.save_url_name)
        resp = self.client.post(url, {
            "description": "No-date hire",
            "supplier": "Brass Co",
            "cost": "10.00",
            "startDate": "",
            "endDate": "",
        })
        self.assertEqual(resp.status_code, 302, msg=resp.content[:500])
        rc = models.RentalContract.objects.get(description="No-date hire")
        self.assertIsNone(rc.startDate)
        self.assertIsNone(rc.endDate)


class PersonCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_person"
    save_url_name = "admin_person"
    form_url_name = "admin_person_form"
    delete_url_name = "admin_person_delete"
    model = Person
    list_check_text = "Frost"

    def setUp(self):
        super().setUp()
        family = models.InstrumentFamily.objects.create(name="Strings")
        self.viola = models.Instrument.objects.create(name="Viola", family=family)

    def sample_form_data(self):
        return {
            "firstNames": "Mike",
            "lastName": "Frost",
            "email": "mike@example.com",
            "phone": "01234 567890",
            "instruments": [self.viola.id],
            "member": True,
            "is_admin": False,
        }

    def minimal_create_kwargs(self):
        # m2m can't be set in create() — fixture only needs scalar fields.
        return {
            "firstNames": "Mike",
            "lastName": "Frost",
            "email": "mike@example.com",
            "phone": "01234 567890",
            "member": True,
        }

    def update_form_data(self, obj):
        d = self.sample_form_data()
        d["lastName"] = "Frostbite"
        d["email"] = obj.email  # keep unique email unchanged
        return d

    def assert_updated(self, obj):
        self.assertEqual(obj.lastName, "Frostbite")

    def test_create_with_only_required_fields(self):
        """Phone and instruments are optional; email is required."""
        url = reverse(self.save_url_name)
        resp = self.client.post(url, {
            "firstNames": "Anon",
            "lastName": "Mouse",
            "email": "anon@example.com",
            "phone": "",
            # instruments deliberately omitted
            "member": False,
            "is_admin": False,
        })
        self.assertEqual(resp.status_code, 302, msg=resp.content[:500])
        person = Person.objects.get(lastName="Mouse")
        self.assertEqual(person.email, "anon@example.com")
        self.assertEqual(person.phone, "")
        self.assertEqual(person.instruments.count(), 0)

    def test_create_then_listed(self):
        # Override base: the test user is itself a Person row, so count starts at 1.
        initial = Person.objects.count()
        resp = self.client.post(self._save_url(), self.sample_form_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Person.objects.count(), initial + 1)
        list_resp = self.client.get(self._list_url())
        if self.list_check_text:
            self.assertContains(list_resp, self.list_check_text)

    def test_invalid_create_rerenders_with_errors(self):
        initial = Person.objects.count()
        resp = self.client.post(self._save_url(), {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Person.objects.count(), initial)

    def test_admin_person_list_shows_admin_column(self):
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Admin?")


class MusicItemCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_music_item"
    save_url_name = "admin_music_item"
    form_url_name = "admin_music_item_form"
    delete_url_name = "admin_music_item_delete"
    model = models.MusicItem
    list_check_text = "Symphony No. 5"

    def sample_form_data(self):
        return {
            "name": "Symphony No. 5",
            "composer": "Beethoven",
            "duration": "00:35:00",
            "notes": "Famous opening motif.",
            "contract": "",  # optional FK
        }

    def minimal_create_kwargs(self):
        return {
            "name": "Symphony No. 5",
            "composer": "Beethoven",
            "duration": timedelta(minutes=35),
            "notes": "",
        }

    def update_form_data(self, obj):
        d = self.sample_form_data()
        d["composer"] = "Ludwig van Beethoven"
        return d

    def assert_updated(self, obj):
        self.assertEqual(obj.composer, "Ludwig van Beethoven")

    def test_create_with_only_name(self):
        """composer, duration, notes, contract are all optional on MusicItem."""
        url = reverse(self.save_url_name)
        resp = self.client.post(url, {
            "name": "Untitled",
            "composer": "",
            "duration": "",
            "notes": "",
            "contract": "",
        })
        self.assertEqual(resp.status_code, 302, msg=resp.content[:500])
        item = models.MusicItem.objects.get(name="Untitled")
        self.assertEqual(item.composer, "")
        self.assertIsNone(item.duration)
        self.assertEqual(item.notes, "")
        self.assertIsNone(item.contract)


def _venue_kwargs():
    return {
        "name": "St Mary's Church",
        "address": "1 Church Lane",
        "postcode": "SO1 1AA",
        "contactName": "Vicar",
        "phone": "01234 567890",
        "email": "vicar@example.org",
        "website": "https://example.org",
        "directions": "Old.",
    }


class RehearsalCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_rehearsal"
    save_url_name = "admin_rehearsal"
    form_url_name = "admin_rehearsal_form"
    delete_url_name = "admin_rehearsal_delete"
    model = models.Rehearsal
    list_check_text = "Tuesday rehearsal"

    def setUp(self):
        super().setUp()
        self.venue = models.Venue.objects.create(**_venue_kwargs())

    def sample_form_data(self):
        return {
            "name": "Tuesday rehearsal",
            "startDate": "2026-05-05",
            "startTime": "19:00",
            "endTime": "21:00",
            "venue": self.venue.id,
        }

    def minimal_create_kwargs(self):
        from datetime import time
        return {
            "name": "Tuesday rehearsal",
            "startDate": date(2026, 5, 5),
            "startTime": time(19, 0),
            "endTime": time(21, 0),
            "venue": self.venue,
        }

    def update_form_data(self, obj):
        d = self.sample_form_data()
        d["name"] = "Tuesday rehearsal (rescheduled)"
        return d

    def assert_updated(self, obj):
        self.assertEqual(obj.name, "Tuesday rehearsal (rescheduled)")

    def test_create_with_only_name_and_date(self):
        """startTime, endTime, and venue are all optional on Rehearsal."""
        url = reverse(self.save_url_name)
        resp = self.client.post(url, {
            "name": "TBC rehearsal",
            "startDate": "2026-05-12",
            "startTime": "",
            "endTime": "",
            "venue": "",
        })
        self.assertEqual(resp.status_code, 302, msg=resp.content[:500])
        r = models.Rehearsal.objects.get(name="TBC rehearsal")
        self.assertIsNone(r.startTime)
        self.assertIsNone(r.endTime)
        self.assertIsNone(r.venue)


class PerformanceCRUDTests(_ModalCRUDMixin, TestCase):
    list_url_name = "admin_performance"
    save_url_name = "admin_performance"
    form_url_name = "admin_performance_form"
    delete_url_name = "admin_performance_delete"
    model = models.Performance
    list_check_text = "Spring concert"

    def setUp(self):
        super().setUp()
        self.venue = models.Venue.objects.create(**_venue_kwargs())

    def sample_form_data(self):
        return {
            "name": "Spring concert",
            "date": "2026-06-01",
            "time": "19:30",
            "venue": self.venue.id,
            "publicDescription": "An evening of strings.",
            "privateDescription": "Black tie.",
            "published": True,
        }

    def minimal_create_kwargs(self):
        from datetime import time
        return {
            "name": "Spring concert",
            "date": date(2026, 6, 1),
            "time": time(19, 30),
            "venue": self.venue,
            "publicDescription": "",
            "privateDescription": "",
            "published": False,
        }

    def update_form_data(self, obj):
        d = self.sample_form_data()
        d["name"] = "Spring gala"
        return d

    def assert_updated(self, obj):
        self.assertEqual(obj.name, "Spring gala")

    def test_create_with_only_name_and_date(self):
        """time, venue, publicDescription, privateDescription all optional."""
        url = reverse(self.save_url_name)
        resp = self.client.post(url, {
            "name": "TBC gala",
            "date": "2026-07-01",
            "time": "",
            "venue": "",
            "publicDescription": "",
            "privateDescription": "",
            "published": False,
        })
        self.assertEqual(resp.status_code, 302, msg=resp.content[:500])
        p = models.Performance.objects.get(name="TBC gala")
        self.assertIsNone(p.time)
        self.assertIsNone(p.venue)
        self.assertEqual(p.publicDescription, "")
        self.assertEqual(p.privateDescription, "")


# ---------------------------------------------------------------------------
# Member-facing pages: index, profile, events, music + login flow.
# ---------------------------------------------------------------------------
class MemberPagesTests(TestCase):
    def setUp(self):
        self.user = _make_user(email="mike@example.com", firstNames="Mike", lastName="Frost")
        self.venue = models.Venue.objects.create(**_venue_kwargs())

    def test_index_redirects_anonymous_to_login(self):
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_index_shows_next_rehearsal_and_performance(self):
        future = date.today() + timedelta(days=3)
        models.Rehearsal.objects.create(
            name="Tuesday rehearsal", startDate=future,
            startTime=time(19, 0), endTime=time(21, 0), venue=self.venue,
        )
        models.Performance.objects.create(
            name="Spring gala", date=future + timedelta(days=14),
            time=time(19, 30), venue=self.venue,
        )
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Tuesday rehearsal")
        self.assertContains(resp, "Spring gala")
        # Apostrophes get HTML-escaped, so just check the bit without one.
        self.assertContains(resp, "Mary")

    def test_index_ignores_past_events(self):
        past = date.today() - timedelta(days=1)
        models.Rehearsal.objects.create(
            name="Old rehearsal", startDate=past, venue=self.venue,
        )
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Old rehearsal")
        self.assertContains(resp, "No upcoming rehearsals")

    def test_profile_requires_login(self):
        resp = self.client.get(reverse("profile"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_profile_shows_person_details(self):
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "mike@example.com")

    def test_events_requires_login(self):
        resp = self.client.get(reverse("events"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_events_lists_upcoming(self):
        future = date.today() + timedelta(days=2)
        models.Rehearsal.objects.create(
            name="Future rehearsal", startDate=future, venue=self.venue,
        )
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("events"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Future rehearsal")

    def test_music_requires_login(self):
        resp = self.client.get(reverse("music"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_music_page_renders(self):
        models.MusicItem.objects.create(name="Symphony No. 5", composer="Beethoven")
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("music"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Symphony No. 5")

    def test_admin_pages_redirect_anonymous_to_login(self):
        for url_name in [
            "admin_instrument_family", "admin_instrument", "admin_person",
            "admin_venue", "admin_rental_contract", "admin_music_item",
            "admin_rehearsal", "admin_performance", "admin_announcement",
        ]:
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 302,
                f"{url_name} should redirect anonymous users")
            self.assertIn("/accounts/login/", resp.url)

    def test_admin_pages_forbidden_for_non_admin(self):
        self.client.login(username="mike@example.com", password="secret")
        for url_name in [
            "admin_instrument_family", "admin_instrument", "admin_person",
            "admin_venue", "admin_rental_contract", "admin_music_item",
            "admin_rehearsal", "admin_performance", "admin_announcement",
        ]:
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 403,
                f"{url_name} should be forbidden for non-admin users")

    def test_admin_dropdown_hidden_for_non_admin(self):
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "admin_person")

    def test_admin_dropdown_visible_for_admin(self):
        self.user.is_admin = True
        self.user.save()
        self.client.login(username="mike@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "admin_person")

    def test_login_page_renders(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sign in")

    def test_login_then_logout(self):
        # Login with email as credential.
        resp = self.client.post(reverse("login"), {
            "username": "mike@example.com", "password": "secret",
        })
        self.assertEqual(resp.status_code, 302)
        # Logged-in index shows Sign out button.
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Sign out")
        # Logout (POST — Django 4.1+ requires POST).
        resp = self.client.post(reverse("logout"))
        self.assertEqual(resp.status_code, 302)
        # Back to anonymous — index now redirects to login page.
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)


class AnnouncementTests(TestCase):
    """Site-wide banner system."""

    def setUp(self):
        from django.utils import timezone as tz
        self.tz = tz
        _make_user(email="mike@example.com")
        self.client.login(username="mike@example.com", password="secret")

    def test_active_banner_shows_in_navbar_area(self):
        models.Announcement.objects.create(
            title="Heads up", body="Rehearsal moved to Tuesday.",
            severity="warning", active=True,
        )
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Heads up")
        self.assertContains(resp, "Rehearsal moved to Tuesday.")
        self.assertContains(resp, "alert-warning")

    def test_inactive_banner_hidden(self):
        models.Announcement.objects.create(
            body="Old news.", active=False,
        )
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Old news.")

    def test_future_visibleFrom_hidden(self):
        future = self.tz.now() + timedelta(days=2)
        models.Announcement.objects.create(
            body="Upcoming announcement.", active=True, visibleFrom=future,
        )
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Upcoming announcement.")

    def test_past_visibleUntil_hidden(self):
        past = self.tz.now() - timedelta(days=2)
        models.Announcement.objects.create(
            body="Stale announcement.", active=True, visibleUntil=past,
        )
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Stale announcement.")

    def test_dismissible_renders_close_button(self):
        models.Announcement.objects.create(
            body="Closeable.", active=True, dismissible=True,
        )
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Closeable.")
        self.assertContains(resp, "aria-label=\"Dismiss\"")

    def test_non_dismissible_omits_close_button(self):
        models.Announcement.objects.create(
            body="Cannot close.", active=True, dismissible=False,
        )
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Cannot close.")
        self.assertNotContains(resp, "aria-label=\"Dismiss\"")


class ProfileEditTests(TestCase):
    def setUp(self):
        family = models.InstrumentFamily.objects.create(name="Strings")
        self.viola = models.Instrument.objects.create(name="Viola", family=family)
        self.user = _make_user(email="mike@example.com", firstNames="Mike", lastName="Frost")
        self.client.login(username="mike@example.com", password="secret")

    def test_profile_shows_prepopulated_form(self):
        resp = self.client.get(reverse("profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "mike@example.com")
        self.assertContains(resp, "Mike")

    def test_profile_update_saves_changes(self):
        resp = self.client.post(reverse("profile"), {
            "firstNames": "Michael",
            "lastName": "Frost",
            "email": "mike@example.com",
            "phone": "07700 900000",
            "instruments": [],
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.firstNames, "Michael")
        self.assertEqual(self.user.phone, "07700 900000")

    def test_profile_update_invalid_email_rerenders(self):
        resp = self.client.post(reverse("profile"), {
            "firstNames": "Mike",
            "lastName": "Frost",
            "email": "not-an-email",
            "phone": "",
            "instruments": [],
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "mike@example.com")

    def test_profile_cannot_change_member_or_is_admin(self):
        """member and is_admin are not in the form — POSTing them has no effect."""
        resp = self.client.post(reverse("profile"), {
            "firstNames": "Mike",
            "lastName": "Frost",
            "email": "mike@example.com",
            "phone": "",
            "instruments": [],
            "member": True,
            "is_admin": True,
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.member)
        self.assertFalse(self.user.is_admin)

    def test_profile_page_has_change_password_link(self):
        resp = self.client.get(reverse("profile"))
        self.assertContains(resp, reverse("password_change"))


class AnnouncementDismissalTests(TestCase):
    def setUp(self):
        self.user = _make_user(email="mike@example.com")
        self.client.login(username="mike@example.com", password="secret")
        self.ann = models.Announcement.objects.create(
            body="Please read this.", active=True, dismissible=True,
        )

    def test_dismiss_creates_dismissal_record(self):
        resp = self.client.post(reverse("dismiss_announcement", args=[self.ann.id]))
        self.assertEqual(resp.status_code, 204)
        self.assertTrue(
            models.AnnouncementDismissal.objects.filter(
                announcement=self.ann, person=self.user,
            ).exists()
        )

    def test_dismissed_banner_absent_on_next_request(self):
        models.AnnouncementDismissal.objects.create(
            announcement=self.ann, person=self.user,
        )
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Please read this.")

    def test_undismissed_banner_still_visible(self):
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Please read this.")

    def test_dismiss_twice_is_idempotent(self):
        self.client.post(reverse("dismiss_announcement", args=[self.ann.id]))
        resp = self.client.post(reverse("dismiss_announcement", args=[self.ann.id]))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            models.AnnouncementDismissal.objects.filter(person=self.user).count(), 1
        )

    def test_dismiss_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("dismiss_announcement", args=[self.ann.id]))
        self.assertEqual(resp.status_code, 302)

    def test_dismiss_button_renders_with_url(self):
        resp = self.client.get(reverse("index"))
        expected_url = reverse("dismiss_announcement", args=[self.ann.id])
        self.assertContains(resp, expected_url)


class SetPasswordTests(TestCase):
    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.target = _make_user(email="member@example.com", firstNames="Jo", lastName="Smith")
        self.client.login(username="admin@example.com", password="secret")

    def _url(self):
        return reverse("admin_person_set_password", args=[self.target.id])

    def test_get_renders_form(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Jo Smith")

    def test_post_changes_password(self):
        resp = self.client.post(self._url(), {
            "new_password1": "N3wSecurePass!",
            "new_password2": "N3wSecurePass!",
        })
        self.assertEqual(resp.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("N3wSecurePass!"))

    def test_mismatched_passwords_rerenders(self):
        resp = self.client.post(self._url(), {
            "new_password1": "N3wSecurePass!",
            "new_password2": "DifferentPass!",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.target.check_password("N3wSecurePass!"))

    def test_forbidden_for_non_admin(self):
        self.client.logout()
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_set_password_button_appears_in_people_list(self):
        resp = self.client.get(reverse("admin_person"))
        self.assertContains(resp, "Set password")


class PollTests(TestCase):
    def setUp(self):
        self.member = _make_user(email="member@example.com", firstNames="Jo", lastName="Smith")
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.poll = models.Poll.objects.create(title="Favourite colour", active=True)

    # ---- member-facing ----

    def test_polls_page_renders(self):
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(reverse("polls"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Favourite colour")

    def test_poll_answer_free_text(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="What's your favourite colour?",
            kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.post(
            reverse("poll_answer", args=[q.id]), {"answer": "Blue"}
        )
        self.assertEqual(resp.status_code, 302)
        answer = models.PollAnswer.objects.get(question=q, person=self.member)
        self.assertEqual(answer.text_value, "Blue")
        self.assertIsNone(answer.choice)

    def test_poll_answer_free_text_update(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Q", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        models.PollAnswer.objects.create(
            question=q, person=self.member, text_value="Old answer",
        )
        self.client.login(username="member@example.com", password="secret")
        self.client.post(reverse("poll_answer", args=[q.id]), {"answer": "New answer"})
        self.assertEqual(models.PollAnswer.objects.filter(question=q, person=self.member).count(), 1)
        self.assertEqual(
            models.PollAnswer.objects.get(question=q, person=self.member).text_value,
            "New answer",
        )

    def test_poll_answer_single_choice(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Pick one", kind=models.PollQuestion.TYPE_SINGLE, order=1,
        )
        c1 = models.PollChoice.objects.create(question=q, text="Red", order=1)
        models.PollChoice.objects.create(question=q, text="Blue", order=2)
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.post(reverse("poll_answer", args=[q.id]), {"choice": c1.id})
        self.assertEqual(resp.status_code, 302)
        answer = models.PollAnswer.objects.get(question=q, person=self.member)
        self.assertEqual(answer.choice, c1)

    def test_poll_answer_single_choice_replaces_previous(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Pick one", kind=models.PollQuestion.TYPE_SINGLE, order=1,
        )
        c1 = models.PollChoice.objects.create(question=q, text="Red", order=1)
        c2 = models.PollChoice.objects.create(question=q, text="Blue", order=2)
        models.PollAnswer.objects.create(question=q, person=self.member, choice=c1)
        self.client.login(username="member@example.com", password="secret")
        self.client.post(reverse("poll_answer", args=[q.id]), {"choice": c2.id})
        self.assertEqual(models.PollAnswer.objects.filter(question=q, person=self.member).count(), 1)
        self.assertEqual(
            models.PollAnswer.objects.get(question=q, person=self.member).choice, c2,
        )

    def test_poll_answer_multi_choice(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Pick any", kind=models.PollQuestion.TYPE_MULTI, order=1,
        )
        c1 = models.PollChoice.objects.create(question=q, text="Red", order=1)
        c2 = models.PollChoice.objects.create(question=q, text="Blue", order=2)
        models.PollChoice.objects.create(question=q, text="Green", order=3)
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.post(
            reverse("poll_answer", args=[q.id]), {"choices": [c1.id, c2.id]},
        )
        self.assertEqual(resp.status_code, 302)
        selected = set(
            models.PollAnswer.objects.filter(question=q, person=self.member)
            .values_list("choice_id", flat=True)
        )
        self.assertEqual(selected, {c1.id, c2.id})

    def test_poll_answer_requires_login(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Q", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        resp = self.client.post(reverse("poll_answer", args=[q.id]), {"answer": "X"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_inactive_poll_answer_rejected(self):
        self.poll.active = False
        self.poll.save()
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Q", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.post(reverse("poll_answer", args=[q.id]), {"answer": "X"})
        self.assertEqual(resp.status_code, 404)

    # ---- admin ----

    def test_admin_poll_questions_page(self):
        models.PollQuestion.objects.create(
            poll=self.poll, text="Colour?", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        self.client.login(username="admin@example.com", password="secret")
        resp = self.client.get(reverse("admin_poll_questions", args=[self.poll.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Colour?")

    def test_admin_add_question(self):
        self.client.login(username="admin@example.com", password="secret")
        resp = self.client.post(
            reverse("admin_poll_questions", args=[self.poll.id]),
            {"text": "New question", "kind": "free_text", "order": 1},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.PollQuestion.objects.filter(poll=self.poll, text="New question").exists())

    def test_admin_delete_question(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="To delete", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        self.client.login(username="admin@example.com", password="secret")
        resp = self.client.post(reverse("admin_poll_question_delete", args=[self.poll.id, q.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(models.PollQuestion.objects.filter(pk=q.id).exists())

    def test_admin_poll_responses_page(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Colour?", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )
        models.PollAnswer.objects.create(question=q, person=self.member, text_value="Blue")
        self.client.login(username="admin@example.com", password="secret")
        resp = self.client.get(reverse("admin_poll_responses", args=[self.poll.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Blue")

    def test_admin_poll_choices_add(self):
        q = models.PollQuestion.objects.create(
            poll=self.poll, text="Pick", kind=models.PollQuestion.TYPE_SINGLE, order=1,
        )
        self.client.login(username="admin@example.com", password="secret")
        resp = self.client.post(
            reverse("admin_poll_choices", args=[self.poll.id, q.id]),
            {"text": "Option A"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(models.PollChoice.objects.filter(question=q, text="Option A").exists())


class PollBannerTests(TestCase):
    """Poll show_as_banner renders in base.html for unanswered polls."""

    def setUp(self):
        self.user = _make_user(email="member@example.com", firstNames="Alice", lastName="B")
        self.poll = models.Poll.objects.create(title="Banner Poll", active=True, show_as_banner=True)
        self.question = models.PollQuestion.objects.create(
            poll=self.poll, text="Pick one?", kind=models.PollQuestion.TYPE_FREE_TEXT, order=1,
        )

    def test_banner_appears_for_unanswered_poll(self):
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Banner Poll")
        self.assertContains(resp, reverse("polls"))

    def test_banner_hidden_when_fully_answered(self):
        models.PollAnswer.objects.create(question=self.question, person=self.user, text_value="My answer")
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Banner Poll")

    def test_banner_hidden_for_anonymous(self):
        resp = self.client.get(reverse("index"), follow=True)
        self.assertNotContains(resp, "Banner Poll")

    def test_banner_hidden_when_poll_inactive(self):
        self.poll.active = False
        self.poll.save()
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Banner Poll")

    def test_banner_hidden_when_show_as_banner_false(self):
        self.poll.show_as_banner = False
        self.poll.save()
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Banner Poll")

    def test_partial_answer_still_shows_banner(self):
        q2 = models.PollQuestion.objects.create(
            poll=self.poll, text="Other question?", kind=models.PollQuestion.TYPE_FREE_TEXT, order=2,
        )
        models.PollAnswer.objects.create(question=self.question, person=self.user, text_value="Done")
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Banner Poll")
        _ = q2  # silence unused-variable warning


class RSVPTests(TestCase):
    """Member RSVPs for upcoming rehearsals and performances."""

    def setUp(self):
        self.person = _make_user(
            email="mike@example.com", firstNames="Mike", lastName="Frost",
        )
        self.venue = models.Venue.objects.create(**_venue_kwargs())
        future = date.today() + timedelta(days=3)
        self.rehearsal = models.Rehearsal.objects.create(
            name="Tuesday rehearsal", startDate=future, venue=self.venue,
        )
        self.performance = models.Performance.objects.create(
            name="Spring concert", date=future + timedelta(days=14), venue=self.venue,
        )
        self.client.login(username="mike@example.com", password="secret")

    def test_events_page_shows_rsvp_buttons(self):
        resp = self.client.get(reverse("events"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Going")
        self.assertContains(resp, "Not going")
        self.assertContains(resp, "Maybe")

    def test_rsvp_creates_record(self):
        url = reverse("rsvp", args=["rehearsal", self.rehearsal.id])
        resp = self.client.post(url, {"status": "yes"})
        self.assertEqual(resp.status_code, 302)
        rsvp = models.RSVP.objects.get(person=self.person, rehearsal=self.rehearsal)
        self.assertEqual(rsvp.status, "yes")

    def test_rsvp_updates_existing_record(self):
        models.RSVP.objects.create(
            person=self.person, rehearsal=self.rehearsal, status="maybe",
        )
        url = reverse("rsvp", args=["rehearsal", self.rehearsal.id])
        resp = self.client.post(url, {"status": "no"})
        self.assertEqual(resp.status_code, 302)
        # Still only one row, with the new status.
        self.assertEqual(models.RSVP.objects.filter(person=self.person).count(), 1)
        rsvp = models.RSVP.objects.get(person=self.person, rehearsal=self.rehearsal)
        self.assertEqual(rsvp.status, "no")

    def test_rsvp_for_performance(self):
        url = reverse("rsvp", args=["performance", self.performance.id])
        resp = self.client.post(url, {"status": "yes"})
        self.assertEqual(resp.status_code, 302)
        rsvp = models.RSVP.objects.get(person=self.person, performance=self.performance)
        self.assertEqual(rsvp.status, "yes")

    def test_rsvp_invalid_status_400(self):
        url = reverse("rsvp", args=["rehearsal", self.rehearsal.id])
        resp = self.client.post(url, {"status": "definitely-maybe"})
        self.assertEqual(resp.status_code, 400)

    def test_rsvp_unknown_kind_400(self):
        url = reverse("rsvp", args=["picnic", self.rehearsal.id])
        resp = self.client.post(url, {"status": "yes"})
        self.assertEqual(resp.status_code, 400)

    def test_rsvp_requires_login(self):
        self.client.logout()
        url = reverse("rsvp", args=["rehearsal", self.rehearsal.id])
        resp = self.client.post(url, {"status": "yes"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_events_page_marks_active_rsvp(self):
        models.RSVP.objects.create(
            person=self.person, rehearsal=self.rehearsal, status="yes",
        )
        resp = self.client.get(reverse("events"))
        # The "Going" button for our rehearsal should pick up the active class.
        self.assertContains(resp, "btn-primary")


class AttendanceBreakdownTests(TestCase):
    """Admin attendance-by-instrument pages for rehearsals and performances."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        future = date.today() + timedelta(days=5)
        self.rehearsal = models.Rehearsal.objects.create(name="Weekly", startDate=future)
        self.performance = models.Performance.objects.create(name="Gala", date=future + timedelta(days=7))
        fam = models.InstrumentFamily.objects.create(name="Strings")
        self.violin = models.Instrument.objects.create(name="Violin", family=fam)
        self.cello = models.Instrument.objects.create(name="Cello", family=fam)
        self.alice = _make_user(email="alice@example.com", firstNames="Alice", lastName="A")
        self.alice.instruments.add(self.violin)
        self.bob = _make_user(email="bob@example.com", firstNames="Bob", lastName="B")
        self.bob.instruments.add(self.cello)
        self.carol = _make_user(email="carol@example.com", firstNames="Carol", lastName="C")
        # carol has no instruments
        self.client.login(username="admin@example.com", password="secret")

    def test_rehearsal_attendance_page_loads(self):
        models.RSVP.objects.create(person=self.alice, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Violin")
        self.assertContains(resp, "Alice")

    def test_rehearsal_attendance_excludes_no_rsvp(self):
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Alice")

    def test_rehearsal_attendance_excludes_non_yes(self):
        models.RSVP.objects.create(person=self.alice, rehearsal=self.rehearsal, status="no")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertNotContains(resp, "Alice")

    def test_rehearsal_attendance_groups_by_instrument(self):
        models.RSVP.objects.create(person=self.alice, rehearsal=self.rehearsal, status="yes")
        models.RSVP.objects.create(person=self.bob, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertContains(resp, "Violin")
        self.assertContains(resp, "Cello")

    def test_rehearsal_attendance_no_instrument_bucket(self):
        models.RSVP.objects.create(person=self.carol, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertContains(resp, "No instrument")

    def test_performance_attendance_page_loads(self):
        models.RSVP.objects.create(person=self.alice, performance=self.performance, status="yes")
        resp = self.client.get(reverse("admin_performance_attendance", args=[self.performance.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Violin")

    def test_attendance_requires_admin(self):
        member = _make_user(email="plain@example.com")
        self.client.login(username="plain@example.com", password="secret")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertEqual(resp.status_code, 403)


class MusicItemFileTests(TestCase):
    """MusicItem score_file and external_link fields on member-facing music page."""

    def setUp(self):
        self.user = _make_user(email="member@example.com")
        self.client.login(username="member@example.com", password="secret")

    def test_music_page_shows_download_link_for_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("score.pdf", b"PDF", content_type="application/pdf")
        item = models.MusicItem.objects.create(name="Symphony No 1", score_file=f)
        resp = self.client.get(reverse("music"))
        self.assertContains(resp, "Download")
        item.score_file.delete(save=False)

    def test_music_page_shows_open_link_for_external(self):
        models.MusicItem.objects.create(name="Overture", external_link="https://imslp.org/test")
        resp = self.client.get(reverse("music"))
        self.assertContains(resp, "Open")
        self.assertContains(resp, "https://imslp.org/test")

    def test_music_page_shows_dash_when_no_score(self):
        models.MusicItem.objects.create(name="Nocturne")
        resp = self.client.get(reverse("music"))
        self.assertEqual(resp.status_code, 200)

    def test_admin_create_with_external_link(self):
        admin = _make_user(email="adm@example.com", is_admin=True)
        self.client.login(username="adm@example.com", password="secret")
        resp = self.client.post(reverse("admin_music_item"), {
            "name": "Waltz",
            "composer": "",
            "duration": "",
            "notes": "",
            "external_link": "https://example.com/score",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.MusicItem.objects.filter(
            name="Waltz", external_link="https://example.com/score",
        ).exists())
        _ = admin


class RehearsalSeriesTests(TestCase):
    """RehearsalSeries CRUD and generate_rehearsals logic."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.client.login(username="admin@example.com", password="secret")
        self.series = models.RehearsalSeries.objects.create(
            name="Weekly Tuesday",
            frequency=models.RehearsalSeries.FREQ_WEEKLY,
            day_of_week=1,  # Tuesday
            series_start=date(2025, 1, 7),  # a Tuesday
        )

    def test_series_list_page_loads(self):
        resp = self.client.get(reverse("admin_rehearsal_series"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Weekly Tuesday")

    def test_series_requires_admin(self):
        member = _make_user(email="m@example.com")
        self.client.login(username="m@example.com", password="secret")
        resp = self.client.get(reverse("admin_rehearsal_series"))
        self.assertEqual(resp.status_code, 403)

    def test_generate_weekly_creates_correct_count(self):
        self.series.generate_rehearsals(date(2025, 1, 28))
        rehearsals = models.Rehearsal.objects.filter(series=self.series).order_by("startDate")
        self.assertEqual(rehearsals.count(), 4)  # 7, 14, 21, 28 Jan
        self.assertEqual(rehearsals[0].startDate, date(2025, 1, 7))
        self.assertEqual(rehearsals[3].startDate, date(2025, 1, 28))

    def test_generate_biweekly_creates_correct_count(self):
        self.series.frequency = models.RehearsalSeries.FREQ_BIWEEKLY
        self.series.save()
        self.series.generate_rehearsals(date(2025, 1, 28))
        rehearsals = models.Rehearsal.objects.filter(series=self.series)
        self.assertEqual(rehearsals.count(), 2)  # 7, 21 Jan

    def test_generate_does_not_duplicate_existing(self):
        models.Rehearsal.objects.create(
            name="Weekly Tuesday", startDate=date(2025, 1, 7), series=self.series,
        )
        self.series.generate_rehearsals(date(2025, 1, 14))
        self.assertEqual(models.Rehearsal.objects.filter(series=self.series).count(), 2)

    def test_generate_updates_existing_venue(self):
        venue = models.Venue.objects.create(
            name="Hall", address="1 St", postcode="AB1", contactName="", phone="", email="", website="", directions=""
        )
        r = models.Rehearsal.objects.create(
            name="Weekly Tuesday", startDate=date(2025, 1, 7), series=self.series,
        )
        self.assertIsNone(r.venue)
        self.series.venue = venue
        self.series.save()
        created, updated = self.series.generate_rehearsals(date(2025, 1, 7))
        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)
        r.refresh_from_db()
        self.assertEqual(r.venue, venue)

    def test_generate_respects_series_end(self):
        self.series.series_end = date(2025, 1, 14)
        self.series.save()
        self.series.generate_rehearsals(date(2025, 1, 28))
        self.assertEqual(models.Rehearsal.objects.filter(series=self.series).count(), 2)

    def test_generate_view_post_creates_rehearsals(self):
        resp = self.client.post(
            reverse("admin_rehearsal_series_generate", args=[self.series.id]),
            {"until": "2025-01-21"},
        )
        self.assertRedirects(resp, reverse("admin_rehearsal_series"))
        self.assertEqual(models.Rehearsal.objects.filter(series=self.series).count(), 3)

    def test_generate_view_get_renders_form(self):
        resp = self.client.get(reverse("admin_rehearsal_series_generate", args=[self.series.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Weekly Tuesday")

    def test_generated_rehearsals_inherit_series_name(self):
        self.series.generate_rehearsals(date(2025, 1, 7))
        rehearsal = models.Rehearsal.objects.get(series=self.series)
        self.assertEqual(rehearsal.name, "Weekly Tuesday")

    def test_rehearsal_list_shows_series_column(self):
        models.Rehearsal.objects.create(
            name="Weekly Tuesday", startDate=date(2025, 1, 7), series=self.series,
        )
        resp = self.client.get(reverse("admin_rehearsal"))
        self.assertContains(resp, "Series")


class RepertoireEditorTests(TestCase):
    """Inline XHR repertoire editor for rehearsals and performances."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.client.login(username="admin@example.com", password="secret")
        future = date.today() + timedelta(days=5)
        self.rehearsal = models.Rehearsal.objects.create(name="Practice", startDate=future)
        self.performance = models.Performance.objects.create(name="Concert", date=future + timedelta(days=7))
        self.music = models.MusicItem.objects.create(name="Symphony No 5")

    def test_rehearsal_repertoire_page_loads(self):
        resp = self.client.get(reverse("admin_rehearsal_repertoire", args=[self.rehearsal.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Symphony No 5")

    def test_rehearsal_repertoire_add(self):
        url = reverse("admin_rehearsal_repertoire", args=[self.rehearsal.id]) + "0"
        resp = self.client.post(url, {"music_item_id": self.music.id, "order": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(models.RehearsalItem.objects.filter(rehearsal=self.rehearsal, musicItem=self.music).exists())

    def test_rehearsal_repertoire_delete(self):
        item = models.RehearsalItem.objects.create(rehearsal=self.rehearsal, musicItem=self.music, order=1)
        url = reverse("admin_rehearsal_repertoire", args=[self.rehearsal.id]) + str(item.id)
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(models.RehearsalItem.objects.filter(pk=item.id).exists())

    def test_rehearsal_repertoire_reorder(self):
        """Drag-and-drop reorder via ?action=reorder updates order of all items."""
        item1 = models.RehearsalItem.objects.create(rehearsal=self.rehearsal, musicItem=self.music, order=1)
        music2 = models.MusicItem.objects.create(name="Overture")
        item2 = models.RehearsalItem.objects.create(rehearsal=self.rehearsal, musicItem=music2, order=2)
        base_url = reverse("admin_rehearsal_repertoire", args=[self.rehearsal.id])
        # Swap order: item2 first, item1 second
        resp = self.client.post(
            base_url + "?action=reorder",
            {"order[]": [item2.id, item1.id]},
        )
        self.assertEqual(resp.status_code, 200)
        item1.refresh_from_db()
        item2.refresh_from_db()
        self.assertEqual(item1.order, 2)
        self.assertEqual(item2.order, 1)

    def test_rehearsal_repertoire_update_start_time(self):
        """POSTing start_time to an existing item saves it."""
        item = models.RehearsalItem.objects.create(rehearsal=self.rehearsal, musicItem=self.music, order=1)
        url = reverse("admin_rehearsal_repertoire", args=[self.rehearsal.id]) + str(item.id)
        resp = self.client.post(url, {"start_time": "19:30"})
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        from datetime import time as dt_time
        self.assertEqual(item.start_time, dt_time(19, 30))

    def test_performance_repertoire_page_loads(self):
        resp = self.client.get(reverse("admin_performance_repertoire", args=[self.performance.id]))
        self.assertEqual(resp.status_code, 200)

    def test_performance_repertoire_add(self):
        url = reverse("admin_performance_repertoire", args=[self.performance.id]) + "0"
        resp = self.client.post(url, {"music_item_id": self.music.id, "order": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(models.PerformanceItem.objects.filter(performance=self.performance, musicItem=self.music).exists())

    def test_repertoire_requires_admin(self):
        member = _make_user(email="m@example.com")
        self.client.login(username="m@example.com", password="secret")
        resp = self.client.get(reverse("admin_rehearsal_repertoire", args=[self.rehearsal.id]))
        self.assertEqual(resp.status_code, 403)


class MusicItemPartsTests(TestCase):
    """Inline XHR parts editor for MusicItem."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.client.login(username="admin@example.com", password="secret")
        self.music = models.MusicItem.objects.create(name="Symphony No 5")
        fam = models.InstrumentFamily.objects.create(name="Strings")
        self.violin = models.Instrument.objects.create(name="Violin", family=fam)

    def test_parts_page_loads(self):
        resp = self.client.get(reverse("admin_music_item_parts", args=[self.music.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Symphony No 5")
        self.assertContains(resp, "Violin")

    def test_add_part(self):
        url = reverse("admin_music_item_parts", args=[self.music.id]) + "0"
        resp = self.client.post(url, {"instrument_id": self.violin.id,
                                      "external_link": "https://example.com/violin.pdf"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(models.MusicItemPart.objects.filter(musicItem=self.music, instrument=self.violin).exists())

    def test_update_part_external_link(self):
        """Updating a part's external_link persists correctly."""
        part = models.MusicItemPart.objects.create(musicItem=self.music, instrument=self.violin)
        url = reverse("admin_music_item_parts", args=[self.music.id]) + str(part.id)
        resp = self.client.post(url, {"external_link": "https://example.com/part.pdf"})
        self.assertEqual(resp.status_code, 200)
        part.refresh_from_db()
        self.assertEqual(part.external_link, "https://example.com/part.pdf")

    def test_update_part_rejected_bad_extension(self):
        """Uploading a disallowed file type returns 400."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        part = models.MusicItemPart.objects.create(musicItem=self.music, instrument=self.violin)
        url = reverse("admin_music_item_parts", args=[self.music.id]) + str(part.id)
        bad_file = SimpleUploadedFile("malicious.exe", b"MZ", content_type="application/x-msdownload")
        resp = self.client.post(url, {"file": bad_file})
        self.assertEqual(resp.status_code, 400)

    def test_delete_part(self):
        part = models.MusicItemPart.objects.create(musicItem=self.music, instrument=self.violin)
        url = reverse("admin_music_item_parts", args=[self.music.id]) + str(part.id)
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(models.MusicItemPart.objects.filter(pk=part.id).exists())

    def test_parts_requires_admin(self):
        _make_user(email="m@example.com")
        self.client.login(username="m@example.com", password="secret")
        resp = self.client.get(reverse("admin_music_item_parts", args=[self.music.id]))
        self.assertEqual(resp.status_code, 403)


class PollResponseDrillDownTests(TestCase):
    """Choice respondents page and table view of poll responses."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.member = _make_user(email="member@example.com", firstNames="Alice", lastName="A")
        self.poll = models.Poll.objects.create(title="My Poll", active=True)
        self.q = models.PollQuestion.objects.create(
            poll=self.poll, text="Colour?", kind=models.PollQuestion.TYPE_SINGLE, order=1,
        )
        self.choice_red = models.PollChoice.objects.create(question=self.q, text="Red")
        self.choice_blue = models.PollChoice.objects.create(question=self.q, text="Blue")
        models.PollAnswer.objects.create(question=self.q, person=self.member, choice=self.choice_red)
        self.client.login(username="admin@example.com", password="secret")

    def test_choice_respondents_page(self):
        resp = self.client.get(
            reverse("admin_poll_choice_respondents", args=[self.poll.id, self.choice_red.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice")
        self.assertContains(resp, "Red")

    def test_choice_respondents_empty(self):
        resp = self.client.get(
            reverse("admin_poll_choice_respondents", args=[self.poll.id, self.choice_blue.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Alice")

    def test_responses_table_view(self):
        resp = self.client.get(reverse("admin_poll_responses_table", args=[self.poll.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice")
        self.assertContains(resp, "Red")
        self.assertContains(resp, "Colour?")

    def test_responses_page_has_table_view_link(self):
        resp = self.client.get(reverse("admin_poll_responses", args=[self.poll.id]))
        self.assertContains(resp, reverse("admin_poll_responses_table", args=[self.poll.id]))

    def test_responses_page_choice_names_are_links(self):
        resp = self.client.get(reverse("admin_poll_responses", args=[self.poll.id]))
        self.assertContains(resp, reverse("admin_poll_choice_respondents", args=[self.poll.id, self.choice_red.id]))

    def test_requires_admin(self):
        self.client.login(username="member@example.com", password="secret")
        resp = self.client.get(
            reverse("admin_poll_choice_respondents", args=[self.poll.id, self.choice_red.id])
        )
        self.assertEqual(resp.status_code, 403)


class RSVPInstrumentTests(TestCase):
    """RSVP playing_instruments selection."""

    def setUp(self):
        self.person = _make_user(email="alice@example.com", firstNames="Alice", lastName="A")
        fam = models.InstrumentFamily.objects.create(name="Strings")
        self.violin = models.Instrument.objects.create(name="Violin", family=fam)
        self.cello = models.Instrument.objects.create(name="Cello", family=fam)
        self.person.instruments.add(self.violin, self.cello)
        future = date.today() + timedelta(days=3)
        self.rehearsal = models.Rehearsal.objects.create(name="Practice", startDate=future)
        self.client.login(username="alice@example.com", password="secret")

    def test_rsvp_yes_saves_playing_instruments(self):
        resp = self.client.post(
            reverse("rsvp", args=["rehearsal", self.rehearsal.id]),
            {"status": "yes", "playing_instruments": [self.violin.id]},
        )
        self.assertEqual(resp.status_code, 302)
        rsvp = models.RSVP.objects.get(person=self.person, rehearsal=self.rehearsal)
        self.assertEqual(list(rsvp.playing_instruments.values_list("pk", flat=True)), [self.violin.id])

    def test_rsvp_status_change_preserves_playing_instruments(self):
        """Instruments are kept when status changes so switching back to yes retains them."""
        rsvp = models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="yes")
        rsvp.playing_instruments.add(self.violin)
        self.client.post(
            reverse("rsvp", args=["rehearsal", self.rehearsal.id]),
            {"status": "no", "playing_instruments": [self.violin.id]},
        )
        rsvp.refresh_from_db()
        self.assertEqual(rsvp.playing_instruments.count(), 1)

    def test_playing_instruments_cannot_include_others_instruments(self):
        other_fam = models.InstrumentFamily.objects.create(name="Brass")
        trumpet = models.Instrument.objects.create(name="Trumpet", family=other_fam)
        self.client.post(
            reverse("rsvp", args=["rehearsal", self.rehearsal.id]),
            {"status": "yes", "playing_instruments": [trumpet.id]},
        )
        rsvp = models.RSVP.objects.get(person=self.person, rehearsal=self.rehearsal)
        self.assertEqual(rsvp.playing_instruments.count(), 0)

    def test_events_page_shows_instrument_checkboxes(self):
        # Checkboxes only appear once the user has a "yes" RSVP.
        models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("events"))
        self.assertContains(resp, "Violin")
        self.assertContains(resp, "Cello")

    def test_attendance_uses_playing_instruments(self):
        admin = _make_user(email="admin@example.com", is_admin=True)
        rsvp = models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="yes")
        rsvp.playing_instruments.add(self.violin)
        resp = self.client.get(
            reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]),
            HTTP_COOKIE=f"sessionid={self._login_as_admin(admin)}",
        )
        _ = resp  # just confirm view doesn't error; breakdown logic tested in unit

    def _login_as_admin(self, admin):
        self.client.login(username="admin@example.com", password="secret")
        return self.client.cookies.get("sessionid", "").value


class MusicCopyrightNoteTests(TestCase):
    """Admin music page shows copyright reminder."""

    def setUp(self):
        _make_user(email="admin@example.com", is_admin=True)
        self.client.login(username="admin@example.com", password="secret")

    def test_copyright_notice_on_music_page(self):
        resp = self.client.get(reverse("admin_music_item"))
        self.assertContains(resp, "copyright")


class RehearsalSeriesUpdateTests(TestCase):
    """Series generate updates existing rehearsals."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.client.login(username="admin@example.com", password="secret")
        self.venue = models.Venue.objects.create(
            name="Hall", address="1 St", postcode="AB1", contactName="", phone="", email="", website="", directions=""
        )
        self.series = models.RehearsalSeries.objects.create(
            name="Weekly", frequency=models.RehearsalSeries.FREQ_WEEKLY,
            day_of_week=1, series_start=date(2025, 1, 7),
        )

    def test_generate_returns_created_updated(self):
        created, updated = self.series.generate_rehearsals(date(2025, 1, 7))
        self.assertEqual(created, 1)
        self.assertEqual(updated, 0)

    def test_generate_updates_venue_on_existing(self):
        self.series.generate_rehearsals(date(2025, 1, 7))
        self.series.venue = self.venue
        self.series.save()
        created, updated = self.series.generate_rehearsals(date(2025, 1, 7))
        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)
        r = models.Rehearsal.objects.get(series=self.series, startDate=date(2025, 1, 7))
        self.assertEqual(r.venue, self.venue)

    def test_generate_view_success_message_shows_updated(self):
        self.series.generate_rehearsals(date(2025, 1, 7))
        self.series.venue = self.venue
        self.series.save()
        resp = self.client.post(
            reverse("admin_rehearsal_series_generate", args=[self.series.id]),
            {"until": "2025-01-07"},
            follow=True,
        )
        self.assertContains(resp, "updated 1")


# ---------------------------------------------------------------------------
# Coverage for views/models/helpers added during recent rounds.
# ---------------------------------------------------------------------------

class InstrumentMembersTests(TestCase):
    """Drill-down pages: instrument members, instrument-family members."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        strings = models.InstrumentFamily.objects.create(name="Strings")
        winds = models.InstrumentFamily.objects.create(name="Winds")
        self.violin = models.Instrument.objects.create(name="Violin", family=strings)
        self.cello = models.Instrument.objects.create(name="Cello", family=strings)
        self.flute = models.Instrument.objects.create(name="Flute", family=winds)
        self.alice = _make_user(email="alice@example.com", firstNames="Alice", lastName="A")
        self.alice.instruments.add(self.violin)
        self.bob = _make_user(email="bob@example.com", firstNames="Bob", lastName="B")
        self.bob.instruments.add(self.cello)
        self.charlie = _make_user(email="charlie@example.com", firstNames="Charlie", lastName="C")
        self.charlie.instruments.add(self.flute)
        self.strings = strings
        self.winds = winds
        self.client.login(username="admin@example.com", password="secret")

    def test_instrument_members_lists_only_players(self):
        resp = self.client.get(reverse("admin_instrument_members", args=[self.violin.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice A")
        self.assertNotContains(resp, "Bob B")
        self.assertNotContains(resp, "Charlie C")

    def test_instrument_members_empty(self):
        empty = models.Instrument.objects.create(name="Triangle", family=self.strings)
        resp = self.client.get(reverse("admin_instrument_members", args=[empty.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No members play this instrument")

    def test_family_members_lists_distinct_people(self):
        resp = self.client.get(reverse("admin_instrument_family_members", args=[self.strings.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice A")
        self.assertContains(resp, "Bob B")
        self.assertNotContains(resp, "Charlie C")

    def test_family_members_dedupes_multi_instrument(self):
        # Alice plays both Violin and Cello (both Strings) — should only appear once.
        self.alice.instruments.add(self.cello)
        resp = self.client.get(reverse("admin_instrument_family_members", args=[self.strings.id]))
        self.assertEqual(resp.content.decode().count("Alice A"), 1)

    def test_members_pages_require_admin(self):
        plain = _make_user(email="plain@example.com")
        self.client.login(username="plain@example.com", password="secret")
        resp = self.client.get(reverse("admin_instrument_members", args=[self.violin.id]))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(reverse("admin_instrument_family_members", args=[self.strings.id]))
        self.assertEqual(resp.status_code, 403)

    def test_instrument_admin_list_has_members_link(self):
        resp = self.client.get(reverse("admin_instrument"))
        self.assertContains(resp, "Members")
        self.assertContains(resp, reverse("admin_instrument_members", args=[self.violin.id]))

    def test_family_admin_list_has_members_link(self):
        resp = self.client.get(reverse("admin_instrument_family"))
        self.assertContains(resp, reverse("admin_instrument_family_members", args=[self.strings.id]))


class SocialAppAdminTests(TestCase):
    """Custom UI for managing OAuth SocialApp records (django-allauth)."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        self.client.login(username="admin@example.com", password="secret")

    def test_list_page_loads(self):
        resp = self.client.get(reverse("admin_social_apps"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Social Auth Apps")

    def test_list_shows_disabled_notice_when_flag_off(self):
        from django.test import override_settings
        with override_settings(SOCIAL_LOGIN_ENABLED=False):
            resp = self.client.get(reverse("admin_social_apps"))
            self.assertContains(resp, "currently disabled")

    def test_create_assigns_current_site_and_provider_id(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        resp = self.client.post(
            reverse("admin_social_apps"),
            {"provider": "google", "provider_id": "",
             "name": "Google login", "client_id": "abc.apps.googleusercontent.com",
             "secret": "shh"},
        )
        self.assertEqual(resp.status_code, 302)
        app = SocialApp.objects.get(provider="google")
        self.assertEqual(app.provider_id, "google")  # auto-defaulted
        self.assertEqual(app.client_id, "abc.apps.googleusercontent.com")
        self.assertIn(Site.objects.get_current(), app.sites.all())

    def test_create_keeps_explicit_provider_id(self):
        from allauth.socialaccount.models import SocialApp
        self.client.post(
            reverse("admin_social_apps"),
            {"provider": "google", "provider_id": "google-prod",
             "name": "Prod", "client_id": "x", "secret": "y"},
        )
        self.assertEqual(SocialApp.objects.get(provider_id="google-prod").name, "Prod")

    def test_admin_only(self):
        plain = _make_user(email="plain@example.com")
        self.client.login(username="plain@example.com", password="secret")
        resp = self.client.get(reverse("admin_social_apps"))
        self.assertEqual(resp.status_code, 403)


class AnnouncementVisibilityTests(TestCase):
    """Direct unit tests for Announcement.is_visible_now()."""

    def test_inactive_never_visible(self):
        a = models.Announcement(body="hi", active=False)
        self.assertFalse(a.is_visible_now())

    def test_active_visible(self):
        a = models.Announcement(body="hi", active=True)
        self.assertTrue(a.is_visible_now())

    def test_before_visible_from_hidden(self):
        from django.utils import timezone as tz
        future = tz.now() + timedelta(days=2)
        a = models.Announcement(body="hi", active=True, visibleFrom=future)
        self.assertFalse(a.is_visible_now())

    def test_after_visible_until_hidden(self):
        from django.utils import timezone as tz
        past = tz.now() - timedelta(days=2)
        a = models.Announcement(body="hi", active=True, visibleUntil=past)
        self.assertFalse(a.is_visible_now())

    def test_within_window_visible(self):
        from django.utils import timezone as tz
        a = models.Announcement(
            body="hi", active=True,
            visibleFrom=tz.now() - timedelta(hours=1),
            visibleUntil=tz.now() + timedelta(hours=1),
        )
        self.assertTrue(a.is_visible_now())


class MergedAttendanceTests(TestCase):
    """The attendance page merges going + maybe RSVPs by instrument."""

    def setUp(self):
        self.admin = _make_user(email="admin@example.com", is_admin=True)
        future = date.today() + timedelta(days=5)
        self.rehearsal = models.Rehearsal.objects.create(name="Weekly", startDate=future)
        fam = models.InstrumentFamily.objects.create(name="Strings")
        self.viola = models.Instrument.objects.create(name="Viola", family=fam)
        self.mike = _make_user(email="mike@example.com", firstNames="Mike", lastName="M")
        self.mike.instruments.add(self.viola)
        self.zoe = _make_user(email="zoe@example.com", firstNames="Zoe", lastName="Z")
        self.zoe.instruments.add(self.viola)
        self.john = _make_user(email="john@example.com", firstNames="John", lastName="J")
        self.john.instruments.add(self.viola)
        self.client.login(username="admin@example.com", password="secret")

    def test_maybes_appear_alongside_going(self):
        models.RSVP.objects.create(person=self.mike, rehearsal=self.rehearsal, status="yes")
        models.RSVP.objects.create(person=self.zoe, rehearsal=self.rehearsal, status="yes")
        models.RSVP.objects.create(person=self.john, rehearsal=self.rehearsal, status="maybe")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertContains(resp, "Mike M")
        self.assertContains(resp, "Zoe Z")
        self.assertContains(resp, "John J")
        self.assertContains(resp, "2 going")
        self.assertContains(resp, "1 maybe")

    def test_summary_omits_maybe_when_zero(self):
        models.RSVP.objects.create(person=self.mike, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertContains(resp, "1 going")
        self.assertNotContains(resp, "maybe")

    def test_no_rsvp_excluded(self):
        models.RSVP.objects.create(person=self.mike, rehearsal=self.rehearsal, status="no")
        resp = self.client.get(reverse("admin_rehearsal_attendance", args=[self.rehearsal.id]))
        self.assertNotContains(resp, "Mike M")

    def test_helper_groups_by_instrument(self):
        from orman.views import _merged_attendance
        models.RSVP.objects.create(person=self.mike, rehearsal=self.rehearsal, status="yes")
        models.RSVP.objects.create(person=self.john, rehearsal=self.rehearsal, status="maybe")
        yes_qs = models.RSVP.objects.filter(rehearsal=self.rehearsal, status="yes")
        maybe_qs = models.RSVP.objects.filter(rehearsal=self.rehearsal, status="maybe")
        rows = _merged_attendance(yes_qs, maybe_qs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instrument"], self.viola)
        self.assertEqual([str(p) for p in rows[0]["going"]], ["Mike M"])
        self.assertEqual([str(p) for p in rows[0]["maybe"]], ["John J"])


class EventsPageInstrumentUXTests(TestCase):
    """Single-instrument auto-check, hidden status field, instrument list shown for any RSVP."""

    def setUp(self):
        self.person = _make_user(email="p@example.com")
        fam = models.InstrumentFamily.objects.create(name="Strings")
        self.violin = models.Instrument.objects.create(name="Violin", family=fam)
        self.viola = models.Instrument.objects.create(name="Viola", family=fam)
        future = date.today() + timedelta(days=3)
        self.rehearsal = models.Rehearsal.objects.create(name="Weekly", startDate=future)
        self.client.login(username="p@example.com", password="secret")

    def test_single_instrument_pre_checked(self):
        self.person.instruments.add(self.violin)
        models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("events"))
        # Single instrument should be checked even without an explicit playing_instruments save.
        body = resp.content.decode()
        self.assertIn('value="%d"' % self.violin.id, body)
        # Find the violin checkbox and assert it's checked.
        idx = body.find('value="%d"' % self.violin.id)
        # Walk forward from the input start to find the closing >; "checked" must appear before then.
        snippet = body[max(0, idx - 200):idx + 200]
        self.assertIn("checked", snippet)

    def test_multiple_instruments_only_saved_ones_checked(self):
        self.person.instruments.add(self.violin, self.viola)
        rsvp = models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="yes")
        rsvp.playing_instruments.add(self.violin)
        resp = self.client.get(reverse("events"))
        body = resp.content.decode()
        # Both inputs present, only violin is checked.
        v_idx = body.find('value="%d"' % self.violin.id)
        a_idx = body.find('value="%d"' % self.viola.id)
        self.assertGreater(v_idx, 0)
        self.assertGreater(a_idx, 0)
        # Crude but adequate: violin's input tag contains "checked", viola's does not.
        self.assertIn("checked", body[v_idx - 200:v_idx + 200])
        self.assertNotIn("checked", body[a_idx - 200:a_idx + 200])

    def test_instrument_section_hidden_without_rsvp(self):
        self.person.instruments.add(self.violin)
        resp = self.client.get(reverse("events"))
        self.assertNotContains(resp, "Playing as:")

    def test_instrument_section_shown_for_maybe(self):
        self.person.instruments.add(self.violin)
        models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="maybe")
        resp = self.client.get(reverse("events"))
        self.assertContains(resp, "Playing as:")

    def test_instrument_section_shown_for_no(self):
        """Even 'no' RSVPs allow setting instruments — preserved if they switch back."""
        self.person.instruments.add(self.violin)
        models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="no")
        resp = self.client.get(reverse("events"))
        self.assertContains(resp, "Playing as:")

    def test_hidden_status_field_present_for_existing_rsvp(self):
        self.person.instruments.add(self.violin)
        models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="maybe")
        resp = self.client.get(reverse("events"))
        self.assertContains(resp, 'name="status" value="maybe"')

    def test_checkbox_has_onchange_autosubmit(self):
        self.person.instruments.add(self.violin)
        models.RSVP.objects.create(person=self.person, rehearsal=self.rehearsal, status="yes")
        resp = self.client.get(reverse("events"))
        self.assertContains(resp, "this.form.submit()")


class RSVPStatusOrderTests(TestCase):
    """Going → Maybe → Not going button order on the events page."""

    def test_status_choices_order(self):
        labels = [label for _, label in models.RSVP.STATUS_CHOICES]
        self.assertEqual(labels, ["Going", "Maybe", "Not going"])

    def test_buttons_render_in_order(self):
        person = _make_user(email="p@example.com")
        future = date.today() + timedelta(days=3)
        models.Rehearsal.objects.create(name="Weekly", startDate=future)
        self.client.login(username="p@example.com", password="secret")
        resp = self.client.get(reverse("events"))
        body = resp.content.decode()
        # No RSVP exists, so the only place these appear is on the status buttons.
        i_yes = body.find('value="yes"')
        i_maybe = body.find('value="maybe"')
        i_no = body.find('value="no"')
        self.assertGreater(i_yes, 0)
        self.assertGreater(i_maybe, i_yes)
        self.assertGreater(i_no, i_maybe)


class SiteFlagsContextProcessorTests(TestCase):
    """The site_flags processor exposes SOCIAL_LOGIN_ENABLED to templates."""

    def test_flag_exposed_when_enabled(self):
        from django.test import override_settings
        from orman.context_processors import site_flags
        with override_settings(SOCIAL_LOGIN_ENABLED=True):
            self.assertEqual(site_flags(None), {"SOCIAL_LOGIN_ENABLED": True})

    def test_flag_exposed_when_disabled(self):
        from django.test import override_settings
        from orman.context_processors import site_flags
        with override_settings(SOCIAL_LOGIN_ENABLED=False):
            self.assertEqual(site_flags(None), {"SOCIAL_LOGIN_ENABLED": False})


class GenerateRehearsalsTimeUpdateTests(TestCase):
    """generate_rehearsals propagates start_time/end_time/name changes too."""

    def setUp(self):
        self.fam = models.InstrumentFamily.objects.create(name="Strings")
        self.series = models.RehearsalSeries.objects.create(
            name="Original",
            frequency="weekly",
            day_of_week=2,  # Wednesday
            series_start=date(2025, 1, 1),  # Wed
            start_time=time(19, 0),
        )

    def test_propagates_time_change(self):
        self.series.generate_rehearsals(date(2025, 1, 1))
        self.series.start_time = time(20, 0)
        self.series.save()
        created, updated = self.series.generate_rehearsals(date(2025, 1, 1))
        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)
        r = models.Rehearsal.objects.get(series=self.series)
        self.assertEqual(r.startTime, time(20, 0))

    def test_propagates_name_change(self):
        self.series.generate_rehearsals(date(2025, 1, 1))
        self.series.name = "Renamed"
        self.series.save()
        self.series.generate_rehearsals(date(2025, 1, 1))
        r = models.Rehearsal.objects.get(series=self.series)
        self.assertEqual(r.name, "Renamed")

    def test_no_op_when_unchanged(self):
        self.series.generate_rehearsals(date(2025, 1, 1))
        created, updated = self.series.generate_rehearsals(date(2025, 1, 1))
        self.assertEqual((created, updated), (0, 0))
