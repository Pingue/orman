"""ModelForms for orman admin CRUD pages.

These forms are deliberately thin wrappers around the models. We use them to
get free validation + a single source of truth for the field list rendered in
the modal forms (templates/_modal_form.html).
"""
from django import forms
from django.contrib.auth.forms import SetPasswordForm
from allauth.socialaccount.models import SocialApp

from . import models


def normalise_url(value: str) -> str:
    """Prepend https:// if the user omitted the scheme (e.g. 'example.com')."""
    value = value.strip()
    if value and not value.startswith(("http://", "https://", "ftp://")):
        value = "https://" + value
    return value


class _DaisyFormMixin:
    """Apply DaisyUI/Tailwind classes to all rendered widgets.

    Keeps individual ModelForm definitions short — we override the widget
    attrs centrally rather than per-field.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            css = widget.attrs.get("class", "")
            if isinstance(widget, forms.Textarea):
                widget.attrs["class"] = (css + " textarea textarea-bordered w-full").strip()
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = (css + " select select-bordered w-full").strip()
            elif isinstance(widget, forms.SelectMultiple):
                widget.attrs["class"] = (css + " select select-bordered w-full h-32").strip()
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = (css + " checkbox").strip()
            elif isinstance(widget, (forms.DateInput, forms.TimeInput, forms.DateTimeInput)):
                widget.attrs["class"] = (css + " input input-bordered w-full").strip()
            elif isinstance(widget, forms.NumberInput):
                widget.attrs["class"] = (css + " input input-bordered w-full").strip()
            else:
                widget.attrs["class"] = (css + " input input-bordered w-full").strip()


class DaisySetPasswordForm(_DaisyFormMixin, SetPasswordForm):
    pass


class InstrumentFamilyForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.InstrumentFamily
        fields = ["name"]


class InstrumentForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Instrument
        fields = ["name", "family"]


class ProfileForm(_DaisyFormMixin, forms.ModelForm):
    """Self-serve editing — excludes admin-only fields member and is_admin."""
    class Meta:
        model = models.Person
        fields = ["firstNames", "lastName", "email", "phone", "instruments"]
        widgets = {
            "email": forms.EmailInput(),
        }


class PersonForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Person
        fields = [
            "firstNames", "lastName", "email", "phone",
            "instruments", "member", "is_admin",
        ]
        widgets = {
            "email": forms.EmailInput(),
        }


class VenueForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Venue
        fields = [
            "name", "address", "postcode",
            "contactName", "phone", "email", "website", "directions",
        ]
        widgets = {
            "directions": forms.Textarea(attrs={"rows": 4}),
            "email": forms.EmailInput(),
            "website": forms.URLInput(),
        }


class RentalContractForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.RentalContract
        fields = ["description", "supplier", "cost", "startDate", "endDate"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "startDate": forms.DateInput(attrs={"type": "date"}),
            "endDate": forms.DateInput(attrs={"type": "date"}),
        }


class MusicItemForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.MusicItem
        fields = ["name", "composer", "duration", "notes", "contract", "score_file", "external_link"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_external_link(self):
        return normalise_url(self.cleaned_data.get("external_link", ""))


class RehearsalSeriesForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.RehearsalSeries
        fields = ["name", "frequency", "day_of_week", "start_time", "end_time", "venue", "series_start", "series_end"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "series_start": forms.DateInput(attrs={"type": "date"}),
            "series_end": forms.DateInput(attrs={"type": "date"}),
        }


class RehearsalSeriesGenerateForm(forms.Form):
    until = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Generate up to",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["until"].widget.attrs["class"] = "input input-bordered"


class RehearsalForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Rehearsal
        fields = ["name", "startDate", "startTime", "endTime", "venue"]
        widgets = {
            "startDate": forms.DateInput(attrs={"type": "date"}),
            "startTime": forms.TimeInput(attrs={"type": "time"}),
            "endTime": forms.TimeInput(attrs={"type": "time"}),
        }


class PollForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Poll
        fields = ["title", "description", "active", "show_as_banner"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class PollQuestionForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.PollQuestion
        fields = ["text", "kind", "order"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 2}),
        }


class PollChoiceForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.PollChoice
        fields = ["text"]


class AnnouncementForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Announcement
        fields = [
            "title", "body", "severity", "active", "dismissible",
            "visibleFrom", "visibleUntil",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3}),
            "visibleFrom": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "visibleUntil": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class SocialAppForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = SocialApp
        fields = ["provider", "provider_id", "name", "client_id", "secret"]
        help_texts = {
            "provider": "e.g. google, github, microsoft",
            "provider_id": "Leave blank to use the provider name as the ID. "
                           "Only needed when configuring multiple apps for the same provider.",
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.provider_id:
            instance.provider_id = instance.provider
        if commit:
            instance.save()
            from django.contrib.sites.models import Site
            instance.sites.set([Site.objects.get_current()])
        return instance


class PerformanceForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.Performance
        fields = [
            "name", "date", "time", "venue",
            "publicDescription", "privateDescription", "published",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time": forms.TimeInput(attrs={"type": "time"}),
            "publicDescription": forms.Textarea(attrs={"rows": 3}),
            "privateDescription": forms.Textarea(attrs={"rows": 3}),
        }


class SiteContentForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.SiteContent
        fields = ["description", "contactName", "contactEmail", "contactPhone", "contactAddress"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 10}),
            "contactEmail": forms.EmailInput(),
            "contactAddress": forms.Textarea(attrs={"rows": 3}),
        }


class CarouselImageForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.CarouselImage
        fields = ["image", "caption", "order"]


class MailingListForm(_DaisyFormMixin, forms.ModelForm):
    class Meta:
        model = models.MailingList
        fields = [
            "name", "filter_type", "filter_instrument", "filter_instrument_family",
            "provider", "address", "description",
            "api_key", "list_id",
            "google_service_account_json", "google_delegated_admin",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "google_service_account_json": forms.Textarea(attrs={"rows": 4,
                "placeholder": '{"type":"service_account","project_id":"…"}'}),
            "address": forms.EmailInput(),
            "google_delegated_admin": forms.EmailInput(),
            "api_key": forms.PasswordInput(render_value=True),
        }

    def clean(self):
        cleaned = super().clean()
        ft = cleaned.get("filter_type")
        if ft == models.MailingList.FILTER_INSTRUMENT and not cleaned.get("filter_instrument"):
            self.add_error("filter_instrument", "Select an instrument for this filter type.")
        if ft == models.MailingList.FILTER_FAMILY and not cleaned.get("filter_instrument_family"):
            self.add_error("filter_instrument_family", "Select an instrument family for this filter type.")
        return cleaned
