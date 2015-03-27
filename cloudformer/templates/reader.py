from __future__ import unicode_literals

import random
import sys
from collections import OrderedDict

import yaml
from yaml.constructor import ConstructorError

from cloudformer.templates.state import TemplateState
from cloudformer.templates.string_parser import StringParser


class TemplateLoader(yaml.Loader):
    """Loads a YAML document representing a CloudFormation template.

    The templates function much like any standard YAML document, with
    a few additions and changes:

    * The order of keys within a mapping are preserved from the loaded file.

    * All integers and booleans are converted into strings, as needed by
      CloudFormation JSON templates.

    * Multi-line strings are automatically turned into CloudFormation
      Fn::Join'd strings. If the string starts with "__base64__", it will
      be run through Fn::Base64.

    * Any "@@MyRefName" strings found in any values will be expanded
      into { "Ref": "MyRefName" }.

    * Any "<% MyFuncName(...) %>" strings found in any values will be expanded
      into { "Fn::MyFuncName": { ... } }.

    * Any "$$MyVarName" strings found in any values will be expanded into
      strings based on a defined variable.

    * Any "$${MyVarKey.MyVarName}" strings found in any values will be
      looked up as paths within the known variables and expanded into strings.

    * Documents in the form of "--- !vars" may define variables as
      keys/values.

    * Documents in the form of "--- !macros" may define macros that accept
      arguments and return generated content. These can be called by
      "!call-macro". Macro names, like variables, can have nested paths.

    * "!import" statements will import variables and macros found in the
      named file.

    * "!tags" will take a dictionary of tag names to values, and convert it
      into a standard CloudFormation list of tag dictionaries.

    * Keys named "<" work sort of like YAML merge keys, but are compatible
      with the "!call-macro" statement.
    """

    def __init__(self, *args, **kwargs):
        super(TemplateLoader, self).__init__(*args, **kwargs)

        self.template_state = None

    @classmethod
    def register_template_constructors(cls):
        """Register all custom constructors used by the templates."""
        cls.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                            cls.construct_ordered_mapping)

        # Process all strings as templates.
        cls.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SCALAR_TAG,
                            cls.construct_string)

        # Convert integers and booleans to strings.
        cls.add_constructor('tag:yaml.org,2002:int', cls.construct_yaml_str)
        cls.add_constructor('tag:yaml.org,2002:bool', cls.construct_yaml_str)

        # Define some custom functions that will be used.
        cls.add_constructor('!embed-file', cls.construct_embed_file)
        cls.add_constructor('!import', cls.construct_import)
        cls.add_constructor('!call-macro', cls.construct_call_macro)
        cls.add_constructor('!cloud-init', cls.construct_cloud_init)
        cls.add_constructor('!tags', cls.construct_tags)

    def construct_ordered_mapping(self, node):
        """Construct all key/value mappings.

        The order of keys listed in the mapping will be preserved,
        guaranteeing they're later retrieved in the same order in which
        they were specified.

        Any key found that's named "<" will be replaced by the value of
        the key. This allows compatibility with macros.
        """
        self.flatten_mapping(node)

        d = OrderedDict()
        pairs = self.construct_pairs(node)

        for key, value in pairs:
            if isinstance(key, basestring) and key == '<':
                d.update(value)
            else:
                d[key] = value

        return d

    def construct_string(self, node):
        """Convert strings into their best representation in CloudFormation.

        If the string consists of multiple lines, it will be turned into
        a CloudFormation Fn::Join statement, producing a more readable
        template.

        If the first line of a multi-line string contains "__base64__", then
        the result will be run through Fn::Base64 in the CloudFormation
        template.

        Any references/functions/variables found within the string will
        be converted into their appropriate statements.
        """
        parser = StringParser(self.template_state)

        return parser.parse_string(node.value)

    def construct_embed_file(self, node):
        """Handle !embed-file statements.

        This takes a filename and serializes it to a string. The file is
        treated as a plain text blob, and will not be processed for
        variables or references.
        """
        values = self.construct_mapping(node)

        try:
            filename = values['filename']
        except KeyError:
            raise ConstructorError('Missing filename in !embed-file')

        is_base64 = values.get('base64', False)

        self.template_state.embedded_files.add(filename)

        try:
            with open(filename, 'r') as fp:
                result = {
                    'Fn::Join': ['', fp.readlines()],
                }

                if is_base64:
                    result = {
                        'Fn::Base64': result
                    }

                return result
        except IOError as e:
            raise ConstructorError('Unable to read file "%s" for embedding'
                                   % filename)

    def construct_import(self, node):
        """Handle !import statements.

        The filename referenced will be read and parsed. Any macros and
        variables found will be copied to this template.
        """
        filenames = self.construct_scalar(node).split()
        self.template_state.imported_files.update(filenames)

        for filename in filenames:
            reader = TemplateReader()
            reader.load_file(filename)

            self.template_state.update(reader.template_state)

    def construct_call_macro(self, node):
        """Handle !call-macro statements.

        The given macro, if found, will be processed. The location of the
        !call-macro will then be replaced by the processed content.
        """
        values = self.construct_mapping(node)
        name = values.pop('macro')

        try:
            macro = self.template_state.resolve(name,
                                                self.template_state.macros)
            content = macro['content']
        except KeyError:
            raise ConstructorError('"%s" is not a valid macro' % name)

        variables = self.template_state.variables.copy()
        variables.update(macro.get('defaultParams', {}))
        variables.update(values)

        return self._process_macro(content, name, variables)

    def construct_cloud_init(self, node):
        """Handle !cloud-init statements.

        This constructs a base64-encoded mime message that can upload new
        cloud-init configuration and a setup script.

        When specifying multiple items for UserData like this for cloud-init,
        a mime message must be formatted and embedded in a CloudFormation
        template. This takes the hard work out of that.

        The cloud-init configuration is defined as a string in the
        'config' key.

        The setup script is defined as a string in the 'setup' key.
        """
        children = self.construct_mapping(node)
        mime_items = []

        if 'config' in children:
            content = children['config']

            mime_items.append({
                'contentType': 'text/cloud-config',
                'filename': 'cloud.cfg',
                'content': content,
            })

        if 'script' in children:
            mime_items.append({
                'contentType': 'text/x-shellscript',
                'filename': 'script.sh',
                'content': children['script'],
            })

        if len(mime_items) > 1:
            fmt = '%%0%dd' % len(repr(sys.maxsize - 1))
            token = random.randrange(sys.maxsize)
            boundary = '%s%s==' % ('=' * 15, fmt % token)

            result = [
                'Content-Type: multipart/mixed; boundary="%s"\n' % boundary,
                'MIME-Version: 1.0\n',
                '\n'
            ]

            for item in mime_items:
                content_type = item['contentType']
                filename = item['filename']
                content = item['content']

                if isinstance(content, dict) and 'Fn::Join' in content:
                    content = content['Fn::Join'][1]

                result += [
                    '--%s\n' % boundary,
                    'Content-Type: %s\n' % content_type,
                    'MIME-Version: 1.0\n',
                    'Content-Disposition: attachment; filename="%s"\n' % filename,
                    '\n',
                ]

                result += content

            result.append('--%s--\n' % boundary)

            result = {
                'Fn::Join': ['', result],
            }
        else:
            assert mime_items

            result = mime_items[0]['content']

        return {
            'Fn::Base64': result,
        }

    def construct_tags(self, node):
        """Handle !tags statements.

        The keys/values in the mapping passed to !tags will be turned into
        CloudFormation's lists of dictionaries of "Key" and "Value" keys.
        """
        values = self.construct_mapping(node)
        tags = []

        for key, value in values.iteritems():
            if not isinstance(value, dict):
                value = unicode(value)

            tag = OrderedDict()
            tag['Key'] = key
            tag['Value'] = value
            tags.append(tag)

        return tags

    def _process_macro(self, macro_value, macro_name, variables):
        """Process a macro.

        This will run through the macro and convert any referenced
        variables based on the default parameters and any variables
        passed to the template.
        """
        try:
            return self.template_state.process_tree(macro_value, variables)
        except KeyError as e:
            raise ConstructorError(unicode(e))


