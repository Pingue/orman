"""Custom template filters for orman admin pages."""
import markdown as _markdown
from django import template
from django.db.models.manager import Manager
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def crud_value(obj, field_name):
    """Look up `field_name` on `obj` and render it for the admin list table.

    Handles dotted paths ('venue.name'), m2m managers (joins names with ','),
    booleans (✓/✗), image/file fields (thumbnail), and falls back to str().
    """
    value = obj
    for part in field_name.split("."):
        if value is None:
            return ""
        value = getattr(value, part, None)
        # Auto-call zero-arg callables so things like `obj.get_full_name` work.
        if callable(value) and not isinstance(value, type):
            try:
                value = value()
            except TypeError:
                pass
    if isinstance(value, Manager):
        return ", ".join(str(v) for v in value.all())
    if isinstance(value, bool):
        return "✓" if value else "✗"
    if value is None:
        return ""
    if hasattr(value, "url"):  # FieldFile (FileField / ImageField)
        if not value:
            return ""
        return format_html('<img src="{}" alt="" class="h-12 w-auto rounded">', value.url)
    return value


@register.filter
def markdownify(text):
    """Render admin-authored Markdown to HTML.

    Only site admins can edit the source (SiteContent.description), so
    trusting the resulting HTML is the same trust boundary as everywhere
    else admins can already inject content (e.g. MailingList templates).
    """
    if not text:
        return ""
    html = _markdown.markdown(text, extensions=["nl2br", "sane_lists"])
    return mark_safe(html)
