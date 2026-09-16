import uuid as _uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class InstrumentFamily(models.Model):
    name = models.CharField(max_length=200)
    def __str__(self):
        return self.name

class Instrument(models.Model):
    name = models.CharField(max_length=200)
    family = models.ForeignKey(InstrumentFamily, on_delete=models.PROTECT)
    def __str__(self):
        return self.name


class PersonManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_admin", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class Person(AbstractBaseUser, PermissionsMixin):
    firstNames = models.CharField(max_length=200, verbose_name="first names")
    lastName = models.CharField(max_length=200, verbose_name="last name")
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=200, blank=True)
    instruments = models.ManyToManyField(Instrument, blank=True)
    member = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False, verbose_name="admin")
    is_active = models.BooleanField(default=True)
    calendar_token = models.UUIDField(default=_uuid.uuid4, editable=False)

    objects = PersonManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["firstNames", "lastName"]

    @property
    def is_staff(self):
        return self.is_admin or self.is_superuser

    def __str__(self):
        return self.firstNames + " " + self.lastName

class Venue(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200, blank=True)
    postcode = models.CharField(max_length=200, blank=True)
    contactName = models.CharField(max_length=200, blank=True, verbose_name="contact name")
    phone = models.CharField(max_length=200, blank=True)
    email = models.CharField(max_length=200, blank=True)
    website = models.CharField(max_length=200, blank=True)
    directions = models.TextField(blank=True)
    def __str__(self):
        return self.name

class RentalContract(models.Model):
    description = models.TextField()
    supplier = models.CharField(max_length=200)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    startDate = models.DateField(blank=True, null=True, verbose_name="start date")
    endDate = models.DateField(blank=True, null=True, verbose_name="end date")
    def __str__(self):
        return self.description

class MusicItem(models.Model):
    name = models.CharField(max_length=200)
    composer = models.CharField(max_length=200, blank=True)
    duration = models.DurationField(blank=True, null=True)
    notes = models.TextField(blank=True)
    contract = models.ForeignKey(RentalContract, on_delete=models.PROTECT, blank=True, null=True)
    def __str__(self):
        return self.name

def _part_upload_to(instance, filename):
    """Store uploaded part files with a UUID stem to prevent path traversal / enumeration."""
    import uuid, os
    ext = os.path.splitext(filename)[1].lower()
    return f"parts/{uuid.uuid4().hex}{ext}"


class MusicItemPart(models.Model):
    """A single uploaded file or link for a MusicItem, assignable to the full
    score and/or one or more instruments — e.g. one file can cover both the
    score and percussion, instead of needing a duplicate upload."""
    musicItem = models.ForeignKey(MusicItem, on_delete=models.PROTECT)
    instruments = models.ManyToManyField(Instrument, blank=True, related_name="music_item_parts")
    is_score = models.BooleanField(default=False, verbose_name="full score")
    file = models.FileField(upload_to=_part_upload_to, blank=True, null=True, verbose_name="file upload")
    external_link = models.URLField(blank=True, verbose_name="external link")

    class Meta:
        ordering = ["-is_score", "id"]

    @property
    def label(self):
        names = [str(i) for i in self.instruments.all()]
        if self.is_score:
            return "Full score" if not names else "Full score + " + ", ".join(names)
        return ", ".join(names) if names else "Untitled part"

    def __str__(self):
        return self.musicItem.name + " - " + self.label

class RehearsalSeries(models.Model):
    FREQ_WEEKLY = "weekly"
    FREQ_BIWEEKLY = "biweekly"
    FREQ_CHOICES = [
        (FREQ_WEEKLY, "Weekly"),
        (FREQ_BIWEEKLY, "Every two weeks"),
    ]
    DAY_CHOICES = [
        (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"),
        (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
    ]
    name = models.CharField(max_length=200)
    frequency = models.CharField(max_length=20, choices=FREQ_CHOICES, default=FREQ_WEEKLY, verbose_name="frequency")
    day_of_week = models.SmallIntegerField(choices=DAY_CHOICES, verbose_name="day of week")
    start_time = models.TimeField(blank=True, null=True, verbose_name="start time")
    end_time = models.TimeField(blank=True, null=True, verbose_name="end time")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, blank=True, null=True)
    series_start = models.DateField(verbose_name="series start")
    series_end = models.DateField(blank=True, null=True, verbose_name="series end")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "rehearsal series"

    def __str__(self):
        return self.name

    def generate_rehearsals(self, until):
        """Create or update Rehearsal rows for this series up to `until`. Returns (created, updated)."""
        from datetime import timedelta
        step = timedelta(weeks=1 if self.frequency == self.FREQ_WEEKLY else 2)
        d = self.series_start
        while d.weekday() != self.day_of_week:
            d += timedelta(days=1)
        ceiling = min(until, self.series_end) if self.series_end else until
        created = updated = 0
        while d <= ceiling:
            existing = Rehearsal.objects.filter(series=self, startDate=d).first()
            if existing:
                changed = []
                for attr, val in [("name", self.name), ("startTime", self.start_time),
                                   ("endTime", self.end_time), ("venue", self.venue)]:
                    if getattr(existing, attr) != val:
                        setattr(existing, attr, val)
                        changed.append(attr)
                if changed:
                    existing.save(update_fields=changed)
                    updated += 1
            else:
                Rehearsal.objects.create(
                    name=self.name,
                    startDate=d,
                    startTime=self.start_time,
                    endTime=self.end_time,
                    venue=self.venue,
                    series=self,
                )
                created += 1
            d += step
        return created, updated


class Rehearsal(models.Model):
    name = models.CharField(max_length=200)
    startDate = models.DateField(verbose_name="start date")
    startTime = models.TimeField(blank=True, null=True, verbose_name="start time")
    endTime = models.TimeField(blank=True, null=True, verbose_name="end time")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, blank=True, null=True)
    series = models.ForeignKey(
        RehearsalSeries, on_delete=models.SET_NULL, blank=True, null=True, related_name="rehearsals",
    )
    rehearsalItems = models.ManyToManyField(MusicItem, through='RehearsalItem')
    def __str__(self):
        return self.name

