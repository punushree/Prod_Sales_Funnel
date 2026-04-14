from django import template

register = template.Library()


@register.filter(name="split")
def split_filter(value, delimiter=","):
    """
    Split a string by a delimiter and return a list.
    Usage: {{ some_string|split:"," }}
    """
    if not value:
        return []
    return value.split(delimiter)