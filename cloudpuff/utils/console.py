from __future__ import print_function, unicode_literals


def prompt_template_param(template_param, required=True):
    """Prompt the user for a template parameter.

    The template parameter name, description, and any default will be
    shown to the user.

    The resulting value will be returned.

    Args:
        template_param (boto.cloudformation.template.TemplateParameter):
            The template parameter to prompt for.

        required (bool, optional):
            Whether this parameter is required.

    Returns:
        unicode:
        The value chosen for the parameter, or the default value if not
        specified.
    """
    key = template_param['ParameterKey']
    default_value = template_param['DefaultValue']

    print()
    print(template_param['Description'])

    if default_value:
        prompt = f'{key} [{default_value}]: '
    else:
        prompt = f'{key}: '

    while True:
        # We should be checking template_param.no_echo, and using getpass()
        # if it's set, but boto has a bug causing no_echo to always be True.
        # This can be fixed once https://github.com/boto/boto/pull/3052 has
        # landed.
        value = input(prompt)

        if not value and default_value:
            value = default_value

        if value or not required:
            break

    return value
