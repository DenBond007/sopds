# -*- coding: utf-8 -*-

import os
import codecs
import base64
import io
import subprocess
import tempfile
from pathlib import Path

from django.http import HttpResponse, Http404
from django.views.decorators.cache import cache_page

from opds_catalog.models import Book, bookshelf
from opds_catalog import settings, utils, opdsdb, fb2parse
import opds_catalog.zipf as zipfile

from book_tools.format import create_bookfile, mime_detector
from book_tools.format.mimetype import Mimetype

from constance import config
from PIL import Image

from opds_catalog.middleware import BasicAuthMiddleware

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


def getFileData(book):
    full_path = resolve_book_source_path(book)

    z = None
    fz = None
    s = None
    fo = None

    if book.cat_type==opdsdb.CAT_NORMAL:
        file_path = full_path
        try:
            fo=codecs.open(file_path, "rb")
            #s = fo.read()
        except FileNotFoundError:
            #s = None
            fo = None

    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        try:
            fz=codecs.open(full_path, "rb")
            z = zipfile.ZipFile(fz, 'r', allowZip64=True)
            fo= z.open(book.filename)
            #s=fo.read()
        except FileNotFoundError:
            #s = None
            fo = None

    dio = io.BytesIO()
    dio.write(fo.read())
    dio.seek(0)

    if fo: fo.close()
    if z: z.close()
    if fz: fz.close()

    return dio

def getFileDataZip(book):
    transname = getFileName(book)
    fo = getFileData(book)
    dio = io.BytesIO()
    zo = zipfile.ZipFile(dio, 'w', zipfile.ZIP_DEFLATED)
    zo.writestr(transname, fo.read())
    zo.close()
    dio.seek(0)

    return dio

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

    with book_data, tempfile.TemporaryDirectory(
        prefix='sopds-convert-', dir=config.SOPDS_TEMP_DIR
    ) as workdir:
        source_path = os.path.join(workdir, 'source.fb2')
        output_path = os.path.join(workdir, 'output.' + convert_type)
        with open(source_path, 'wb') as source_file:
            source_file.write(book_data.read())
        proc = subprocess.Popen(
            [converter_path, source_path, output_path], stdout=subprocess.PIPE
        )
        proc.communicate()
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
    book = Book.objects.get(id=book_id)

    if config.SOPDS_AUTH:
        if not request.user.is_authenticated:
            bau = BasicAuthMiddleware()
            request = bau.process_request(request)
            if not hasattr(request, 'user'):
                return request
        if request.user.is_authenticated:
            bookshelf.objects.get_or_create(user=request.user, book=book)
            
    full_path = resolve_book_source_path(book)
        
    if config.SOPDS_TITLE_AS_FILENAME:
        transname=utils.translit(book.title+'.'+book.format)
    else:
        transname=utils.translit(book.filename)
        
    transname = utils.to_ascii(transname)
        
    if zip_flag == '1':
        dlfilename=transname+'.zip'   
        content_type= Mimetype.FB2_ZIP if book.format=='fb2' else Mimetype.ZIP
    else:    
        dlfilename=transname
        content_type = mime_detector.fmt(book.format)

    response = HttpResponse()
    response["Content-Type"]='%s; name="%s"'%(content_type,dlfilename)
    response["Content-Disposition"] = 'attachment; filename="%s"'%(dlfilename)
    response["Content-Transfer-Encoding"]='binary'

    z = None
    fz = None
    s = None
    book_size = book.filesize
    if book.cat_type==opdsdb.CAT_NORMAL:
        file_path = full_path
        book_size=os.path.getsize(file_path)
        try:
            fo=codecs.open(file_path, "rb")
        except FileNotFoundError:
            raise Http404
        s=fo.read()
    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        try:
            fz=codecs.open(full_path, "rb")
        except FileNotFoundError:
            raise Http404
        z = zipfile.ZipFile(fz, 'r', allowZip64=True)
        book_size=z.getinfo(book.filename).file_size
        fo= z.open(book.filename)
        s=fo.read()

    if zip_flag=='1':
        dio = io.BytesIO()
        zo = zipfile.ZipFile(dio, 'w', zipfile.ZIP_DEFLATED)
        zo.writestr(transname,s)
        zo.close()
        buf = dio.getvalue()
        response["Content-Length"] = str(len(buf))
        response.write(buf)        
    else:
        response["Content-Length"] = str(book_size)
        response.write(s)

    fo.close()
    if z: z.close()
    if fz: fz.close()

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
    book = Book.objects.get(id=book_id)                                                                                                
                                                                                                                                       
    if book.format.lower() != "fb2":                                                                                                   
        raise Http404                                                                                                                  
                                                                                                                                       
    # FB2-Inhalt laden                                                                                                                 
    fb2_data = get_book_content(book)                                                                                                  
                                                                                                                                       
    fb2_file = None                                                                                                                    
    html_file = None                                                                                                                   
                                                                                                                                       
    try:                                                                                                                               
        # tempor..re FB2-Datei                                                                                                         
        fb2_file = tempfile.NamedTemporaryFile(                                                                                        
            suffix=".fb2",                                                                                                             
            delete=False                                                                                                               
        )                                                                                                                              
        fb2_file.write(fb2_data)                                                                                                       
        fb2_file.close()                                                                                                               
                                                                                                                                       
        # tempor..re HTML-Datei                                                                                                        
        html_file = tempfile.NamedTemporaryFile(                                                                                       
            suffix=".html",                                                                                                            
            delete=False                                                                                                               
        )                                                                                                                              
        html_file.close()
        # Konverter aufrufen                                                                                                           
        subprocess.run(                                                                                                                
            [                                                                                                                          
                "/sopds/convert/fb2_to_html.py",                                                                                       
                fb2_file.name,                                                                                                         
                "-o",                                                                                                                  
                html_file.name                                                                                                         
            ],                                                                                                                         
            check=True                                                                                                                 
        )                                                                                                                              
                                                                                                                                       
        with open(html_file.name, "r", encoding="utf-8") as f:                                                                         
            html = f.read()
        html = html.replace(
            "</head>",
            """
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
            </head>
            """
        )
            
                                                                                                                                       
        return HttpResponse(                                                                                                           
            html,                                                                                                                      
            content_type="text/html; charset=utf-8"                                                                                    
        )                                                                                                                              
                                                                                                                                       
    finally:                                                                                                                           
        if fb2_file and os.path.exists(fb2_file.name):                                                                                 
            os.unlink(fb2_file.name)                                                                                                   
                                                                                                                                       
        if html_file and os.path.exists(html_file.name):                                                                               
            os.unlink(html_file.name)

