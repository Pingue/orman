"""Helpers for the admin-style CRUD pages.

The pages share a common shape:

    GET  /admin_<thing>/                -> list page with create/edit modal
    GET  /admin_<thing>/<id>/form/      -> form HTML fragment for the modal
    POST /admin_<thing>/                -> create
    POST /admin_<thing>/<id>/           -> update
    POST /admin_<thing>/<id>/delete/    -> delete (using POST not DELETE so plain
                                          forms work without JS)

All forms are rendered into a single DaisyUI <dialog> on the list page, so
no separate detail/edit/delete pages are needed.
"""
from __future__ import annotations

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render


LIST_TEMPLATE = "_admin_list_page.html"
FORM_TEMPLATE = "_modal_form.html"


def crud_view(
    request,
    *,
    model,
    form_class,
    page_title,
    columns,
    list_context_name,
    list_url_name,
    form_url_name,
    save_url_name,
    delete_url_name,
    singular=None,
    id=None,
    row_extra_actions=None,
    page_notice=None,
    extra_context=None,
):
    """Generic CRUD handler. Pages plug their model + form + display config in.

    columns: list of {"label": str, "field": str} — `field` may be a dotted
             path (e.g. "family.name"); rendered via the crud_value filter.
    *_url_name: URL names for list / form fragment / save / delete.
    """
    extra = row_extra_actions or []

    # GET form fragment for the modal (existing or new instance).
    if request.method == "GET" and request.path.endswith("/form/"):
        instance = get_object_or_404(model, pk=id) if id else None
        form = form_class(instance=instance)
        return render(request, FORM_TEMPLATE, {"form": form, "instance": instance})

    # POST delete.
    if request.method == "POST" and request.path.endswith("/delete/"):
        instance = get_object_or_404(model, pk=id)
        try:
            instance.delete()
        except Exception as e:
            return HttpResponseBadRequest(str(e))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        return redirect(list_url_name)

    # POST create or update.
    if request.method == "POST":
        instance = get_object_or_404(model, pk=id) if id else None
        form = form_class(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            obj = form.save()
            if _wants_json(request):
                return JsonResponse({"ok": True, "id": obj.pk, "label": str(obj)})
            return redirect(list_url_name)
        if _wants_json(request):
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        # Re-render list page with the modal open showing errors.
        ctx = _build_context(
            model, form_class, page_title, columns,
            list_context_name, list_url_name, form_url_name,
            save_url_name, delete_url_name, singular,
            form=form, open_modal=True, edit_id=id or 0,
            row_extra_actions=extra, page_notice=page_notice,
            extra_context=extra_context,
        )
        return render(request, LIST_TEMPLATE, ctx)

    # GET list page.
    ctx = _build_context(
        model, form_class, page_title, columns,
        list_context_name, list_url_name, form_url_name,
        save_url_name, delete_url_name, singular,
        form=form_class(), open_modal=False, edit_id=0,
        row_extra_actions=extra, page_notice=page_notice,
        extra_context=extra_context,
    )
    return render(request, LIST_TEMPLATE, ctx)


def _wants_json(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
        request.headers.get("Accept", "").startswith("application/json")


def _build_context(model, form_class, page_title, columns, list_context_name,
                   list_url_name, form_url_name, save_url_name, delete_url_name,
                   singular,
                   *, form, open_modal, edit_id, row_extra_actions=None, page_notice=None,
                   extra_context=None):
    qs = model.objects.all()
    field_names = {f.name for f in model._meta.get_fields()}
    if "name" in field_names:
        qs = qs.order_by("name")
    elif "lastName" in field_names:
        qs = qs.order_by("lastName", "firstNames")
    return {
        "page_title": page_title,
        "singular": singular or page_title.rstrip("s"),
        "columns": columns,
        "rows": qs,                       # used by _admin_list_page.html
        list_context_name: qs,            # also exposed under model-specific name
        "list_url_name": list_url_name,
        "form_url_name": form_url_name,
        "save_url_name": save_url_name,
        "delete_url_name": delete_url_name,
        "form": form,
        "open_modal": open_modal,
        "edit_id": edit_id,
        "row_extra_actions": row_extra_actions or [],
        "page_notice": page_notice,
        **(extra_context or {}),
    }
