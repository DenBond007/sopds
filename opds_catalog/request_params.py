"""Bounded integer parameters shared by web and OPDS endpoints."""

from django.core.exceptions import BadRequest


def integer_parameter(value, *, minimum=0, choices=None, exception=BadRequest):
    text = str(value)
    if len(text) > 10 or not text.isascii() or not text.isdecimal():
        raise exception('Invalid integer parameter')
    number = int(text)
    if not minimum <= number <= 2147483647 or (choices is not None and number not in choices):
        raise exception('Integer parameter out of range')
    return number
