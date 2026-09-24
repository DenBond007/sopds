# -*- coding: utf-8 -*-

import os
import codecs
import base64
import io
import subprocess
import tempfile
import signal
import sys
import zipfile as standard_zipfile
from contextlib import closing
from pathlib import Path

from django.http import HttpResponse, Http404, FileResponse, StreamingHttpResponse
from django.views.decorators.cache import cache_page
from django.shortcuts import get_object_or_404
from opds_catalog.request_params import integer_parameter

from opds_catalog.models import Book, bookshelf
from opds_catalog import settings, utils, opdsdb, fb2parse
import opds_catalog.zipf as zipfile

from book_tools.format import create_bookfile, mime_detector
from book_tools.format.mimetype import Mimetype

from constance import config
from PIL import Image

from opds_catalog.middleware import BasicAuthMiddleware

CONVERTER_TIMEOUT_SECONDS = 300
DOWNLOAD_CHUNK_SIZE = 64 * 1024
MAX_COVER_SOURCE_BYTES = 32 * 1024 * 1024
HTML_HEAD_STYLE = b'''
            <style>
            body {
                max-width: 900px;
                margin: auto;
                padding: 20px;
                font-size: 1.1rem;
                line-height: 1.6;
                color: #ddd;
                background-color: #1b1b1b;
            }

            a {
                color: #8ecfff;
            }
            </style>
            </head>'''


def _cover_bookfile(reader, filename):
    source = reader.read(MAX_COVER_SOURCE_BYTES + 1)
    if len(source) > MAX_COVER_SOURCE_BYTES:
        raise ValueError('book is too large to parse for a cover')
    return create_bookfile(io.BytesIO(source), filename)


def _write_styled_html(reader, writer):
    needle = b'</head>'
    pending = b''
    while chunk := reader.read(DOWNLOAD_CHUNK_SIZE):
        parts = (pending + chunk).split(needle)
        for part in parts[:-1]:
            writer.write(part)
            writer.write(HTML_HEAD_STYLE)
        pending = parts[-1]
        if len(pending) >= len(needle):
            writer.write(pending[:-(len(needle) - 1)])
            pending = pending[-(len(needle) - 1):]
    writer.write(pending)

def resolve_book_source_path(book):
    try:
        root = Path(config.SOPDS_ROOT_LIB).resolve(strict=True)
        source = Path(root, book.path)
        if book.cat_type == opdsdb.CAT_NORMAL:
            source = source / book.filename
        elif book.cat_type == opdsdb.CAT_INP:
            source = source.parent.parent.parent / source.name
        elif book.cat_type != opdsdb.CAT_ZIP:
            raise Http404
        source = source.resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file():
            raise Http404
    except (OSError, RuntimeError):
        raise Http404
    return str(source)


def getFileName(book):
    if config.SOPDS_TITLE_AS_FILENAME:
        transname = utils.translit(book.title + '.' + book.format)
    else:
        transname = utils.translit(book.filename)

    return utils.to_ascii(transname)


def safe_download_filename(book):
    filename = getFileName(book).replace('\\', '/').rsplit('/', 1)[-1]
    filename = filename.replace('"', '_').replace('\r', '_').replace('\n', '_')
    if filename in ('', '.', '..'):
        filename = 'book-%s.%s' % (book.pk, book.format)
    return filename


def _book_chunks(path, member_name=None):
    with open(path, 'rb') as source:
        if member_name is None:
            reader = source
            while chunk := reader.read(DOWNLOAD_CHUNK_SIZE):
                yield chunk
        else:
            with zipfile.ZipFile(source, 'r', allowZip64=True) as archive:
                with archive.open(member_name) as reader:
                    while chunk := reader.read(DOWNLOAD_CHUNK_SIZE):
                        yield chunk


def _book_size(path, member_name=None):
    if member_name is None:
        return os.path.getsize(path)
    try:
        with open(path, 'rb') as source:
            with zipfile.ZipFile(source, 'r', allowZip64=True) as archive:
                return archive.getinfo(member_name).file_size
    except (FileNotFoundError, KeyError, zipfile.BadZipFile):
        raise Http404


