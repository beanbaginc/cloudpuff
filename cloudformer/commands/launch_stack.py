from __future__ import print_function, unicode_literals

import os
import sys
from datetime import datetime

from cloudformer.cloudformation import CloudFormation
from cloudformer.commands import BaseCommand, run_command
from cloudformer.errors import StackCreationError
from cloudformer.templates import TemplateCompiler
from cloudformer.utils.console import prompt_template_param


class LaunchStack(BaseCommand):
    """LaunchStackes a CloudFormation stack."""

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

        compiler = TemplateCompiler(for_amis=True)
        compiler.load_file(template_file)
        template_body = compiler.to_json()

        cf = CloudFormation(self.options.region)
        result = cf.validate_template(template_body)
        params = self._get_template_params(result.template_parameters)

        print()
        print('Creating the CloudFormation stack.')
        print('Please wait. This may take several minutes...')

        try:
            cf.create_stack_and_wait(
                stack_name=self.options.stack_name or
                           self._generate_stack_name(),
                template_body=template_body,
                params=params,
                rollback_on_error=self.options.rollback)
        except StackCreationError as e:
            sys.stderr.write('Error creating the stack: %s\n' % e)
            sys.exit(1)

        print()
        print('The stack has been launched!')

    def _generate_stack_name(self):
        """Generate a name for a new CloudFormation stack.

        The name will be prefixed with "ami-creator-", a normalized version
        of the template filename, and the date/time.
        """
        template_file = self.options.template
        norm_filename = \
            '.'.join(os.path.basename(template_file).split('.')[:-1])
        norm_filename = norm_filename.replace('_', '-')
        norm_filename = norm_filename.replace('.', '-')

        return '%s-%s' % (norm_filename,
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


def main():
    run_command(LaunchStack)
