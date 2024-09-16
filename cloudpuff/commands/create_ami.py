"""Command for creating a new AMI."""

from __future__ import annotations

import os
import re
import sys
import textwrap
import time
from datetime import datetime

from cloudpuff.ami import AMICreator
from cloudpuff.cloudformation import CloudFormation
from cloudpuff.commands import BaseCommand, run_command
from cloudpuff.errors import StackCreationError
from cloudpuff.templates import TemplateCompiler
from cloudpuff.templates.errors import TemplateError, TemplateSyntaxError
from cloudpuff.utils.console import prompt_template_param


class CreateAMI(BaseCommand):
    """Launches a CloudFormation stack and creates AMIs from any instances.

    This requires a template containing at least one EC2 instance containing
    a Metadata.CloudPuff.AMINameFormat key. This specifies the name that
    should be used when creating the AMI. It accepts variables, references,
    and special template variables in the form of "{{varname}}". The following
    variables are supported:

    * {yyyy} - The current 4-digit year.
    * {mm} - The current 2 digit month.
    * {dd} - The current 2 digit day of the month.
    * {HH} - The current hour (24-hour time).
    * {MM} - The current minute.
    * {SS} - The current second.

    A Metadata.CloudPuff.PreviousAMI key can also be added to indicate the
    previous AMI ID that was generated by this template. If using the
    --update-amis-file=<filename> parameter, previous AMI IDs in the filename
    will be replaced with the IDs of any new AMIs.
    """

    AMI_NAME_FORMAT_RE = re.compile('{([A-Za-z0-9_]+)}')

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
        parser.add_argument(
            '--update-amis-file',
            metavar='FILENAME',
            help='Specifies a filename containing the former AMI ID to update '
                 'with the new ID.')

    def main(self):
        template_file = self.options.template

        if not os.path.exists(template_file):
            sys.stderr.write('The template file "%s" could not be found.\n'
                             % template_file)
            sys.exit(1)

        compiler = TemplateCompiler(for_amis=True)

        try:
            compiler.load_file(template_file)
        except TemplateSyntaxError as e:
            sys.stderr.write('Template syntax error: %s\n' % e)
            sys.exit(1)
        except TemplateError as e:
            sys.stderr.write('Template error: %s\n' % e)
            sys.exit(1)

        template_body = compiler.to_json()

        if not compiler.ami_outputs:
            sys.stderr.write(textwrap.fill(
                'The template must have at least one EC2 instance with a '
                '"Metadata.CloudPuff.AMINameFormat" value in order to '
                'generate AMIs.'))
            sys.stderr.write('\n')
            sys.exit(1)

        cf = CloudFormation(self.options.region)

        result = cf.validate_template(template_body)
        params = dict(self._get_template_params(result.template_parameters))

        print()
        print('Creating the CloudFormation stack.')
        print('Please wait. This may take several minutes...')

        stack_name = self._generate_stack_name()

        try:
            self.print_stack_events(cf.create_stack_and_wait(
                stack_name=stack_name,
                template_body=template_body,
                params=params,
                rollback_on_error=self.options.rollback,
                tags={
                    'cloudpuff_ami_creation': '1',
                }))
        except StackCreationError as e:
            sys.stderr.write('Error creating the stack: %s\n' % e)
            sys.exit(1)

        stack = cf.lookup_stack(stack_name)

        id_map = self._create_amis(stack, compiler.ami_outputs, compiler.doc)
        cf.delete_stack(stack.stack_id)

        if self.options.update_amis_file and id_map:
            self._update_amis_file(self.options.update_amis_file, id_map)

    def _create_amis(self, stack, ami_outputs, template):
        """Create AMIs based on information in the Stack Outputs.

        Each AMI will be named based on the information in the Outputs.
        The description comes from the template description, and contains
        the date.

        If multiple AMIs are being created, then they will be created in
        parallel.
        """
        outputs = dict(
            (output.key, output.value)
            for output in stack.outputs
        )

        id_map = {}
        now = datetime.now()
        datestamp = now.strftime('%Y-%m-%d')
        ami_creator = AMICreator(self.options.region)

        for ami_info in ami_outputs:
            ami_output_keys = ami_info['outputs']

            # Ensure that the keys we expect to find in Outputs all exist.
            valid_ami_info = True

            for output_id in ami_output_keys.values():
                if output_id not in outputs:
                    sys.stderr.write(textwrap.fill(
                        'Could not create AMI for "%s". Output '
                        'ID "%s" was not found.'
                        % (ami_info['resource_name'], output_id)))
                    sys.stderr.write('\n')
                    valid_ami_info = False

            if not valid_ami_info:
                continue

            instance_id = outputs[ami_output_keys['instance_id_key']]
            name_format = outputs[ami_output_keys['name_format_key']]

            # Build a name and description for the AMI.
            ami_name = self._generate_ami_name(name_format)
            ami_description = '%s [%s]' % (template['Description'], datestamp)

            # Begin creating the AMI.
            print('Creating AMI "%s" for EC2 instance "%s"'
                  % (ami_name, instance_id))

            pending_ami = ami_creator.create_ami(instance_id,
                                                 ami_name,
                                                 ami_description)

            # If the AMi was configured to indicate the previous AMI
            # created from this server, then store that so we can replace
            # it in the template.
            previous_ami_key = ami_output_keys['previous_ami_key']

            if previous_ami_key in outputs:
                previous_ami = outputs[ami_output_keys['previous_ami_key']]
                id_map[previous_ami] = pending_ami.id

        while ami_creator.pending:
            time.sleep(20)

        print()
        print('All AMIs have been created!')

        return id_map

    def _update_amis_file(self, filename, id_map):
        """Update the given template file to replace any older AMI IDs.

        The given template file will have any older AMI IDs (the keys in
        ``id_map``) with any newer AMI IDs.
        """
        with open(filename, 'r') as fp:
            content = fp.read()

        for orig_id, new_id in id_map.items():
            content = content.replace(orig_id, new_id)

        with open(filename, 'w') as fp:
            fp.write(content)

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

        return 'ami-creator-%s-%s' % (norm_filename,
                                      datetime.now().strftime('%Y%m%d%H%M%S'))

    def _generate_ami_name(self, name_format):
        """Generate a name for an AMI.

        This takes an AMI name optionally containing formatting variables,
        and returns a suitable name for the new AMI.
        """
        now = datetime.now()

        name_format_vars = {
            'yyyy': now.strftime('%Y'),
            'mm': now.strftime('%m'),
            'dd': now.strftime('%d'),
            'HH': now.strftime('%H'),
            'MM': now.strftime('%M'),
            'SS': now.strftime('%S'),
        }

        return self.AMI_NAME_FORMAT_RE.sub(
            lambda m: name_format_vars[m.group(1)],
            name_format)

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
    run_command(CreateAMI)
