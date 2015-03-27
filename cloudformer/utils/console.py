from __future__ import print_function, unicode_literals

from six.moves import input


def prompt_template_param(template_param):
    """Prompt the user for a template parameter.

    The template parameter name, description, and any default will be
    shown to the user.

    The resulting value will be returned.
    """
    key = template_param.parameter_key
    default_value = template_param.default_value

    print()
    print(template_param.description)

    if default_value:
        prompt = '%s [%s]: ' % (key, default_value)
    else:
        prompt = '%s: ' % key

    prompt = prompt.encode('utf-8')

    while True:
        # We should be checking template_param.no_echo, and using getpass()
        # if it's set, but boto has a bug causing no_echo to always be True.
        # This can be fixed once https://github.com/boto/boto/pull/3052 has
        # landed.
        value = input(prompt)

        if not value and default_value:
            value = default_value

        if value:
            break

    return value
