from __future__ import print_function, unicode_literals

import os
import sys
import textwrap
from datetime import datetime

import six
from colorama import Fore, Style

from cloudformer.cloudformation import CloudFormation
from cloudformer.commands import BaseCommand, run_command
from cloudformer.errors import StackCreationError, StackUpdateError
from cloudformer.templates import TemplateCompiler
from cloudformer.utils.console import prompt_template_param


class LaunchStack(BaseCommand):
    """LaunchStackes a CloudFormation stack."""

    EVENT_ACTION_LABELS = {
        'CREATE_COMPLETE': 'Created',
        'CREATE_FAILED': 'Failed to create',
        'CREATE_IN_PROGRESS': 'Creating',
        'DELETE_COMPLETE': 'Deleted',
        'DELETE_FAILED': 'Failed to delete',
        'DELETE_IN_PROGRESS': 'Deleting',
        'ROLLBACK_COMPLETE': 'Rolled back',
        'ROLLBACK_FAILED': 'Failed to roll back',
        'ROLLBACK_IN_PROGRESS': 'Rolling back',
        'UPDATE_COMPLETE': 'Updated',
        'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS': 'Updated and cleaning up',
        'UPDATE_IN_PROGRESS': 'Updating',
        'UPDATE_ROLLBACK_COMPLETE': 'Rolled back update for',
        'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS':
            'Rolled back and cleaning up',
        'UPDATE_ROLLBACK_FAILED': 'Failed to roll back update for',
        'UPDATE_ROLLBACK_IN_PROGRESS': 'Rolling back update for',
    }

    ICON_ERROR = '\u2717'
    ICON_SUCCESS = '\u2713'
    ICON_PROGRESS = '\u25ba'

    STYLED_ICON_ERROR = Fore.RED + ICON_ERROR + Style.RESET_ALL
    STYLED_ICON_SUCCESS = Fore.GREEN + ICON_SUCCESS + Style.RESET_ALL
    STYLED_ICON_PROGRESS = Fore.YELLOW + ICON_PROGRESS + Style.RESET_ALL

    def add_options(self, parser):
        parser.add_argument(
            '--region',
            default='us-east-1',
            help='The region to connect to.')
        parser.add_argument(
            '--no-rollback',
            action='store_false',
            dest='rollback',
            default=True,
            help='Prevents rollback when there are errors launching a stack.')
        parser.add_argument(
            '-u', '--update',
            action='store_true',
            default=False,
            help='Update an existing stack.')
        parser.add_argument(
            '-k', '--keep-params',
            action='store_true',
            default=False,
            help='Keep all parameters from the old template, if updating. '
                 'Only unknown parameters will be prompted for.')
        parser.add_argument(
            '--stack-name',
            help='The optional name for the stack.')
        parser.add_argument(
            '--param',
            dest='params',
            metavar='KEY=VALUE',
            default=[],
            action='append',
            help='The parameter to pass to the template, as key=value')
        parser.add_argument(
            '--template',
            metavar='FILENAME',
            required=True,
            help='The template file to launch from.')

    def main(self):
        template_file = self.options.template

        if not os.path.exists(template_file):
            sys.stderr.write('The template file "%s" could not be found.\n'
                             % template_file)
            sys.exit(1)

        if self.options.update and not self.options.stack_name:
            sys.stderr.write('The --update option requires --stack-name.\n')
            sys.exit(1)

        compiler = TemplateCompiler(for_amis=True)
        compiler.load_file(template_file)
        template_body = compiler.to_json()

        generic_stack_name = compiler.meta['Name']

        self.cf = CloudFormation(self.options.region)
        result = self.cf.validate_template(template_body)

        print()

        if self.options.update:
            stack_name = self.options.stack_name
            stack = self.cf.lookup_stack(stack_name)

            stack_params = dict([
                (param.key, param.value)
                for param in stack.parameters
            ])

            template_params = result.template_parameters
            new_template_params = []
            template_param_keys = set()

            # Set the defaults for all template parameters based on what's
            # already used in the stack.
            for template_param in template_params:
                key = template_param.parameter_key
                template_param_keys.add(key)

                if key in stack_params:
                    template_param.default_value = stack_params[key]

            if self.options.keep_params:
                # We're going to keep any parameters already set in the stack,
                # so exclude any for now so that we don't prompt for them.
                template_params = [
                    template_param
                    for template_param in template_params
                    if template_param.parameter_key not in stack_params
                ]

            params = self._get_template_params(template_params)

            if self.options.keep_params:
                # Add any existing stack parameters to the list here. Only
                # include those that exist in the current template.
                params += [
                    (param_key, param_value)
                    for param_key, param_value in six.iteritems(stack_params)
                    if param_key in template_param_keys
                ]

            print('Updating the CloudFormation stack.')
            print('Please wait. This may take several minutes...')

            try:
                self._print_events(self.cf.update_stack_and_wait(
                    stack_name=stack_name,
                    template_body=template_body,
                    params=params,
                    tags=compiler.get_tags(params),
                    rollback_on_error=self.options.rollback))
            except StackUpdateError as e:
                print()
                print('%s Updating the stack has failed.'
                      % self.STYLED_ICON_ERROR)
                print()
                print('You can update the template and try again with:')
                print()
                print('%s$%s %s -u -k --stack-name=%s --template %s'
                      % (Fore.CYAN, Style.RESET_ALL, sys.argv[0],
                         stack_name, template_file))
                sys.exit(1)
        else:
            stack_name = (self.options.stack_name or
                          self._generate_stack_name(generic_stack_name))
            params = self._get_template_params(result.template_parameters)

            print('Creating the CloudFormation stack.')
            print('Please wait. This may take several minutes...')

            try:
                self._print_events(self.cf.create_stack_and_wait(
                    stack_name=stack_name,
                    template_body=template_body,
                    params=params,
                    tags=compiler.get_tags(params),
                    rollback_on_error=self.options.rollback))
            except StackCreationError as e:
                print()
                print('%s Creating the stack has failed.'
                      % self.STYLED_ICON_ERROR)
                print()
                print('Delete the stack and try again.')
                sys.exit(1)

        print()
        print('%s The stack has been launched!' % self.STYLED_ICON_SUCCESS)
        print()
        print('%sStack ID:%s %s' %
              (Style.BRIGHT, Style.RESET_ALL, stack_name))

    def _generate_stack_name(self, base_stack_name):
        """Generate a timestamped name for a new CloudFormation stack.

        The name will be consist of the given stack name and a date/time.

        Args:
            base_stack_name (unicode):
                The base name for the stack.

        Returns:
            unicode:
            A stack name in the form of :samp:`{base_stack_name}-{timestamp}`.
        """
        return '%s-%s' % (base_stack_name,
                          datetime.now().strftime('%Y%m%d%H%M%S'))

    def _get_template_params(self, template_parameters):
        """Return values for all needed template parameters.

        Any parameters needed by the template that weren't provided on the
        command line will be requested on the console. Users will get the
        key name, default value, and a description, and will be prompted for
        a suitable value for the template.
        """
        params = dict(
            param.split('=', 1)
            for param in self.options.params
        )

        for template_param in template_parameters:
            key = template_param.parameter_key

            if key not in params:
                params[key] = prompt_template_param(template_param)

        return list(params.items())

    def _print_events(self, events):
        """Print stack events to the console as they come in.

        This will take a generator of stack events and print them out as
        they come in. Each event will continue an icon representing the event
        ("X" for error, checkmark for success, ">" for progress), along
        with information on the event.

        Args:
            events (generator):
                A generator of events.
        """
        for event in events:
            event_status = event.resource_status

            if event_status.endswith('FAILED'):
                icon = self.ICON_ERROR
                status_color = Fore.RED
            elif event_status.endswith('COMPLETE'):
                icon = self.ICON_SUCCESS
                status_color = Fore.GREEN
            elif event_status.endswith('IN_PROGRESS'):
                icon = self.ICON_PROGRESS
                status_color = Fore.YELLOW
            else:
                # This shouldn't happen, as it's not a valid state.
                icon = '?'
                status_color = ''

            # Override the color for rollbacks.
            if 'ROLLBACK_IN_PROGRESS' in event_status:
                status_color = Fore.RED

            action = self.EVENT_ACTION_LABELS.get(event_status, event_status)

            print('%s%s%s %s %s (%s)'
                   % (status_color, icon, Style.RESET_ALL, action,
                      event.logical_resource_id, event.resource_type))

            if event.resource_status_reason:
                print('%s%s%s'
                      % (status_color,
                         textwrap.fill(event.resource_status_reason,
                                       initial_indent='  ',
                                       subsequent_indent='  '),
                         Style.RESET_ALL))


def main():
    run_command(LaunchStack)
