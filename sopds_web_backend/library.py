"""Personal reading data is independent of the legacy download bookshelf."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache

from opds_catalog.models import Book, ReadingState
from opds_catalog.request_params import integer_parameter
from opds_catalog.dl import ViewHtml


class ReadingForm(forms.Form):
    status = forms.ChoiceField(choices=ReadingState.STATUS_CHOICES, required=False)
    position = forms.IntegerField(min_value=0, max_value=10000, required=False)
    action = forms.ChoiceField(choices=[('save', 'save'), ('remove', 'remove')], required=False)


@never_cache
@login_required(login_url='web:login')
@require_http_methods(['GET', 'POST'])
def reading_state(request, book_id):
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1))
    if request.method == 'GET':
        state = ReadingState.objects.filter(user=request.user, book=book).first()
        return JsonResponse({'status': state.status if state else '',
                             'position': state.position if state else 0})
    form = ReadingForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'errors': form.errors}, status=400)
    data = form.cleaned_data
    if data['action'] == 'remove':
        ReadingState.objects.filter(user=request.user, book=book).delete()
    else:
        updates = {}
        if data['status']:
            updates['status'] = data['status']
        if data['position'] is not None:
            updates['position'] = data['position']
        if not updates:
            return JsonResponse({'error': 'Provide a status or position'}, status=400)
        if updates.get('status') == 'read' and 'position' not in updates:
            updates['position'] = 10000
        if 'position' in updates and 'status' not in updates:
            updates['status'] = 'read' if updates['position'] == 10000 else 'reading'
        ReadingState.objects.update_or_create(user=request.user, book=book, defaults=updates)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'saved': True})
    target = request.POST.get('next', reverse('web:searchbooks') + '?searchtype=m')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = reverse('web:main')
    return redirect(target)


@never_cache
@login_required(login_url='web:login')
def reader(request, book_id):
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1), format__iexact='fb2')
    return render(request, 'sopds_reader.html', {'book': book, 'breadcrumbs': ['Чтение', book.title]})


@never_cache
@login_required(login_url='web:login')
def reader_document(request, book_id):
    response = ViewHtml(request, book_id)
    # Same-origin access lets the trusted parent measure scrolling. Book scripts,
    # forms, popups, network requests and navigation stay disabled by CSP/sandbox.
    response['Content-Security-Policy'] = (
        "sandbox allow-same-origin; default-src 'none'; img-src data:; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
    return response


@never_cache
@login_required(login_url='web:login')
def operations(request):
    from django.http import HttpResponseForbidden
    from opds_catalog.health import health_report
    if not request.user.is_staff:
        return HttpResponseForbidden()
    report = health_report()
    return render(request, 'sopds_operations.html', {'report': report, 'breadcrumbs': ['Состояние библиотеки']})
