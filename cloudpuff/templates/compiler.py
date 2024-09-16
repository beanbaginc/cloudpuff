from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Any, Optional

from typing_extensions import TypedDict

from cloudpuff.errors import InvalidTagError
from cloudpuff.templates.reader import TemplateReader


class TemplateAMIOutputInfo(TypedDict):
    """Detailed information on an AMI output."""

    #: The key for the previous AMI.
    previous_ami_key: str

    #: The key for the instance ID.
    instance_id_key: str

    #: The key for the AMI's name format.
    name_format_key: str


class TemplateAMIOutput(TypedDict):
    """Information on an AMI output."""

    #: The name of the resource in the template.
    resource_name: str

    #: The outputs from the template.
    outputs: TemplateAMIOutputInfo


class TemplateCompiler(object):
    """Compiles a CloudPuff template to a CloudFormation template.

    The compiled template will be accessible through the ``doc``
    attribute.
    """

    SECTIONS = ('Parameters', 'Mappings', 'Conditions', 'Resources', 'Outputs')

    ######################
    # Instance variables #
    ######################

    #: The AMI outputs created by the compiler.
    ami_outputs: list[TemplateAMIOutput]

    #: The generated template document.
    doc: Optional[OrderedDict[str, Any]]

    def __init__(self, for_amis=False):
        self.doc = None
        self.meta = None
        self.for_amis = for_amis
        self.ami_outputs = []
        self.stack_param_lookups = {}
        self.required_params = {}

    def load_string(self, s, stack_name=None, filename=None):
        """Load a CloudPuff template from a string.

        Args:
            s (unicode):
                The template string to load.

            stack_name (unicode, optional):
                The optional generic name of the stack.

                If not provided, a "Name" must be specified in the "Meta"
                section (which always overrides this value).

            filename (unicode, optional):
                The name of the file being loaded. This is used to generate
                more useful errors.
        """
        reader = TemplateReader()

        if self.for_amis:
            reader.template_state.variables['buildingAMIs'] = 'true'
        else:
            reader.template_state.variables['buildingAMIs'] = 'false'

        reader.load_string(s, filename=filename)

        self.doc = OrderedDict()
        self.doc['AWSTemplateFormatVersion'] = '2010-09-09'

        self.meta = reader.doc['Meta']
        self.meta.setdefault('Name', stack_name)

        if 'Description' in self.meta:
            description = self.meta['Description']

            if 'Version' in self.meta:
                description += ' [v%s]' % self.meta['Version']

            self.doc['Description'] = description

        for section in self.SECTIONS:
            try:
                self.doc[section] = reader.doc[section]
            except KeyError:
                self.doc[section] = OrderedDict()

        # Process any if statements found, converting them to Conditions.
        template_state = reader.template_state

        for section in ('Conditions', 'Resources'):
            self.doc[section] = template_state.process_tree(
                self.doc[section],
                resolve_variables=False,
                resolve_if_conditions=True)

        if template_state.if_conditions:
            self.doc['Conditions'].update(template_state.if_conditions)

        # Look for any parameters that reference outputs from other stacks.
        self._post_process_params()

        # Look for any metadata specific to CloudPuff that we want to
        # process.
        self._scan_cloudpuff_metadata()

        # Clean up any sections not being used.
        for section in self.SECTIONS:
            if not self.doc[section]:
                del self.doc[section]

    def load_file(self, filename):
        """Load a CloudPuff template from disk."""
        generic_stack_name = \
            '.'.join(os.path.basename(filename).split('.')[:-1])
        generic_stack_name = generic_stack_name.replace('_', '-')
        generic_stack_name = generic_stack_name.replace('.', '-')

        with open(filename, 'r') as fp:
            self.load_string(fp.read(),
                             stack_name=generic_stack_name,
                             filename=filename)

    def to_json(self):
        """Return a JSON string version of the compiled template."""
        return json.dumps(self.doc, indent=4)

    def get_tags(self, params):
        """Return a dictionary of tags for the stack.

        This takes a list of parameters going into the stack, so that it
        can resolve references to parameters in the stack values.

        This will also ``GenericStackName`` and ``Version`` tags.

        Args:
            params (list):
                A list of tuples of (key, value) for parameters.

        Returns:
            dict:
            A dictionary of tags for the stack.
        """
        params = dict(params)
        meta = self.meta

        assert meta is not None

        tags = {
            'GenericStackName': self.meta['Name'],
        }

        if 'Version' in meta:
            tags['StackVersion'] = meta['Version']

        for tag_name, tag_value in meta.get('Tags', {}).items():
            if isinstance(tag_value, dict) and 'Ref' in tag_value:
                tag_value = params[tag_value['Ref']]

            if not isinstance(tag_value, str):
                raise InvalidTagError(
                    'Invalid value "%r" for tag "%s" found in the stack '
                    'metadata.'
                    % (tag_value, tag_name))

            tags[tag_name] = tag_value

        return tags

    def _post_process_params(self):
        """Scan the list of parameters for those referencing external stacks.

        Any parameter containing a ``LookupFromStack`` will be specially
        tracked in :py:attr:`stack_param_lookups` so that data from those
        parameters can be scanned from an external stack later.
        """
        doc = self.doc
        assert doc is not None

        for param_name, param in doc['Parameters'].items():
            # Grab the data and delete it from the parameter, so that
            # CloudFormation doesn't get confused by it.
            lookup_from_stack = param.pop('LookupFromStack', None)

            if lookup_from_stack:
                self.stack_param_lookups[param_name] = lookup_from_stack

            self.required_params[param_name] = \
                param.pop('Required', 'true').lower() == 'true'

    def _scan_cloudpuff_metadata(self):
        doc = self.doc
        assert doc is not None

        ami_metadata = []

        for resource_name, resource in doc['Resources'].items():
            if (not isinstance(resource, dict) or
                resource.get('Type') != 'AWS::EC2::Instance' or
                'CloudPuff' not in resource.get('Metadata', {})):
                continue

            metadata = resource['Metadata']['CloudPuff']

            if 'AMINameFormat' in metadata:
                ami_info = {
                    'name_format': metadata['AMINameFormat'],
                    'resource_name': resource_name,
                    'resource': resource,
                }

                if 'PreviousAMI' in metadata:
                    ami_info['previous_ami'] = metadata['PreviousAMI']

                ami_metadata.append(ami_info)

        if ami_metadata and self.for_amis:
            outputs = {}

            # Create individual outputs for each AMI we need to generate.
            for metadata in ami_metadata:
                resource_name = metadata['resource_name']
                previous_ami_key = 'CloudPuff%sPreviousAMI' % resource_name
                instance_id_key = 'CloudPuff%sInstanceID' % resource_name
                name_format_key = 'CloudPuff%sAMINameFormat' % resource_name

                output = OrderedDict()
                output['Description'] = 'Instance ID for %s' % resource_name
                output['Value'] = { 'Ref': resource_name }
                outputs[instance_id_key] = output

                output = OrderedDict()
                output['Description'] = ('Name format for the AMI for %s'
                                         % resource_name)
                output['Value'] = metadata['name_format']
                outputs[name_format_key] = output

                if 'previous_ami' in metadata:
                    output = OrderedDict()
                    output['Description'] = ('Previous AMI ID created for %s'
                                             % resource_name)
                    output['Value'] = metadata['previous_ami']
                    outputs[previous_ami_key] = output

                self.ami_outputs.append({
                    'resource_name': metadata['resource_name'],
                    'outputs': {
                        'previous_ami_key': previous_ami_key,
                        'instance_id_key': instance_id_key,
                        'name_format_key': name_format_key,
                    }
                })

            self.doc.setdefault('Outputs', {}).update(outputs)