class Performance(models.Model):
    name = models.CharField(max_length=200)
    date = models.DateField()
    time = models.TimeField(blank=True, null=True)
    publicDescription = models.TextField(blank=True, verbose_name="public description")
    privateDescription = models.TextField(blank=True, verbose_name="private description")
    published = models.BooleanField(default=False)
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, blank=True, null=True)
    performanceItems = models.ManyToManyField(MusicItem, through='PerformanceItem')
    def __str__(self):
        return self.name

class RehearsalItem(models.Model):
    rehearsal = models.ForeignKey(Rehearsal, on_delete=models.PROTECT)
    musicItem = models.ForeignKey(MusicItem, on_delete=models.PROTECT)
    order = models.IntegerField()
    start_time = models.TimeField(blank=True, null=True, verbose_name="start time")

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.rehearsal.name + " - " + self.musicItem.name

class PerformanceItem(models.Model):
    performance = models.ForeignKey(Performance, on_delete=models.PROTECT)
    musicItem = models.ForeignKey(MusicItem, on_delete=models.PROTECT)
    order = models.IntegerField()
    start_time = models.TimeField(blank=True, null=True, verbose_name="start time")

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.performance.name + " - " + self.musicItem.name

class PerformanceSeats(models.Model):
    performance = models.ForeignKey(Performance, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    def __str__(self):
        return self.performance.name + " - " + self.person.firstNames + " " + self.person.lastName + " - " + self.instrument.name
# TODO: Assign music parts to people


class RSVP(models.Model):
    """A member's RSVP for a single Rehearsal or Performance.

    Exactly one of `rehearsal` / `performance` is set per row. The (person,
    event) pair is unique so each person has at most one RSVP per event.
    """
    STATUS_YES = "yes"
    STATUS_NO = "no"
    STATUS_MAYBE = "maybe"
    STATUS_CHOICES = [
        (STATUS_YES, "Going"),
        (STATUS_MAYBE, "Maybe"),
        (STATUS_NO, "Not going"),
    ]
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="rsvps")
    rehearsal = models.ForeignKey(
        Rehearsal, on_delete=models.CASCADE, blank=True, null=True, related_name="rsvps",
    )
    performance = models.ForeignKey(
        Performance, on_delete=models.CASCADE, blank=True, null=True, related_name="rsvps",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    note = models.TextField(blank=True)
    playing_instruments = models.ManyToManyField(
        Instrument, blank=True, related_name="rsvps",
        verbose_name="playing instruments",
    )
    updatedAt = models.DateTimeField(auto_now=True, verbose_name="updated at")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["person", "rehearsal"],
                condition=models.Q(rehearsal__isnull=False),
                name="unique_rsvp_rehearsal",
            ),
            models.UniqueConstraint(
                fields=["person", "performance"],
                condition=models.Q(performance__isnull=False),
                name="unique_rsvp_performance",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(rehearsal__isnull=False, performance__isnull=True)
                    | models.Q(rehearsal__isnull=True, performance__isnull=False)
                ),
                name="rsvp_exactly_one_event",
            ),
        ]

    @property
    def event(self):
        return self.rehearsal or self.performance

    def __str__(self):
        return f"{self.person} → {self.event}: {self.status}"


