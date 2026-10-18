from random import randint

from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import BadRequest
from django.template.context_processors import csrf
from django.db.models import Count, Min, Max
from django.utils.translation import gettext as _
from django.contrib.auth import authenticate, login, logout, REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers
from django.urls import reverse, reverse_lazy
from django.http import JsonResponse, Http404
from django.db.models import Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden

from opds_catalog import models
from opds_catalog.annotations import annotation_text, enqueue_visible_ids
from opds_catalog.book_queries import books_in_series, apply_book_filters, title_search, trailing_duplicate_count
from opds_catalog.opdsdb import CAT_INP
from opds_catalog.prefix_query import prefix_groups
from opds_catalog.request_params import integer_parameter
from opds_catalog.models import ReadingState, AnnotationTask, Book, Author, Series, bookshelf, Counter, Catalog, Genre, lang_menu
from opds_catalog import settings
from constance import config
from opds_catalog.opds_paginator import Paginator as OPDS_Paginator

from sopds_web_backend.settings import HALF_PAGES_LINKS

def sopds_login(function=None, redirect_field_name=REDIRECT_FIELD_NAME, url=None):
    actual_decorator = user_passes_test(
        lambda u: (u.is_authenticated if config.SOPDS_AUTH else True),
        login_url=reverse_lazy(url),
        redirect_field_name=redirect_field_name
    ) 
    if function:
        return actual_decorator(function)
    return actual_decorator

def sopds_processor(request):
    args={}
    auth_enabled = config.SOPDS_AUTH
    args['app_title']=settings.TITLE
    args['sopds_auth']=auth_enabled
    args['sopds_version']=settings.VERSION
    args['alphabet'] = config.SOPDS_ALPHABET_MENU
    args['splititems'] = config.SOPDS_SPLITITEMS
    args['fb2tomobi'] = (config.SOPDS_FB2TOMOBI!="")
    args['fb2toepub'] = (config.SOPDS_FB2TOEPUB!="")
    args['nozip'] = settings.NOZIP_FORMATS
    args['cache_t']=0

    if args['alphabet']:
        args['lang_menu'] = lang_menu
    
    if auth_enabled:
        user=request.user
        if user.is_authenticated:
            result=[]
            for row in bookshelf.objects.filter(user=user).select_related('book').order_by('-readtime', 'pk')[:8]:
                book = row.book
                p = {'id':row.id, 'readtime': row.readtime, 'book_id': row.book_id, 'title': book.title, 'authors':book.authors.values()}
                result.append(p)
            args['bookshelf']=result
        
    books_count = Counter.objects.get_counter(models.counter_allbooks)
    if books_count and (not auth_enabled or request.user.is_authenticated):
        bounds = Book.objects.aggregate(first=Min('pk'), last=Max('pk'))
        if bounds['first'] is not None:
            pivot = randint(bounds['first'], bounds['last'])
            random_book = Book.objects.filter(pk__gte=pivot).order_by('pk').first()
            if random_book is None:
                random_book = Book.objects.order_by('pk').first()
        else:
            random_book = None
    else:
        random_book= None
    if random_book:
        random_book.annotation = annotation_text(random_book.annotation)
    args['random_book'] = random_book
    stats = { d['name']:d['value'] for d in Counter.obj.all().values() }
    stats['lastscan_date']=Counter.objects.get_lastscan()
    args['stats'] = stats
  
    return args


@sopds_login(url='web:login')
def AnnotationStatusView(request):
    raw_ids = request.GET.get('ids', '').split(',')
    if not raw_ids or len(raw_ids) > 60 or any(
            not value.isascii() or not value.isdecimal() for value in raw_ids):
        return HttpResponseBadRequest()
    ids = {integer_parameter(value, minimum=1) for value in raw_ids}
    enqueue_visible_ids(ids)
    result = {}
    for book in Book.objects.filter(pk__in=ids, avail__gt=0).select_related('annotation_task'):
        task = getattr(book, 'annotation_task', None)
        status = task.status if task else AnnotationTask.STATUS_PENDING
        result[str(book.pk)] = {
            'annotation': annotation_text(book.annotation),
            'status': status,
        }
    response = JsonResponse({'books': result})
    response['Cache-Control'] = 'no-store'
    return response

