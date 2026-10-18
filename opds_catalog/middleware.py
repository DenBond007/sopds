import base64
import binascii
from functools import wraps
from django.http import HttpResponse
from django.contrib import auth
from django.utils import translation
from django.middleware.cache import FetchFromCacheMiddleware as DjangoFetchFromCacheMiddleware
from django.utils.deprecation import MiddlewareMixin

from constance import config


class BasicAuthMiddleware(object):
    header = "HTTP_AUTHORIZATION"

    def unauthed(self):
        response = HttpResponse("""<html><title>Auth required</title><body>
                                <h1>Authorization Required</h1></body></html>""", content_type="text/html")
        response['WWW-Authenticate'] = 'Basic realm="OPDS"'
        response.status_code = 401
        return response

    def process_request(self,request):
        if not config.SOPDS_AUTH:
            return
            
        # AuthenticationMiddleware is required so that request.user exists.
        #if not hasattr(request, 'user'):
        #    raise ImproperlyConfigured(
        #        "The Django remote user auth middleware requires the"
        #        " authentication middleware to be installed.  Edit your"
        #        " MIDDLEWARE setting to insert"
        #        " 'django.contrib.auth.middleware.AuthenticationMiddleware'"
        #        " before the BasicAuthMiddleware class.")
        try:
            authentication = request.META[self.header]
        except KeyError:
            return self.unauthed()  
                    
        try:
            (auth_meth, auth_data) = authentication.split(' ',1)
        except ValueError:
            return self.unauthed()
            
        if 'basic' != auth_meth.lower():
            return self.unauthed()
        try:
            auth_data = base64.b64decode(auth_data.strip(), validate=True).decode('utf-8')
            username, password = auth_data.split(':', 1)
        except (binascii.Error, UnicodeError, ValueError):
            return self.unauthed()

        user = auth.authenticate(username=username, password=password)
        if user and user.is_active:
            request.user = user
            # Basic credentials authenticate this request without creating a browser session.
            return request

        return self.unauthed()


def opds_auth_required(view):
    """Check access before file processing or a cached response is reached."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if config.SOPDS_AUTH and not request.user.is_authenticated:
            result = BasicAuthMiddleware().process_request(request)
            if isinstance(result, HttpResponse):
                return result
        return view(request, *args, **kwargs)
    return wrapped


class SOPDSLocaleMiddleware(MiddlewareMixin):

    def process_request(self, request):
            request.LANG = config.SOPDS_LANGUAGE
            translation.activate(request.LANG)
            request.LANGUAGE_CODE = request.LANG

class FetchFromCacheMiddleware(DjangoFetchFromCacheMiddleware):

    def process_request(self, request):
        if not request.user.is_authenticated:
            return None
        else:
            return super(FetchFromCacheMiddleware, self).process_request(request)