class Poll(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    show_as_banner = models.BooleanField(default=False, verbose_name="show as banner")
    createdAt = models.DateTimeField(auto_now_add=True, verbose_name="created at")

    class Meta:
        ordering = ["-createdAt"]

    def __str__(self):
        return self.title


class PollQuestion(models.Model):
    TYPE_FREE_TEXT = "free_text"
    TYPE_SINGLE = "single_choice"
    TYPE_MULTI = "multi_choice"
    TYPE_CHOICES = [
        (TYPE_FREE_TEXT, "Free text"),
        (TYPE_SINGLE, "Single choice"),
        (TYPE_MULTI, "Multiple choice"),
    ]
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField(verbose_name="question text")
    kind = models.CharField(
        max_length=20, choices=TYPE_CHOICES, default=TYPE_FREE_TEXT, verbose_name="type",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.text[:80]


class PollChoice(models.Model):
    question = models.ForeignKey(PollQuestion, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.text


class PollAnswer(models.Model):
    """One person's answer to one question.

    free_text: one row per (question, person), text_value set, choice null.
    single_choice: one row per (question, person), choice set, text_value blank.
    multi_choice: one row per selected choice per person; multiple rows allowed.
    """
    question = models.ForeignKey(PollQuestion, on_delete=models.CASCADE, related_name="answers")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="poll_answers")
    text_value = models.TextField(blank=True)
    choice = models.ForeignKey(
        PollChoice, on_delete=models.SET_NULL, blank=True, null=True, related_name="answers",
    )
    updatedAt = models.DateTimeField(auto_now=True, verbose_name="updated at")

    class Meta:
        constraints = [
            # free_text / single_choice: at most one answer per (question, person)
            models.UniqueConstraint(
                fields=["question", "person"],
                condition=models.Q(choice__isnull=True),
                name="unique_freetext_answer",
            ),
            # multi_choice: at most one row per (question, person, choice)
            models.UniqueConstraint(
                fields=["question", "person", "choice"],
                condition=models.Q(choice__isnull=False),
                name="unique_choice_answer",
            ),
        ]

    def __str__(self):
        return f"{self.person} → {self.question}"


class Announcement(models.Model):
    """A site-wide banner shown to logged-in members.

    Severities map to DaisyUI's alert classes (alert-info, alert-success,
    alert-warning, alert-error). Optional visibleFrom/visibleUntil let
    you schedule a banner ahead of time and have it disappear automatically.
    """
    SEVERITY_CHOICES = [
        ("info", "Info"),
        ("success", "Success"),
        ("warning", "Warning"),
        ("error", "Error"),
    ]
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="info")
    active = models.BooleanField(default=True)
    dismissible = models.BooleanField(default=True)
    visibleFrom = models.DateTimeField(blank=True, null=True, verbose_name="visible from")
    visibleUntil = models.DateTimeField(blank=True, null=True, verbose_name="visible until")
    createdAt = models.DateTimeField(auto_now_add=True, verbose_name="created at")

    class Meta:
        ordering = ["-createdAt"]

    def __str__(self):
        return self.title or (self.body[:50] + ("…" if len(self.body) > 50 else ""))

    def is_visible_now(self, now=None):
        """Whether this banner should be shown at the given moment."""
        from django.utils import timezone as _tz
        if not self.active:
            return False
        now = now or _tz.now()
        if self.visibleFrom and now < self.visibleFrom:
            return False
        if self.visibleUntil and now > self.visibleUntil:
            return False
        return True


class AnnouncementDismissal(models.Model):
    announcement = models.ForeignKey(
        Announcement, on_delete=models.CASCADE, related_name="dismissals",
    )
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="announcement_dismissals",
    )
    dismissedAt = models.DateTimeField(auto_now_add=True, verbose_name="dismissed at")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "person"], name="unique_dismissal",
            ),
        ]


class Passkey(models.Model):
    """A WebAuthn passkey registered to a person."""
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="passkeys",
    )
    name = models.CharField(max_length=100, default="Passkey")
    credential_id = models.BinaryField(unique=True)
    credential_public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.person} — {self.name}"


