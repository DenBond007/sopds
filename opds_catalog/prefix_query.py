"""Parameterized prefix counts shared by OPDS and web navigation."""

from .models import Author, Book, Series


PREFIX_COLUMNS = {
    Book: 'search_title',
    Author: 'search_full_name',
    Series: 'search_ser',
}


def prefix_groups(model, length, lang_code, chars):
    # Identifiers come only from this fixed mapping and Django model metadata.
    column = PREFIX_COLUMNS[model]
    table = model._meta.db_table
    where = f'{column} LIKE %s'
    params = [length, length]
    if lang_code:
        where = f'lang_code = %s AND {where}'
        params.append(lang_code)
    params.extend((f'{chars}%', length))
    sql = (
        f'SELECT %s AS l, SUBSTRING({column}, 1, %s) AS id, COUNT(*) AS cnt '
        f'FROM {table} WHERE {where} '
        f'GROUP BY SUBSTRING({column}, 1, %s) ORDER BY id'
    )
    return model.objects.raw(sql, params)