def getFileData(book):
    source_path = resolve_book_source_path(book)
    member_name = None if book.cat_type == opdsdb.CAT_NORMAL else book.filename
    buffer = tempfile.TemporaryFile(mode='w+b')
    try:
        with closing(_book_chunks(source_path, member_name)) as chunks:
            for chunk in chunks:
                buffer.write(chunk)
        buffer.seek(0)
        return buffer
    except (FileNotFoundError, KeyError, zipfile.BadZipFile) as error:
        buffer.close()
        raise Http404 from error
    except BaseException:
        buffer.close()
        raise


def getFileDataZip(book):
    source = getFileData(book)
    try:
        archive_file = tempfile.TemporaryFile(mode='w+b')
    except BaseException:
        source.close()
        raise
    try:
        with source, standard_zipfile.ZipFile(
            archive_file, 'w', compression=standard_zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            entry = standard_zipfile.ZipInfo(safe_download_filename(book))
            entry.compress_type = standard_zipfile.ZIP_DEFLATED
            entry.file_size = os.fstat(source.fileno()).st_size
            with archive.open(entry, 'w') as output:
                while chunk := source.read(DOWNLOAD_CHUNK_SIZE):
                    output.write(chunk)
        archive_file.seek(0)
        return archive_file
    except BaseException:
        source.close()
        archive_file.close()
        raise


def conversion_directory(prefix):
    root = Path(config.SOPDS_TEMP_DIR)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=root)


