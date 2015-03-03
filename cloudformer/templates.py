import re
import sys
from collections import OrderedDict
from itertools import chain
from io import StringIO

import yaml
from yaml.constructor import ConstructorError


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

    * Any "!!MyFuncName(...)" strings found in any values will be expanded
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

    PARSE_STR_RE = re.compile(
        r'('
        r'!!(?P<func>[A-Za-z]+)\((?P<params>(((@@)?[A-Za-z0-9:_]+)(,\s*)?)+)\)'
        r'|@@(?P<ref_name>[A-Za-z0-9:_]+)'
        r'|\$\$((?P<var_name>[A-Za-z0-9_]+)|{(?P<var_path>[A-Za-z0-9_.]+)})'
        r')')
    FUNC_PARAM_RE = re.compile(',\s*')

    def __init__(self, *args, **kwargs):
        super(TemplateLoader, self).__init__(*args, **kwargs)

        self.macros = {}
        self.variables = {}

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
        cls.add_constructor('!import', cls.construct_import)
        cls.add_constructor('!call-macro', cls.construct_call_macro)
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
        lines = node.value.splitlines(True)

        if lines[0].strip() == '__base64__':
            process_func = 'Fn::Base64'
            lines = lines[1:]
        else:
            process_func = None

        norm_lines = list(chain.from_iterable([
            self._parse_string(line, always_return_list=True)
            for line in lines
        ]))

        if len(norm_lines) == 1:
            result = norm_lines[0]
        else:
            result = {
                'Fn::Join': [
                    '',
                    norm_lines
                ]
            }

        if process_func:
            result = {
                process_func: result,
            }

        return result

    def construct_import(self, node):
        """Handle !import statements.

        The filename referenced will be read and parsed. Any macros and
        variables found will be copied to this template.
        """
        filenames = self.construct_scalar(node).split()

        for filename in filenames:
            reader = TemplateReader()
            reader.load_file(filename)

            self.macros.update(reader.macros)
            self.variables.update(reader.variables)

    def construct_call_macro(self, node):
        """Handle !call-macro statements.

        The given macro, if found, will be processed. The location of the
        !call-macro will then be replaced by the processed content.
        """
        values = self.construct_mapping(node)
        name = values.pop('macro')

        try:
            macro = self._resolve_var(name, self.macros)
            content = macro['content']
        except KeyError:
            raise ConstructorError('"%s" is not a valid macro' % name)

        variables = self.variables.copy()
        variables.update(macro.get('defaultParams', {}))
        variables.update(values)

        return self._process_macro(content, name, variables)

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
        if isinstance(macro_value, dict):
            return OrderedDict(
                (self._process_macro(key, macro_name, variables),
                 self._process_macro(value, macro_name, variables))
                for key, value in macro_value.iteritems()
            )
        elif isinstance(macro_value, list):
            return [
                self._process_macro(item, macro_name, variables)
                for item in self._collapse_vars(macro_value, variables)
            ]
        elif isinstance(macro_value, VarReference):
            try:
                return self._resolve_var(macro_value.name, variables)
            except KeyError:
                raise ConstructorError(
                    'Unknown variable "%s"'
                    % macro_value.name)
        else:
            return macro_value

    def _collapse_vars(self, items, variables):
        """Collapse a list of strings or variable references.

        All string items will remain their own items in the list. Any
        variable reference items that have a matching variable in the
        template will be folded into the adjacent strings.
        """
        collapse_string = False
        was_string = False
        result = []

        for item in items:
            collapse_next_string = False

            if isinstance(item, VarReference):
                try:
                    item = self._resolve_var(item.name, variables)
                except KeyError:
                    # We'll keep it as a VarReference.
                    pass
                else:
                    collapse_string = True
                    collapse_next_string = True
                    was_string = True

            if isinstance(item, basestring):
                if collapse_string and result:
                    result[-1] += item
                else:
                    result.append(item)

                collapse_string = collapse_next_string
                was_string = True
            else:
                result.append(item)
                collapse_string = False
                was_string = False

        return result

    def _resolve_var(self, var_name, variables):
        """Resolve a variable name or path.

        If the variable contains one or more dots in the name, it will
        be looked up as a path within the variables dictionary.
        """
        var_parts = var_name.split('.')
        result = variables

        for part in var_parts:
            result = result[part]

        return result

    def _parse_string(self, s, always_return_list=False):
        """Parse a string for any references, functions, or variables.

        Any substrings starting with "@@" will be turned into a
        { "Ref": "<name>" } mapping.

        Any substrings starting with "!!" will be turned into a
        { "Fn::<name>": { ... } } mapping.

        Any substrings starting with "$$" will be resolved into a variable's
        content, if the variable exists, or a VarReference if not.

        If the result is a single string, it will be returned as a string.
        Otherwise, it will be returned as a list of
        strings/dictionaries/VarReferences.
        """
        prev = 0
        parts = []

        for m in self.PARSE_STR_RE.finditer(s):
            start = m.start()
            groups = m.groupdict()

            if start > 0:
                parts.append(s[prev:start])

            if groups['func']:
                func_name = 'Fn::%s' % groups['func']
                parts.append({
                    func_name: [
                        self._parse_string(value)
                        for value in self.FUNC_PARAM_RE.split(groups['params'])
                    ]
                })
            elif groups['ref_name']:
                parts.append({
                    'Ref': groups['ref_name']
                })
            elif groups['var_name']:
                parts.append(VarReference(groups['var_name']))
            elif groups['var_path']:
                parts.append(VarReference(groups['var_path']))

            prev = m.end()

        if prev != len(s):
            parts.append(s[prev:])

        if always_return_list or len(parts) > 1:
            return self._collapse_vars(parts, self.variables)
        else:
            return parts[0]

