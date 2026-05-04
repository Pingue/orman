"""Custom template filters for orman admin pages."""
from django import template
from django.db.models.manager import Manager

register = template.Library()


@register.filter
def crud_value(obj, field_name):
    """Look up `field_name` on `obj` and render it for the admin list table.

    Handles dotted paths ('venue.name'), m2m managers (joins names with ','),
    booleans (✓/✗), and falls back to str().
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
        return "\u2713" if value else "\u2717"
    if value is None:
        return ""
    return value