class TemplateReader(object):
    """Reads a template file.

    The template file is processed and read. The resulting ``doc`` attribute
    returns the processed document.

    The template file may contain zero or more "!macros" YAML documents,
    zero or more "!vars" documents, and should contain one standard
    template document.
    """

    def __init__(self):
        self.doc = OrderedDict()
        self.template_state = TemplateState()

    def load_string(self, s):
        """Load a template file from a string."""
        reader = self

        class ReaderTemplateLoader(TemplateLoader):
            """A TemplateLoader that interfaces with this TemplateReader.

            The macros and variables in the loader will be shared with
            the reader, allowing all documents in the loader to share the
            same macros and variables.
            """
            def __init__(self, *args, **kwargs):
                super(ReaderTemplateLoader, self).__init__(*args, **kwargs)

                # Share the state across all instances within this reader.
                self.template_state = reader.template_state

        for doc in yaml.load_all(s, Loader=ReaderTemplateLoader):
            if isinstance(doc, MacrosDoc):
                self.template_state.macros.update(doc.__dict__)
            elif isinstance(doc, VariablesDoc):
                doc_tree = self._resolve_variables(reader, doc)
                self.template_state.variables.update(doc_tree)
            else:
                self.doc.update(doc)

    def load_file(self, filename):
        """Load a template file from disk."""
        with open(filename, 'r') as fp:
            self.load_string(fp.read())

    def _resolve_variables(self, reader, doc):
        prev_unresolved_vars = None

        doc_tree = doc.__dict__

        while prev_unresolved_vars != self.template_state.unresolved_variables:
            doc_tree = self.template_state.process_tree(doc_tree, doc_tree)
            prev_unresolved_vars = \
                self.template_state.unresolved_variables.copy()

        return doc_tree


class MacrosDoc(yaml.YAMLObject):
    """A document consisting of macro definitions."""

    yaml_tag = '!macros'
    yaml_loader = TemplateLoader


class VariablesDoc(yaml.YAMLObject):
    """A document consisting of variable definitions."""

    yaml_tag = '!vars'
    yaml_loader = TemplateLoader


TemplateLoader.register_template_constructors()
