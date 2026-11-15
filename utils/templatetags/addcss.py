from django import template

register = template.Library()


@register.filter(name='addcss')
def addcss(field, css):
    """Render a bound form field with the given CSS class on its widget."""
    try:
        return field.as_widget(attrs={"class": css})
    except AttributeError:
        return field
