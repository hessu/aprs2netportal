from django import template

register = template.Library()


@register.inclusion_tag('sysop/servertable.html')
def servertable(servers):
    return {'servers': servers}