# Create your views here.
@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchBooksView(request):
    #Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        auth_enabled = config.SOPDS_AUTH
        searchtype = request.GET.get('searchtype', 'm')
        if searchtype not in ('m', 'b', 'a', 's', 'g', 'u', 'd', 'i'):
            raise BadRequest('Invalid book search type')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = integer_parameter(request.GET.get('page', '1'))
        page_num = page_num if page_num>0 else 1
        
        #if (len(searchterms)<3) and (searchtype in ('m', 'b', 'e')):
        #    args['errormsg'] = 'Too few symbols in search string !';
        #    return render_to_response('sopds_error.html', args)
        
        if searchtype == 'm':
            #books = Book.objects.extra(where=["upper(title) like %s"], params=["%%%s%%"%searchterms.upper()]).order_by('title','-docdate')
            books = title_search(searchterms)
            args['breadcrumbs'] = [_('Books'),_('Поиск по названию'),searchterms]
            args['searchobject'] = 'title'
            
        if searchtype == 'b':
            #books = Book.objects.extra(where=["upper(title) like %s"], params=["%s%%"%searchterms.upper()]).order_by('title','-docdate')
            books = title_search(searchterms, 'startswith')
            args['breadcrumbs'] = [_('Books'),_('Поиск по названию'),searchterms]   
            args['searchobject'] = 'title'         
            
        elif searchtype == 'a':
            try:
                author_id = int(searchterms)
                author = Author.objects.get(id=author_id)
                #aname = "%s %s"%(author.last_name,author.first_name)
                aname = author.full_name
            except (ValueError, Author.DoesNotExist):
                author_id = 0
                aname = ""                  
            books = Book.objects.filter(authors=author_id).distinct().order_by('search_title', '-docdate', 'pk')
            args['breadcrumbs'] = [_('Books'),_('Поиск по автору'),aname]   
            args['searchobject'] = 'author' 
            
        # Поиск книг по серии
        elif searchtype == 's':
            try:
                ser_id = int(searchterms)
                ser = Series.objects.get(id=ser_id).ser
            except (ValueError, Series.DoesNotExist):
                ser_id = 0
                ser = ""
            #books = Book.objects.filter(series=ser_id).order_by('search_title','-docdate')
            books = books_in_series(ser_id)
            args['breadcrumbs'] = [_('Books'),_('Поиск по серии'),ser]
            args['searchobject'] = 'series'
            
        # Поиск книг по жанру
        elif searchtype == 'g':
            try:
                genre_id = int(searchterms)
                section = Genre.objects.get(id=genre_id).section
                subsection = Genre.objects.get(id=genre_id).subsection
                args['breadcrumbs'] = [_('Books'),_('Поиск по жанру'),section,subsection]
            except (ValueError, Genre.DoesNotExist):
                genre_id = 0
                args['breadcrumbs'] = [_('Books'),_('Поиск по жанру')]
                
            books = Book.objects.filter(genres=genre_id).distinct().order_by('search_title', '-docdate', 'pk')
            args['searchobject'] = 'genre'
                                   
        # Поиск книг на книжной полке            
        elif searchtype == 'u':
            if auth_enabled:
                books = Book.objects.filter(bookshelf__user=request.user).order_by('-bookshelf__readtime', 'pk')
                args['breadcrumbs'] = [_('Books'),_('Bookshelf'),request.user.username]
                #books = bookshelf.objects.filter(user=request.user).select_related('book')              
            else:
                books=Book.objects.filter(id=0)     
                args['breadcrumbs'] = [_('Books'), _('Bookshelf')] 
            args['searchobject'] = 'title'
            args['isbookshelf'] = 1
                
        # Поиск дубликатов для книги            
        elif searchtype == 'd':
            #try:
            book_id = integer_parameter(searchterms, minimum=1, exception=Http404)
            mbook = get_object_or_404(Book, pk=book_id)
            books = Book.objects.filter(title=mbook.title, authors__in=mbook.authors.all()).exclude(id=book_id).distinct().order_by('-docdate', 'pk')
            args['breadcrumbs'] = [_('Books'),_('Doubles for book'),mbook.title]
            args['searchobject'] = 'title'
            
        # Поиск книги по ID. Хотел найти еще и дубликаты к книге, но почему-то не работает запрос правильно. Ума не приложу почему.    
        elif searchtype == 'i':
            book_id = integer_parameter(searchterms, minimum=1, exception=Http404)
            mbook = get_object_or_404(Book, pk=book_id)
            books = Book.objects.filter(id=book_id) 
            args['breadcrumbs'] = [_('Books'), mbook.title]
            #books = Book.objects.filter(title=mbook.title, authors__in=mbook.authors.all()).distinct().order_by('-docdate')                
            #args['breadcrumbs'] = [_('Books'),mbook.title]
            args['searchobject'] = 'title'
        
        books = apply_book_filters(books, request.GET, request.user)
        books_for_neighbors = books
        books = books.prefetch_related('authors', 'genres', 'series', 'bseries_set')

        
        # Фильтруем дубликаты и формируем выдачу затребованной страницы
        books_count = books.count()
        op = OPDS_Paginator(books_count, 0, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)
        items = []
        
        args['reading_choices'] = ReadingState.STATUS_CHOICES
        args['filter_genres'] = Genre.objects.order_by('subsection', 'pk')
        args['filters'] = request.GET
        prev_title = ''
        prev_authors_set = set()
        
        # Начаинам анализ с последнего элемента на предидущей странице, чторбы он "вытянул" с этой страницы
        # свои дубликаты если они есть
        summary_DOUBLES_HIDE =  config.SOPDS_DOUBLES_HIDE and (searchtype != 'd')
        start = op.d1_first_pos if ((op.d1_first_pos==0) or (not summary_DOUBLES_HIDE)) else op.d1_first_pos-1
        finish = op.d1_last_pos
        
        for row in books[start:finish+1]:
            shortpath = row.path[row.path.rfind('/')+1:]
            authors = [{'id': author.id, 'full_name': author.full_name}
                       for author in row.authors.all()]
            genres = [{'id': genre.id, 'subsection': genre.subsection}
                      for genre in row.genres.all()]
            series = [{'id': item.id, 'ser': item.ser}
                      for item in row.series.all()]
            ser_no = [{'ser_no': item.ser_no} for item in row.bseries_set.all()]
            p = {'doubles':0, 'lang_code': row.lang_code, 'filename': row.filename, 'path': row.path, 'shortpath': shortpath, \
                  'registerdate': row.registerdate, 'id': row.id, 'annotation': annotation_text(row.annotation), \
                  'annotation_pending': row.cat_type == CAT_INP and row.avail > 0, \
                  'docdate': row.docdate, 'lang': row.lang, 'format': row.format, 'title': row.title, 'filesize': row.filesize,\
                  'authors': authors, 'genres': genres, 'series': series, 'ser_no': ser_no,\
                  'readtime':row.bookshelf_set.filter(user=request.user).values('readtime') if auth_enabled else None
                 }
            if summary_DOUBLES_HIDE:
                title = p['title']
                authors_set = {a['id'] for a in p['authors']}         
                if title.upper()==prev_title.upper() and authors_set==prev_authors_set:
                    items[-1]['doubles']+=1
                else:
                    items.append(p)                   
                prev_title = title
                prev_authors_set = authors_set
            else:
                items.append(p)
                
        # "вытягиваем" дубликаты книг со следующей страницы и удаляем первый элемент который с предыдущей страницы и "вытягивал" дубликаты с текущей
        if summary_DOUBLES_HIDE:
            if items and finish + 1 < books_count:
                items[-1]['doubles'] += trailing_duplicate_count(
                    books_for_neighbors, finish + 1, prev_title, prev_authors_set)

            if op.d1_first_pos!=0:     
                items.pop(0)                                   
              
        if request.user.is_authenticated:
            states = {state.book_id: state for state in ReadingState.objects.filter(
                user=request.user, book_id__in=[item['id'] for item in items])}
            for item in items:
                state = states.get(item['id'])
                item['reading_status'] = state.status if state else ''
                item['reading_percent'] = round(state.position / 100) if state else 0
        enqueue_visible_ids([item['id'] for item in items])
        args['paginator'] = op.get_data_dict()
        args['searchterms']=searchterms;
        args['searchtype']=searchtype;
        args['books']=items   
        args['current'] = 'search'
        args['cache_id']='%s:%s:%s'%(searchterms,searchtype,op.page_num)
        # changes on bookshelf should be refreshed immediatelly
        if searchtype == 'u':
            args['cache_t'] = 0
        else:
            args['cache_t'] = config.SOPDS_CACHE_TIME
        
    return render(request,'sopds_books.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchSeriesView(request):
    #Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        if searchtype not in ('m', 'b', 'e'):
            raise BadRequest('Invalid series search type')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = integer_parameter(request.GET.get('page', '1'))
        page_num = page_num if page_num>0 else 1
        
        if searchtype == 'm':
            series = Series.objects.filter(search_ser__contains=searchterms.upper())
        elif searchtype == 'b': 
            series = Series.objects.filter(search_ser__startswith=searchterms.upper())
        elif searchtype == 'e':
            series = Series.objects.filter(search_ser=searchterms.upper())      

        #if len(series)>0:
        #    series = series.order_by('ser')   
        series = series.annotate(count_book=Count('book')).distinct().order_by('search_ser', 'pk')
            
        # Создаем результирующее множество
        series_count = series.count()
        op = OPDS_Paginator(series_count, 0, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)        
        items = []
        for row in series[op.d1_first_pos:op.d1_last_pos+1]:
            #p = {'id':row.id, 'ser':row.ser, 'lang_code': row.lang_code, 'book_count': Book.objects.filter(series=row).count()}
            p = {'id':row.id, 'ser':row.ser, 'lang_code': row.lang_code, 'book_count': row.count_book}
            items.append(p)                     
              
        args['paginator'] = op.get_data_dict()
        args['searchterms']=searchterms;
        args['searchtype']=searchtype;
        args['series']=items     
        args['searchobject'] = 'series'
        args['current'] = 'search'        
        args['breadcrumbs'] = [_('Series'),_('Search'),searchterms]
        args['cache_id']='%s:%s:%s'%(searchterms,searchtype,op.page_num)
        args['cache_t']=config.SOPDS_CACHE_TIME

    return render(request,'sopds_series.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchAuthorsView(request):
    #Read searchtype, searchterms, searchterms0, page from form    
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        if searchtype not in ('m', 'b', 'e'):
            raise BadRequest('Invalid author search type')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = integer_parameter(request.GET.get('page', '1'))
        page_num = page_num if page_num>0 else 1
        
        if searchtype == 'm':
            authors = Author.objects.filter(search_full_name__contains=searchterms.upper()).order_by('search_full_name', 'pk')
        elif searchtype == 'b':
            authors = Author.objects.filter(search_full_name__startswith=searchterms.upper()).order_by('search_full_name', 'pk')
        elif searchtype == 'e': 
            authors = Author.objects.filter(search_full_name=searchterms.upper()).order_by('search_full_name', 'pk')
                        
        # Создаем результирующее множество
        authors_count = authors.count()
        authors = authors.annotate(book_count=Count('book'))
        op = OPDS_Paginator(authors_count, 0, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)        
        items = []
        
        for row in authors[op.d1_first_pos:op.d1_last_pos+1]:
            p = {'id':row.id, 'full_name':row.full_name, 'lang_code': row.lang_code, 'book_count': row.book_count}
            items.append(p)                     
            
        args['paginator'] = op.get_data_dict()              
        args['searchterms']=searchterms;
        args['searchtype']=searchtype;
        args['authors']=items     
        args['searchobject'] = 'author'
        args['current'] = 'search'       
        args['breadcrumbs'] = [_('Authors'),_('Search'),searchterms]
        args['cache_id']='%s:%s:%s'%(searchterms,searchtype,op.page_num)
        args['cache_t']=config.SOPDS_CACHE_TIME
                                    
    return render(request,'sopds_authors.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def CatalogsView(request):   
    args = {}
    auth_enabled = config.SOPDS_AUTH

    if request.GET:
        cat_id = request.GET.get('cat', None)
        if cat_id is not None:
            cat_id = integer_parameter(cat_id)
        page_num = integer_parameter(request.GET.get('page', '1'))
    else:
        cat_id = None
        page_num = 1

    try:
        if cat_id is not None:
            cat = Catalog.objects.get(id=cat_id)
        else:
            cat = Catalog.objects.get(parent__id=cat_id)
    except Catalog.DoesNotExist:
        cat = None
    
    catalogs_list = Catalog.objects.filter(parent=cat).order_by('cat_name', 'pk')
    catalogs_count = catalogs_list.count()
    # Related authors are rendered for each book in the current page.
    books_list = Book.objects.filter(catalog=cat).prefetch_related('authors').order_by('search_title', 'pk')
    books_count = books_list.count()
    
    # Получаем результирующий список
    op = OPDS_Paginator(catalogs_count, books_count, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)
    items = []
    
    for row in catalogs_list[op.d1_first_pos:op.d1_last_pos+1]:
        p = {'is_catalog':1, 'title': row.cat_name,'id': row.id, 'cat_type':row.cat_type, 'parent_id':row.parent_id}       
        items.append(p)
          
    for row in books_list[op.d2_first_pos:op.d2_last_pos+1]:
        shortpath = row.path[row.path.rfind('/')+1:]
        p = {'is_catalog':0, 'lang_code': row.lang_code, 'filename': row.filename, 'path': row.path, 'shortpath': shortpath, \
              'registerdate': row.registerdate, 'id': row.id, 'annotation': annotation_text(row.annotation), \
              'docdate': row.docdate, 'lang': row.lang, 'format': row.format, 'title': row.title, 'filesize': row.filesize, \
              'authors':list(row.authors.all()), 'genres':row.genres.values(), 'series':row.series.values(), 'ser_no':row.bseries_set.values('ser_no'),\
              'readtime': row.bookshelf_set.filter(user=request.user).values('readtime') if auth_enabled else None
             }
        items.append(p)
                    
    enqueue_visible_ids([item['id'] for item in items if not item['is_catalog']])
    args['paginator'] = op.get_data_dict()
    args['items']=items
    args['cat_id'] = cat_id
    args['current'] = 'catalog'     
    
    breadcrumbs_list = []
    if cat:
        while (cat.parent):
            breadcrumbs_list.insert(0, (cat.cat_name, cat.id))
            cat = cat.parent
        breadcrumbs_list.insert(0, (_('ROOT'), 0))  
    #breadcrumbs_list.insert(0, (_('Catalogs'),-1))    
    args['breadcrumbs_cat'] =  breadcrumbs_list  
    args['breadcrumbs'] =  [_('Catalogs')]
    args['cache_id'] = '%s:%s:%s' % (args['current'],cat_id, op.page_num)
    args['cache_t'] = config.SOPDS_CACHE_TIME
      
    return render(request,'sopds_catalogs.html', args)  

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def BooksView(request):   
    args = {}

    if request.GET:
        lang_code = integer_parameter(request.GET.get('lang', '0'), choices=lang_menu)
        chars = request.GET.get('chars', '')
    else:
        lang_code = 0
        chars = ''
        
    items = prefix_groups(Book, len(chars) + 1, lang_code, chars)

    args['items']=items
    args['current'] = 'book'      
    args['lang_code'] = lang_code   
    args['breadcrumbs'] =  [_('Books'),_('Select'),lang_menu[lang_code],chars]
    args['cache_id'] = '%s:%s:%s' % (args['current'],lang_code, chars)
    args['cache_t'] = config.SOPDS_CACHE_TIME
      
    return render(request,'sopds_selectbook.html', args)      

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def AuthorsView(request):   
    args = {}

    if request.GET:
        lang_code = integer_parameter(request.GET.get('lang', '0'), choices=lang_menu)
        chars = request.GET.get('chars', '')
    else:
        lang_code = 0
        chars = ''
        
    items = prefix_groups(Author, len(chars) + 1, lang_code, chars)

    args['items']=items
    args['current'] = 'author'      
    args['lang_code'] = lang_code   
    args['breadcrumbs'] =  [_('Authors'),_('Select'),lang_menu[lang_code],chars]
    args['cache_id'] = '%s:%s:%s' % (args['current'],lang_code, chars)
    args['cache_t'] = config.SOPDS_CACHE_TIME
      
    return render(request,'sopds_selectauthor.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SeriesView(request):   
    args = {}

    if request.GET:
        lang_code = integer_parameter(request.GET.get('lang', '0'), choices=lang_menu)
        chars = request.GET.get('chars', '')
    else:
        lang_code = 0
        chars = ''
        
    items = prefix_groups(Series, len(chars) + 1, lang_code, chars)

    args['items']=items
    args['current'] = 'series'      
    args['lang_code'] = lang_code   
    args['breadcrumbs'] =  [_('Series'),_('Select'),lang_menu[lang_code],chars]
    args['cache_id'] = '%s:%s:%s' % (args['current'],lang_code, chars)
    args['cache_t'] = config.SOPDS_CACHE_TIME
      
    return render(request,'sopds_selectseries.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def GenresView(request):   
    args = {}

    if request.GET:
        section_id = integer_parameter(request.GET.get('section', '0'))
    else:
        section_id = 0
        
    if section_id==0:
        items = Genre.objects.values('section').annotate(section_id=Min('id'), num_book=Count('book')).filter(num_book__gt=0).order_by('section')
        args['breadcrumbs'] =  [_('Genres'),_('Select')]
    else:
        section = get_object_or_404(Genre, pk=section_id).section
        items = Genre.objects.filter(section=section).annotate(num_book=Count('book')).filter(num_book__gt=0).values().order_by('subsection', 'pk')
        args['breadcrumbs'] =  [_('Genres'),_('Select'),section]   
          
    args['items']=items
    args['current'] = 'genre'  
    args['parent_id'] = section_id
    args['cache_id'] = '%s:%s' % (args['current'],section_id)
    args['cache_t'] = config.SOPDS_CACHE_TIME
       
    return render(request,'sopds_selectgenres.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@require_POST
@login_required(login_url='web:login')
def BSDelView(request):
    try:
        book = int(request.POST.get('book', ''))
        if not 0 < book <= 2147483647:
            raise ValueError
    except (ValueError, TypeError):
        return HttpResponseBadRequest()
       
    bookshelf.objects.filter(user=request.user, book=book).delete()
    
    return redirect("%s?searchtype=u"%reverse("web:searchbooks"))

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@require_POST
@login_required(login_url='web:login')
def BSClearView(request):
    bookshelf.objects.filter(user=request.user).delete()
    return redirect("%s?searchtype=u" % reverse("web:searchbooks"))
    
def hello(request):
    args = {}
    args['breadcrumbs'] = [_('HOME')]
    return render(request, 'sopds_hello.html', args)

def LoginView(request):
    args = {}
    args['breadcrumbs'] = [_('Login')]
    args.update(csrf(request))
    try:
        username = request.POST['username']
        password = request.POST['password']
    except KeyError:
        return render(request, 'sopds_login.html', args)
    
    next_url = request.GET.get('next',reverse("web:main"))
    if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse('web:main')

    user = authenticate(username=username, password=password)
    if user is not None:
        if user.is_active:
            login(request, user)
            return redirect(next_url)
        else:
            args['system_message']={'text':_('This account is not active!'),'type':'alert'}
            return handler403(request,args)
            #return render(request, 'sopds_login.html', args)
    else:
        args['system_message']={'text':_('User does not exist or the password is incorrect!'),'type':'alert'}
        return handler403(request,args)
        #return render(request, 'sopds_login.html', args)

    return handler403(request,args)
    #return render(request, 'sopds_login.html', args)

@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@require_POST
@login_required(login_url='web:login')
def LogoutView(request):
    logout(request)
    args = {}
    args['breadcrumbs'] = [_('Logout')]
    return redirect(reverse('web:main'))

def handler403(request,args):
    response = render(request, 'sopds_login.html', args)
    response.status_code = 403
    return response
