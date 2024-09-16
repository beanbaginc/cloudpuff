"""Command for launching a stack."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Optional, Sequence, TYPE_CHECKING

from colorama import Fore, Style

from cloudpuff.cloudformation import CloudFormation
from cloudpuff.commands import BaseCommand, run_command
from cloudpuff.errors import (StackCreationError, StackUpdateError,
                              StackUpdateNotRequired)
from cloudpuff.templates import TemplateCompiler
from cloudpuff.templates.errors import TemplateError, TemplateSyntaxError
from cloudpuff.utils.console import prompt_template_param

if TYPE_CHECKING:
    import argparse


class LaunchStack(BaseCommand):
    """LaunchStackes a CloudFormation stack."""

    def add_options(
        self,
        parser: argparse.ArgumentParser,
    ) -> None:
        """Add options for the command.

        Args:
            parser (argparse.ArgumentParser):
                The argument parser to add options to.
        """
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

    def main(self) -> None:
        template_file: str = self.options.template

        if not os.path.exists(template_file):
            sys.stderr.write('The template file "%s" could not be found.\n'
                             % template_file)
            sys.exit(1)

        if self.options.update and not self.options.stack_name:
            sys.stderr.write('The --update option requires --stack-name.\n')
            sys.exit(1)

        compiler = TemplateCompiler()

        try:
            compiler.load_file(template_file)
        except TemplateSyntaxError as e:
            sys.stderr.write('Template syntax error: %s\n' % e)
            sys.exit(1)
        except TemplateError as e:
            sys.stderr.write('Template error: %s\n' % e)
            sys.exit(1)

        assert compiler.meta is not None

        template_body = compiler.to_json()

        generic_stack_name = compiler.meta['Name']

        self.cf = CloudFormation(region=self.options.region)
        result = self.cf.validate_template(template_body)
        template_params = result.template_parameters

        if self.options.update:
            keep_params: bool = self.options.keep_params
            stack_name: str = self.options.stack_name
            stack = self.cf.lookup_stack(stack_name)

            stack_params = {
                param.key: param.value
                for param in stack.parameters
            }

            template_param_keys: set[str] = set()

            # Set the defaults for all template parameters based on what's
            # already used in the stack.
            for template_param in template_params:
                key = template_param.parameter_key
                template_param_keys.add(key)

                if key in stack_params:
                    template_param.default_value = stack_params[key]

            if keep_params:
                # We're going to keep any parameters already set in the stack,
                # so exclude any for now so that we don't prompt for them.
                template_params = [
                    template_param
                    for template_param in template_params
                    if template_param.parameter_key not in stack_params
                ]

            params = self._get_template_params(
                template_params,
                ignore_params=list(compiler.stack_param_lookups.keys()))

            if keep_params:
                # Add any existing stack parameters to the list here. Only
                # include those that exist in the current template.
                params.update({
                    param_key: param_value
                    for param_key, param_value in stack_params.items()
                    if param_key in template_param_keys
                })

            params = self._lookup_stack_params(params, compiler)

            print('Updating the CloudFormation stack.')
            print('Please wait. This may take several minutes...')

            try:
                self.print_stack_events(self.cf.update_stack_and_wait(
                    stack_name=stack_name,
                    template_body=template_body,
                    params=params,
                    tags=compiler.get_tags(params),
                    rollback_on_error=self.options.rollback))
            except StackUpdateNotRequired as e:
                print()
                self.print_success('The stack is already up-to-date!')
                return
            except StackUpdateError as e:
                sys.stderr.write('\n')
                self.print_error('Updating the stack has failed.')
                sys.stderr.write('\n')
                sys.stderr.write('You can update the template and try again '
                                 'with:\n')
                sys.stderr.write('\n')
                sys.stderr.write('%s$%s %s -u -k --stack-name=%s --template '
                                 '%s\n'
                                 % (Fore.CYAN, Style.RESET_ALL, sys.argv[0],
                                    stack_name, template_file))
                sys.exit(1)
        else:
            stack_name = (self.options.stack_name or
                          self._generate_stack_name(generic_stack_name))
            params = self._get_template_params(
                template_params,
                ignore_params=list(compiler.stack_param_lookups.keys()),
                required_params=compiler.required_params)
            params = self._lookup_stack_params(params, compiler)

            print('Creating the CloudFormation stack.')
            print('Please wait. This may take several minutes...')

            try:
                self.print_stack_events(self.cf.create_stack_and_wait(
                    stack_name=stack_name,
                    template_body=template_body,
                    params=params,
                    tags=compiler.get_tags(params),
                    rollback_on_error=self.options.rollback))
            except StackCreationError as e:
                sys.stderr.write('\n')
                self.print_error('Creating the stack has failed.')
                sys.stderr.write('\n')
                sys.stderr.write('Delete the stack and try again.\n')
                sys.exit(1)

        print()
        self.print_success('The stack has been launched!')
        print()
        print('%sStack ID:%s %s' %
              (Style.BRIGHT, Style.RESET_ALL, stack_name))

    def _generate_stack_name(
        self,
        base_stack_name: str,
    ) -> str:
        """Generate a timestamped name for a new CloudFormation stack.

        The name will be consist of the given stack name and a date/time.

        Args:
            base_stack_name (str):
                The base name for the stack.

        Returns:
            str:
            A stack name in the form of :samp:`{base_stack_name}-{timestamp}`.
        """
        return '%s-%s' % (base_stack_name,
                          datetime.now().strftime('%Y%m%d%H%M%S'))

    def _get_template_params(
        self,
        template_parameters: Sequence[object],
        ignore_params: list[str] = [],
        required_params: Optional[dict[str, bool]] = None,
    ) -> dict[str, str]:
        """Return values for all needed template parameters.

        Any parameters needed by the template that weren't provided on the
        command line will be requested on the console. Users will get the
        key name, default value, and a description, and will be prompted for
        a suitable value for the template.

        Args:
            template_parameters (list):
                A list of
                :py:class:`~boto.cloudformation.template.TemplateParameter`,
                each representing a parameter from the validator.

            ignore_params (list, optional):
                A list of parameter names to ignore.

            required_params (dict):
                A dictionary of parameter requirements. Each key is a
                parameter name and each value is a boolean indicating if it's
                required.

        Returns:
            dict:
            The resulting parameters.
        """
        params = dict(
            param.split('=', 1)
            for param in self.options.params
        )

        for template_param in template_parameters:
            param_name = template_param.parameter_key

            if param_name not in params and param_name not in ignore_params:
                params[param_name] = prompt_template_param(
                    template_param,
                    required=required_params[param_name])

        return params

    def _lookup_stack_params(
        self,
        params: dict[str, str],
        compiler: TemplateCompiler,
    ) -> dict[str, str]:
        """Look up parameter values from referenced stacks.

        Args:
            params (dict):
                Parameters already provided for the template.

            compiler (cloudpuff.compiler.TemplateCompiler):
                The compiler used to compile this template.

        Returns:
            dict:
            The resulting parameters.
        """
        stack_param_lookups = compiler.stack_param_lookups
        stack_outputs: dict[tuple[tuple[str, str], ...], dict[str, str]] = {}

        # Go through all external stack parameter lookups requested by this
        # template, and try to find the appropriate stacks.
        for param_name, lookup_info in stack_param_lookups.items():
            stack_name = lookup_info['StackName']
            required_tags: dict[str, str] = {
                'GenericStackName': stack_name,
            }

            for tag_name in lookup_info['MatchStackTags']:
                required_tags[tag_name] = params[tag_name]

            key = tuple(required_tags.items())

            if key not in stack_outputs:
                # We don't have anything for this stack yet, so try to find
                # the stack and cache it.
                stacks = self.cf.lookup_stacks(
                    statuses=('CREATE_COMPLETE', 'UPDATE_COMPLETE',
                              'UPDATE_ROLLBACK_COMPLETE'),
                    tags=required_tags)

                if len(stacks) == 0:
                    self.print_error(
                        'Could not find a stack "%s", as needed by the '
                        'stack parameter "%s".'
                        % (stack_name, param_name))
                    sys.exit(1)
                elif len(stacks) != 1:
                    # TODO: Allow the user to select a stack.
                    self.print_error(
                        'There were too many stacks returned named "%s" '
                        'matching the criteria for stack parameter "%s".'
                        % (stack_name, param_name))
                    sys.exit(1)

                stack_outputs[key] = {
                    output.key: output.value
                    for output in stacks[0].outputs
                }

            outputs = stack_outputs[key]
            output_name = lookup_info['OutputName']

            for output in stacks[0].outputs:
                if output.key == output_name:
                    params[param_name] = output.value
                    break

            if param_name not in params:
                self.print_error(
                    'Could not find the output "%s" in the stack "%s", as '
                    'needed by the stack parameter "%s".'
                    % (output_name, stack_name, param_name))
                sys.exit(1)

        return params


def main():
    run_command(LaunchStack)