TemplateLoader.register_template_constructors()


class MacrosDoc(yaml.YAMLObject):
    """A document consisting of macro definitions."""

    yaml_tag = '!macros'
    yaml_loader = TemplateLoader


class VariablesDoc(yaml.YAMLObject):
    """A document consisting of variable definitions."""

    yaml_tag = '!vars'
    yaml_loader = TemplateLoader


class VarReference(object):
    """A reference to a variable.

    These are used as placeholders for variables that are referenced.
    They are later resolved into the variable contents.
    """

    def __init__(self, name):
        self.name = name


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
        self.macros = {}
        self.variables = {}

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

                # Share these variables across all instances within this
                # reader.
                self.macros = reader.macros
                self.variables = reader.variables

        for doc in yaml.load_all(s, Loader=ReaderTemplateLoader):
            if isinstance(doc, MacrosDoc):
                self.macros.update(doc.__dict__)
            elif isinstance(doc, VariablesDoc):
                self.variables.update(doc.__dict__)
            else:
                self.doc.update(doc)

    def load_file(self, filename):
        """Load a template file from disk."""
        with open(filename, 'r') as fp:
            self.load_string(fp.read())


class TemplateCompiler(object):
    """Compiles a CloudFormer template to a CloudFormation template.

    The compiled template will be accessible through the ``doc``
    attribute.
    """

    def __init__(self):
        self.doc = None

    def load_string(self, s):
        """Load a CloudFormer template from a string."""
        reader = TemplateReader()
        reader.load_string(s)

        self.doc = OrderedDict()
        self.doc['AWSTemplateFormatVersion'] = '2010-09-09'

        meta = reader.doc['Meta']

        if 'Description' in meta:
            description = meta['Description']

            if 'Version' in meta:
                description += ' [v%s]' % meta['Version']

            self.doc['Description'] = description

        for key in ('Parameters', 'Mappings', 'Conditions', 'Resources',
                    'Outputs'):
            try:
                self.doc[key] = reader.doc[key]
            except KeyError:
                pass

    def load_file(self, filename):
        """Load a CloudFormer template from disk."""
        with open(filename, 'r') as fp:
            self.load_string(fp.read())