# Новая версия (0.42) процедуры извлечения обложек из файлов книг fb2, epub, mobi
@cache_page(config.SOPDS_CACHE_TIME)
def Cover(request, book_id, thumbnail=False):
    """ Загрузка обложки """
    book = Book.objects.get(id=book_id)
    response = HttpResponse()
    full_path = resolve_book_source_path(book)

    try:
        if book.cat_type == opdsdb.CAT_NORMAL:
            file_path = full_path
            fo = codecs.open(file_path, "rb")
            book_data = create_bookfile(fo,book.filename)
            image = book_data.extract_cover_memory()
            #fb2.parse(fo, 0)
            fo.close()
        elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
            fz = codecs.open(full_path, "rb")
            z = zipfile.ZipFile(fz, 'r', allowZip64=True)
            fo = z.open(book.filename)
            book_data = create_bookfile(fo, book.filename)
            image = book_data.extract_cover_memory()
            #fb2.parse(fo, 0)
            fo.close()
            z.close()
            fz.close()
    except:
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
            f = open(config.SOPDS_NOCOVER_PATH, "rb")
            response.write(f.read())
            f.close()
        else:
            raise Http404

    return response

# Старая версия (до 0.41) процедуры извлечения обложек из файлов книг только fb2
def Cover0(request, book_id, thumbnail = False):
    """ Загрузка обложки """
    book = Book.objects.get(id=book_id)
    response = HttpResponse()
    c0=0
    full_path = resolve_book_source_path(book)
         
    if book.format=='fb2':        
        fb2=fb2parse.fb2parser(1)
        if book.cat_type==opdsdb.CAT_NORMAL:
            file_path = full_path
            fo=codecs.open(file_path, "rb")
            fb2.parse(fo,0)
            fo.close()
        elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
            fz=codecs.open(full_path, "rb")
            z = zipfile.ZipFile(fz, 'r', allowZip64=True)
            fo = z.open(book.filename)
            fb2.parse(fo,0)
            fo.close()
            z.close()
            fz.close()

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
            f=open(config.SOPDS_NOCOVER_PATH,"rb")
            response.write(f.read())
            f.close()
        else:
            raise Http404

    return response

def Thumbnail(request, book_id):
    return Cover(request, book_id, True)


def ConvertFB2(request, book_id, convert_type):
    """ Выдача файла книги после конвертации в EPUB или mobi """
    book = Book.objects.get(id=book_id)
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