def getFileDataConv(book, convert_type):
    if book.format != 'fb2':
        return None
    if convert_type == 'epub':
        converter_path = config.SOPDS_FB2TOEPUB
    elif convert_type == 'mobi':
        converter_path = config.SOPDS_FB2TOMOBI
    else:
        return None

    book_data = getFileData(book)
    if not book_data:
        return None

    with book_data, conversion_directory('sopds-convert-') as workdir:
        source_path = os.path.join(workdir, 'source.fb2')
        output_path = os.path.join(workdir, 'output.' + convert_type)
        with open(source_path, 'wb') as source_file:
            source_file.write(book_data.read())
        proc = subprocess.Popen(
            [converter_path, source_path, output_path], stdout=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            proc.communicate(timeout=CONVERTER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            return None
        if proc.returncode != 0:
            return None
        if not os.path.isfile(output_path):
            return None
        with open(output_path, 'rb') as output_file:
            return io.BytesIO(output_file.read())


def getFileDataEpub(book):
    return getFileDataConv(book,'epub')

def getFileDataMobi(book):
    return getFileDataConv(book,'mobi')

def Download(request, book_id, zip_flag):
    """ Загрузка файла книги """
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1, exception=Http404))

    if config.SOPDS_AUTH:
        if not request.user.is_authenticated:
            bau = BasicAuthMiddleware()
            request = bau.process_request(request)
            if not hasattr(request, 'user'):
                return request
        if request.user.is_authenticated:
            bookshelf.objects.get_or_create(user=request.user, book=book)

    source_path = resolve_book_source_path(book)
    member_name = None if book.cat_type == opdsdb.CAT_NORMAL else book.filename
    transname = safe_download_filename(book)
    dlfilename = transname + '.zip' if zip_flag == '1' else transname
    content_type = (
        Mimetype.FB2_ZIP if book.format == 'fb2' else Mimetype.ZIP
    ) if zip_flag == '1' else mime_detector.fmt(book.format)

    if zip_flag == '1':
        wrapped_file = tempfile.TemporaryFile(mode='w+b')
        try:
            with standard_zipfile.ZipFile(
                wrapped_file, 'w', compression=standard_zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as wrapped:
                entry = standard_zipfile.ZipInfo(transname)
                entry.compress_type = standard_zipfile.ZIP_DEFLATED
                entry.file_size = _book_size(source_path, member_name)
                with wrapped.open(entry, 'w') as output:
                    with closing(_book_chunks(source_path, member_name)) as chunks:
                        for chunk in chunks:
                            output.write(chunk)
            wrapped_file.seek(0)
            response = FileResponse(wrapped_file, content_type=content_type)
        except Exception:
            wrapped_file.close()
            raise
    elif member_name is None:
        try:
            source_file = open(source_path, 'rb')
        except FileNotFoundError:
            raise Http404
        response = FileResponse(source_file, content_type=content_type)
    else:
        book_size = _book_size(source_path, member_name)
        response = StreamingHttpResponse(
            _book_chunks(source_path, member_name), content_type=content_type
        )
        response['Content-Length'] = str(book_size)

    response['Content-Type'] = '%s; name="%s"' % (content_type, dlfilename)
    response['Content-Disposition'] = 'attachment; filename="%s"' % dlfilename
    response['Content-Transfer-Encoding'] = 'binary'
    return response


def get_book_content(book):                                                                                                            
    full_path = resolve_book_source_path(book)
                                                                                                                                       
    if book.cat_type == opdsdb.CAT_NORMAL:                                                                                             
        file_path = full_path
                                                                                                                                       
        try:                                                                                                                           
            with open(file_path, "rb") as fo:                                                                                          
                return fo.read()                                                                                                       
        except FileNotFoundError:                                                                                                      
            raise Http404                                                                                                              
                                                                                                                                       
    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:                                                                            
        try:                                                                                                                           
            with open(full_path, "rb") as fz:                                                                                          
                with zipfile.ZipFile(fz, "r", allowZip64=True) as z:                                                                   
                    return z.read(book.filename)                                                                                       
        except FileNotFoundError:                                                                                                      
            raise Http404

def ViewHtml(request, book_id):
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1, exception=Http404))
    if book.format.lower() != "fb2":
        raise Http404
    source_path = resolve_book_source_path(book)
    member_name = None if book.cat_type == opdsdb.CAT_NORMAL else book.filename
    converter_path = Path(__file__).resolve().parent.parent / 'convert' / 'fb2_to_html.py'

    with conversion_directory('sopds-html-') as workdir:
        fb2_path = Path(workdir, 'source.fb2')
        html_path = Path(workdir, 'output.html')
        try:
            with fb2_path.open('wb') as source_file:
                with closing(_book_chunks(source_path, member_name)) as chunks:
                    for chunk in chunks:
                        source_file.write(chunk)
        except (FileNotFoundError, KeyError, zipfile.BadZipFile) as error:
            raise Http404 from error

        proc = subprocess.Popen(
            [sys.executable, str(converter_path), str(fb2_path), '-o', str(html_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            proc.communicate(timeout=CONVERTER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise Http404 from error
        if proc.returncode != 0 or not html_path.is_file():
            raise Http404

        response_file = tempfile.TemporaryFile(mode='w+b')
        try:
            with html_path.open('rb') as output_file:
                _write_styled_html(output_file, response_file)
            response_file.seek(0)
            response = FileResponse(response_file, content_type='text/html; charset=utf-8')
            response['X-Frame-Options'] = 'SAMEORIGIN'
            response['Content-Security-Policy'] = (
                "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                "base-uri 'none'; form-action 'none'"
            )
            return response
        except BaseException:
            response_file.close()
            raise

# Новая версия (0.42) процедуры извлечения обложек из файлов книг fb2, epub, mobi
@cache_page(config.SOPDS_CACHE_TIME)
def Cover(request, book_id=None, thumbnail=False):
    """ Загрузка обложки """
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1, exception=Http404))
    response = HttpResponse()
    full_path = resolve_book_source_path(book)

    try:
        if book.cat_type == opdsdb.CAT_NORMAL:
            with open(full_path, 'rb') as book_file:
                book_data = _cover_bookfile(book_file, book.filename)
                image = book_data.extract_cover_memory()
        elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
            with open(full_path, 'rb') as archive_file:
                with zipfile.ZipFile(archive_file, 'r', allowZip64=True) as archive:
                    with archive.open(book.filename) as book_file:
                        book_data = _cover_bookfile(book_file, book.filename)
                        image = book_data.extract_cover_memory()
    except Exception:
        book_data = None
        image = None

    if image:
        response["Content-Type"] = 'image/jpeg'
        if thumbnail:
            thumb = Image.open(io.BytesIO(image)).convert('RGB')
            thumb.thumbnail((settings.THUMB_SIZE, settings.THUMB_SIZE), Image.LANCZOS)
            tfile = io.BytesIO()
            thumb.save(tfile, 'JPEG')
            image = tfile.getvalue()
        response.write(image)

    if not image:
        if os.path.exists(config.SOPDS_NOCOVER_PATH):
            response["Content-Type"] = 'image/jpeg'
            with open(config.SOPDS_NOCOVER_PATH, 'rb') as fallback:
                response.write(fallback.read())
        else:
            raise Http404

    return response

# Старая версия (до 0.41) процедуры извлечения обложек из файлов книг только fb2
def Cover0(request, book_id, thumbnail = False):
    """ Загрузка обложки """
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1, exception=Http404))
    response = HttpResponse()
    c0=0
    full_path = resolve_book_source_path(book)
         
    if book.format=='fb2':        
        fb2=fb2parse.fb2parser(1)
        if book.cat_type==opdsdb.CAT_NORMAL:
            with open(full_path, 'rb') as book_file:
                fb2.parse(book_file, 0)
        elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
            with open(full_path, 'rb') as archive_file:
                with zipfile.ZipFile(archive_file, 'r', allowZip64=True) as archive:
                    with archive.open(book.filename) as book_file:
                        fb2.parse(book_file, 0)

        if len(fb2.cover_image.cover_data)>0:
            try:
                s=fb2.cover_image.cover_data
                dstr=base64.b64decode(s)
                if thumbnail:
                    response["Content-Type"] = 'image/jpeg'
                    thumb = Image.open(io.BytesIO(dstr)).convert('RGB')
                    thumb.thumbnail((settings.THUMB_SIZE, settings.THUMB_SIZE), Image.ANTIALIAS)
                    tfile = io.BytesIO()
                    thumb.save(tfile, 'JPEG')
                    dstr = tfile.getvalue()
                else:
                    response["Content-Type"] = fb2.cover_image.getattr('content-type')
                response.write(dstr)
                c0=1
            except:
                c0=0

    if c0==0:
        if os.path.exists(config.SOPDS_NOCOVER_PATH):
            response["Content-Type"]='image/jpeg'
            with open(config.SOPDS_NOCOVER_PATH, 'rb') as fallback:
                response.write(fallback.read())
        else:
            raise Http404

    return response

