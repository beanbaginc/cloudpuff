from __future__ import unicode_literals

import json
import os
from collections import OrderedDict

import six

from cloudformer.errors import InvalidTagError
from cloudformer.templates.reader import TemplateReader


class TemplateCompiler(object):
    """Compiles a CloudFormer template to a CloudFormation template.

    The compiled template will be accessible through the ``doc``
    attribute.
    """

    SECTIONS = ('Parameters', 'Mappings', 'Conditions', 'Resources', 'Outputs')

    def __init__(self, for_amis=False):
        self.doc = None
        self.meta = None
        self.for_amis = for_amis
        self.ami_outputs = []
        self.stack_param_lookups = {}

    def load_string(self, s, name=None):
        """Load a CloudFormer template from a string.

        Args:
            s (unicode):
                The template string to load.

            name (unicode, optional):
                The optional generic name of the stack.

                If not provided, a "Name" must be specified in the "Meta"
                section (which always overrides this value).
        """
        reader = TemplateReader()
        reader.load_string(s)

        self.doc = OrderedDict()
        self.doc['AWSTemplateFormatVersion'] = '2010-09-09'

        self.meta = reader.doc['Meta']
        name = self.meta.get('Name', name)

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

        # Look for any metadata specific to CloudFormer that we want to
        # process.
        self._scan_cloudformer_metadata()

        # Clean up any sections not being used.
        for section in self.SECTIONS:
            if not self.doc[section]:
                del self.doc[section]

    def load_file(self, filename):
        """Load a CloudFormer template from disk."""
        generic_stack_name = \
            '.'.join(os.path.basename(filename).split('.')[:-1])
        generic_stack_name = generic_stack_name.replace('_', '-')
        generic_stack_name = generic_stack_name.replace('.', '-')

        with open(filename, 'r') as fp:
            self.load_string(fp.read(), name=generic_stack_name)

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

        tags = {
            'GenericStackName': self.meta['Name'],
        }

        if 'Version' in self.meta:
            tags['StackVersion'] = six.text_type(self.meta['Version'])

        for tag_name, tag_value in six.iteritems(self.meta.get('Tags', {})):
            if isinstance(tag_value, dict) and 'Ref' in tag_value:
                tag_value = params[tag_value['Ref']]

            if not isinstance(tag_value, six.text_type):
                raise InvalidTagError(
                    'Invalid value "%r" for tag "%s" found in the stack '
                    'metadata.'
                    % (tag_value, tag_name))

            tags[tag_name] = tag_value

        return tags

    def _scan_cloudformer_metadata(self):
        ami_metadata = []

        for resource_name, resource in six.iteritems(self.doc['Resources']):
            if (not isinstance(resource, dict) or
                resource.get('Type') != 'AWS::EC2::Instance' or
                'CloudFormer' not in resource.get('Metadata', {})):
                continue

            metadata = resource['Metadata']['CloudFormer']

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
                previous_ami_key = 'CloudFormer%sPreviousAMI' % resource_name
                instance_id_key = 'CloudFormer%sInstanceID' % resource_name
                name_format_key = 'CloudFormer%sAMINameFormat' % resource_name

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
