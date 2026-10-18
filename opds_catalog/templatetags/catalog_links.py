from django import template
from django.http import QueryDict

register = template.Library()


@register.simple_tag(takes_context=True)
def page_query(context, page):
    """Preserve filters and encode values, including ampersands and literal plus signs."""
    request = context.get('request')
    query = request.GET.copy() if request is not None else QueryDict(mutable=True)
    if request is None:
        for key in ('searchtype', 'searchterms'):
            if key in context:
                query[key] = context[key]
    query['page'] = str(page)
    return query.urlencode()