def Thumbnail(request, book_id):
    return Cover(request, book_id, True)


def ConvertFB2(request, book_id, convert_type):
    """ Выдача файла книги после конвертации в EPUB или mobi """
    book = get_object_or_404(Book, pk=integer_parameter(book_id, minimum=1, exception=Http404))
    if book.format != 'fb2':
        raise Http404

    if config.SOPDS_AUTH and request.user.is_authenticated:
        bookshelf.objects.get_or_create(user=request.user, book=book)

    if config.SOPDS_TITLE_AS_FILENAME:
        transname = utils.translit(book.title + '.' + book.format).replace('/', '_')
    else:
        transname = utils.translit(book.filename)
    transname = utils.to_ascii(transname)
    name, _ = os.path.splitext(transname)
    dlfilename = '%s.%s' % (name, convert_type)

    converted = getFileDataConv(book, convert_type)
    if converted is None:
        raise Http404
    with converted:
        data = converted.read()

    response = HttpResponse(data)
    response['Content-Type'] = '%s; name="%s"' % (mime_detector.fmt(convert_type), dlfilename)
    response['Content-Disposition'] = 'attachment; filename="%s"' % dlfilename
    response['Content-Transfer-Encoding'] = 'binary'
    response['Content-Length'] = str(len(data))
    return response