class MailingList(models.Model):
    PROVIDER_MANUAL = "manual"
    PROVIDER_GOOGLE_GROUPS = "google_groups"
    PROVIDER_MAILCHIMP = "mailchimp"
    PROVIDER_BREVO = "brevo"
    PROVIDER_CHOICES = [
        (PROVIDER_MANUAL, "Manual / CSV export"),
        (PROVIDER_GOOGLE_GROUPS, "Google Groups"),
        (PROVIDER_MAILCHIMP, "Mailchimp"),
        (PROVIDER_BREVO, "Brevo"),
    ]

    FILTER_MANUAL = "manual"
    FILTER_ALL_MEMBERS = "all_members"
    FILTER_INSTRUMENT = "instrument"
    FILTER_FAMILY = "instrument_family"
    FILTER_CHOICES = [
        (FILTER_MANUAL, "Manual membership"),
        (FILTER_ALL_MEMBERS, "All active members"),
        (FILTER_INSTRUMENT, "Instrument (all players)"),
        (FILTER_FAMILY, "Instrument family / section"),
    ]

    name = models.CharField(max_length=200)
    provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_MANUAL,
    )
    filter_type = models.CharField(
        max_length=20, choices=FILTER_CHOICES, default=FILTER_MANUAL,
        verbose_name="membership filter",
        help_text=(
            "Manual: you control membership. "
            "Dynamic options recompute from the roster automatically when you reconcile."
        ),
    )
    filter_instrument = models.ForeignKey(
        "Instrument", on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="instrument (filter)",
        help_text="Required when filter is 'Instrument'.",
    )
    filter_instrument_family = models.ForeignKey(
        "InstrumentFamily", on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="instrument family (filter)",
        help_text="Required when filter is 'Instrument family / section'.",
    )
    address = models.EmailField(
        blank=True, verbose_name="group/list email",
        help_text="The display email address for this list (e.g. members@myorchestra.com).",
    )
    description = models.TextField(blank=True)
    api_key = models.CharField(
        max_length=500, blank=True, verbose_name="API key",
        help_text="Mailchimp or Brevo API key. Leave blank for manual/Google-Groups lists.",
    )
    list_id = models.CharField(
        max_length=200, blank=True, verbose_name="list / audience / group ID",
        help_text=(
            "Mailchimp: audience ID (Audience → Settings). "
            "Brevo: numeric list ID. "
            "Google Groups: group email address."
        ),
    )
    google_service_account_json = models.TextField(
        blank=True, verbose_name="Google service-account JSON",
        help_text=(
            "Google Groups only: paste the full service-account JSON key here. "
            "The service account needs domain-wide delegation with "
            "https://www.googleapis.com/auth/admin.directory.group.member scope."
        ),
    )
    google_delegated_admin = models.EmailField(
        blank=True, verbose_name="Google delegated-admin email",
        help_text="Google Groups only: an admin account to impersonate (e.g. admin@myorchestra.com).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "mailing list"
        verbose_name_plural = "mailing lists"

    @property
    def is_dynamic(self) -> bool:
        return self.filter_type != self.FILTER_MANUAL

    def get_computed_members(self):
        """Return a Person queryset for dynamic lists, or None for manual lists."""
        base = Person.objects.filter(member=True, is_active=True)
        if self.filter_type == self.FILTER_ALL_MEMBERS:
            return base
        if self.filter_type == self.FILTER_INSTRUMENT and self.filter_instrument_id:
            return base.filter(instruments=self.filter_instrument)
        if self.filter_type == self.FILTER_FAMILY and self.filter_instrument_family_id:
            return base.filter(instruments__family=self.filter_instrument_family).distinct()
        return None  # manual

    def __str__(self):
        return self.name


class MailingListMember(models.Model):
    SYNC_PENDING = "pending"
    SYNC_OK = "synced"
    SYNC_ERROR = "error"
    SYNC_CHOICES = [
        (SYNC_PENDING, "Pending"),
        (SYNC_OK, "Synced"),
        (SYNC_ERROR, "Error"),
    ]

    mailing_list = models.ForeignKey(
        MailingList, on_delete=models.CASCADE, related_name="list_members",
    )
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="list_memberships",
    )
    added_at = models.DateTimeField(auto_now_add=True)
    sync_status = models.CharField(
        max_length=10, choices=SYNC_CHOICES, default=SYNC_PENDING,
    )
    sync_error = models.CharField(max_length=500, blank=True)
    synced_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["person__lastName", "person__firstNames"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailing_list", "person"], name="unique_mailinglist_member",
            ),
        ]

    def __str__(self):
        return f"{self.person} → {self.mailing_list}"


def _carousel_upload_to(instance, filename):
    """Store uploaded carousel images with a UUID stem to prevent path traversal / enumeration."""
    import uuid, os
    ext = os.path.splitext(filename)[1].lower()
    return f"carousel/{uuid.uuid4().hex}{ext}"


class SiteContent(models.Model):
    """Singleton row holding the admin-editable content shown on the public home page."""
    description = models.TextField(
        blank=True, verbose_name="description",
        help_text="Shown on the public home page. Supports Markdown formatting.",
    )
    contactName = models.CharField(max_length=200, blank=True, verbose_name="contact name")
    contactEmail = models.CharField(max_length=200, blank=True, verbose_name="contact email")
    contactPhone = models.CharField(max_length=200, blank=True, verbose_name="contact phone")
    contactAddress = models.TextField(blank=True, verbose_name="contact address")

    class Meta:
        verbose_name_plural = "site content"

    def __str__(self):
        return "Home page content"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CarouselImage(models.Model):
    """An image shown in the home page carousel, admin-managed."""
    image = models.ImageField(upload_to=_carousel_upload_to)
    caption = models.CharField(max_length=200, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.caption or f"Image {self.pk}"