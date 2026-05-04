"""Template context processors for orman."""
from django.conf import settings
from django.utils import timezone

from . import models


def announcements(request):
    """Expose active, in-window, un-dismissed announcements to every template."""
    now = timezone.now()
    qs = models.Announcement.objects.filter(active=True)
    visible = [a for a in qs if a.is_visible_now(now)]

    if request.user.is_authenticated:
        dismissed = set(
            models.AnnouncementDismissal.objects
            .filter(person=request.user)
            .values_list("announcement_id", flat=True)
        )
        visible = [a for a in visible if a.id not in dismissed]

    return {"announcements": visible}


def site_flags(request):
    return {"SOCIAL_LOGIN_ENABLED": settings.SOCIAL_LOGIN_ENABLED}


def poll_banners(request):
    """Expose active banner-polls that the user hasn't fully answered."""
    if not request.user.is_authenticated:
        return {"poll_banners": []}

    banner_polls = models.Poll.objects.filter(active=True, show_as_banner=True).prefetch_related("questions")
    answered_question_ids = set(
        models.PollAnswer.objects
        .filter(person=request.user)
        .values_list("question_id", flat=True)
        .distinct()
    )

    pending = []
    for poll in banner_polls:
        if any(q.id not in answered_question_ids for q in poll.questions.all()):
            pending.append(poll)

    return {"poll_banners": pending}
