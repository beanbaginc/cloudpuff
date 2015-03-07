from __future__ import unicode_literals

import json
import re
from collections import OrderedDict
from itertools import chain

import six
import yaml
from yaml.constructor import ConstructorError


class TemplateState(object):
    """Manages the state of a template.

    This keeps track of all the variables, unresolved variables, and
    macros defined by a template. It also contains functions for resolving
    variables to values.
    """

    def __init__(self):
        self.variables = {}
        self.macros = {}
        self.unresolved_variables = set()
        self.imported_files = set()

    def update(self, other_state):
        self.macros.update(other_state.macros)
        self.variables.update(other_state.variables)
        self.unresolved_variables.update(other_state.unresolved_variables)
        self.imported_files.update(other_state.imported_files)

    def resolve(self, name, d):
        """Resolve a variable or macro name or path.

        If the name contains one or more dots, it will be looked up as
        a path within the provided dictionary.
        """
        result = d

        for part in name.split('.'):
            result = result[part]

        return result

    def process_tree(self, node_value, variables=None, resolve_variables=True):
        """Resolve variables found in a part of the tree.

        This will walk the tree and resolve any variables found. If
        a variable is referenced that does not exist, a KeyError will
        be raised.
        """
        if variables is None:
            variables = self.variables

        if isinstance(node_value, dict):
            return OrderedDict(
                (self.process_tree(key, variables, resolve_variables),
                 self.process_tree(value, variables, resolve_variables))
                for key, value in six.iteritems(node_value)
            )
        elif isinstance(node_value, list):
            value = [
                self.process_tree(item, variables, resolve_variables)
                for item in self.collapse_variables(node_value, variables)
            ]

            if (isinstance(node_value, VarsStringsList) and
                all([isinstance(item, basestring) for item in value])):
                value = ''.join(value)

            return value
        elif resolve_variables and isinstance(node_value, VarReference):
            try:
                value = self.resolve(node_value.name, variables)
                self.unresolved_variables.discard(node_value)

                return value
            except KeyError:
                raise KeyError('Unknown variable "%s"' % node_value.name)
        else:
            return node_value

    def normalize_vars_list(self, l):
        """Normalize a list to a list or VarsStringsList.

        If the list contains any VarReferences, this will be returned as a
        VarsStringsList. That allows the string to be later identified, so
        that it can potentially be turned back into a single string.
        """
        if any([isinstance(item, VarReference) for item in l]):
            return VarsStringsList(l)
        else:
            return l

    def collapse_variables(self, items, variables=None):
        """Collapse a list of strings or variable references.

        All string items will remain their own items in the list. Any
        variable reference items that have a matching variable in the
        template will be folded into the adjacent strings.
        """
        if variables is None:
            variables = self.variables

        can_collapse_string = not isinstance(items, UncollapsibleList)
        collapse_string = False
        result = []

        for item in items:
            collapse_next_string = False

            if isinstance(item, VarReference):
                try:
                    item = self.resolve(item.name, variables)
                except KeyError:
                    # We'll keep it as a VarReference, and store it
                    # for later.
                    item.parent = result
                    self.unresolved_variables.add(item)
                else:
                    collapse_string = can_collapse_string
                    collapse_next_string = can_collapse_string

            if isinstance(item, basestring):
                if collapse_string and result:
                    result[-1] += item
                else:
                    result.append(item)

                collapse_string = collapse_next_string
            else:
                result.append(item)
                collapse_string = False

        return result


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
        parser = StringParser(self.template_state)

        return parser.parse_string(node.value)

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


TemplateLoader.register_template_constructors()


class StringParserStack(list):
    """Manages the stack of functions and other items when parsing strings.

    This is a convenience around a list that associates the stack with
    any added items, provides access to the StringParser, and provides
    friendlier access to the most recent stack item.
    """

    def __init__(self, parser):
        super(StringParserStack, self).__init__()

        self.parser = parser

    @property
    def current(self):
        """Return the current stack item."""
        return self[-1]

    def push(self, item):
        """Push a new item onto the stack.

        The item will have its ``stack`` attribute set to this stack.
        """
        item.stack = self
        self.append(item)


class StringParserStackItem(object):
    """A parsed item from a string that can appear on the stack.

    The item may have content, and may indicate how many pops are required
    to clear back to the parent. Content can be added, and the representation
    of this item can be serialized.

    Subclasses can provide additional functionality and serialization.
    """

    def __init__(self, stack=None, contents=None, pop_count=1):
        self.stack = stack
        self.contents = contents or []
        self.pop_count = pop_count

    def add_content(self, content):
        """Add content to the item.

        This will return True if the added content can be pushed as a new
        item onto the stack.
        """
        self.contents.append(content)

        return isinstance(content, Function)

    def serialize(self):
        """Serialize the stack item and its contents to a data structure."""
        return [
            self.normalize_content(content)
            for content in self.contents
        ]

    def normalize_content(self, content):
        """Normalize the provided content.

        If the content is another stack item, it will be serialized.

        If it's a list, it will be normalized, collapsed, and set up with
        a Fn::Join if appropriate.
        """
        if isinstance(content, StringParserStackItem):
            content = content.serialize()

        if isinstance(content, list):
            content = [
                self.normalize_content(content)
                for content in content
            ]

            template_state = self.stack.parser.template_state
            content = template_state.normalize_vars_list(
                template_state.process_tree(content, resolve_variables=False))

            if len(content) == 1:
                content = content[0]
            elif len(content) > 1 and not isinstance(content, VarsStringsList):
                content = {
                    'Fn::Join': ['', content]
                }

        return content

    def __repr__(self):
        return '<%r: %r>' % (self.__class__.__name__, self.serialize())


class Function(StringParserStackItem):
    """A CloudFormation function call appearing in a string."""

    def __init__(self, func_name, params=None, **kwargs):
        super(Function, self).__init__(**kwargs)

        self.func_name = func_name
        self.params = params or []

    def validate(self, stack):
        """Validates the function's placement in the current stack."""
        pass

    def normalize_function_name(self):
        """Normalize the function name used for serialization.

        By default, this prefixes the function name with "Fn::", as
        needed by CloudFormation.
        """
        return 'Fn::%s' % self.func_name

    def serialize(self):
        norm_func_name = 'Fn::%s' % self.func_name

        return {
            norm_func_name: self.params,
        }


class BlockFunction(Function):
    """A CloudFormation block-level function call appearing in a string."""

    def normalize_function_contents(self, contents):
        """Normalize the block contents of the function.

        By default, this called normalize_content() on each piece of
        content.
        """
        return [
            self.normalize_content(content)
            for content in contents
        ]

    def serialize(self):
        norm_func_name = self.normalize_function_name()
        norm_contents = self.normalize_function_contents(self.contents)

        return {
            norm_func_name: UncollapsibleList(self.params + norm_contents)
        }


class IfBlockFunction(BlockFunction):
    """A CloudFormation If statement.

    If statements, in our template, can have matching Else and ElseIf
    statements. These all get turned into a tree of CloudFormation If
    statements.
    """

    def __init__(self, *args, **kwargs):
        super(IfBlockFunction, self).__init__(*args, **kwargs)

        self._is_elseif = False
        self._if_true_content = []
        self._if_false_content = []
        self._cur_content = self._if_true_content

    def validate(self, stack):
        """Validate the If statement's position in the stack.

        If this is actually an ElseIf, then this will ensure it's placed
        in an If statement.
        """
        if self._is_elseif and not isinstance(stack.current, IfBlockFunction):
            raise ConstructorError(
                'Found ElseIf without a matching If or ElseIf')

    def add_content(self, content):
        """Add content to the if statement.

        Initially, content will be added to the if-true section.
        If an Else or ElseIf function is being added, content will
        switch to being added to the if-false section.

        Adding an ElseIf will trigger a new If block inside the if-false
        section.
        """
        if isinstance(content, BlockFunction):
            if content.func_name in ('Else', 'ElseIf'):
                if not self._if_true_content:
                    raise ConstructorError(
                        'Found %s without a "true" value in the If'
                        % content.func_name)
                elif self._if_false_content:
                    raise ConstructorError(
                        'Found %s after an Else'
                        % content.func_name)

                self._cur_content = self._if_false_content

                if content.func_name == 'Else':
                    return False
                elif content.func_name == 'ElseIf':
                    # Simulate being an If statement, since that's what
                    # it turns into. Set it up to handle the proper depth
                    # in the stack.
                    content.func_name = 'If'
                    content.pop_count = self.pop_count + 1
                    content._is_elseif = True

        self._cur_content.append(content)

        return True

    def normalize_function_name(self):
        """Normalize the name of the function.

        An If and ElseIf will always be serialized to Fn::If in
        CloudFormation.
        """
        return 'Fn::If'

    def normalize_function_contents(self, contents):
        """Normalizes the if-true and if-false content.

        The if-true content will always be added, but if-false will only
        be added if there's actual content there.
        """
        contents = []
        if_true_content = self.normalize_content(self._if_true_content)
        if_false_content = self.normalize_content(self._if_false_content)

        if isinstance(if_true_content, list):
            contents += if_true_content
        else:
            contents.append(if_true_content)

        if if_false_content:
            if isinstance(if_false_content, list):
                contents += if_false_content
            else:
                contents.append(if_false_content)

        return contents


class ElseBlockFunction(BlockFunction):
    """An Else block, as part of an If statement."""

    def validate(self, stack):
        """Validate the Else block's position in hte stack.

        This will ensure that the Else block is within an If block.
        """
        if not isinstance(stack.current, IfBlockFunction):
            raise ConstructorError('Found Else without a matching If')


class StringParser(object):
    """Parses a string for functions, variables, and references."""

    PARSE_STR_RE = re.compile(
        r'('

        # CloudFormation functions, optionally with opening blocks
        r'<%\s*(?P<func_name>[A-Za-z]+)\s*'
        r'(\((?P<params>([^)]+))\))?'
        r'(?P<func_open>\s*{)?\s*%>\n?'

        # Closing braces for block-level CloudFormation functions
        r'|(?P<func_close><%\s*}\s*%>)\n?'

        # Resource/Parameter references
        r'|@@(?P<ref_brace>{)?(?P<ref_name>[A-Za-z0-9:_]+)(?(ref_brace)})'

        # Template variables
        r'|\$\$((?P<var_name>[A-Za-z0-9_]+)|{(?P<var_path>[A-Za-z0-9_.]+)})'

        r')')

    FUNC_PARAM_RE = re.compile(',\s*')

    FUNCTIONS = {
        'If': IfBlockFunction,
        'ElseIf': IfBlockFunction,
        'Else': ElseBlockFunction,
    }

    def __init__(self, template_state):
        self.template_state = template_state

    def parse_string(self, s):
        """Parse a string.

        The string will be parsed, with variables, function calls, and
        references being turned into their appropriate CloudFormation
        representations.

        The resulting string, or list of strings/dictionaries, will be
        returned.

        If the result is a list of items, they will be wrapped in a Fn::Join.

        If the string starts with "__base64__", the result will be wrapped
        in a Fn::Base64.
        """
        lines = s.splitlines(True)

        if lines[0].strip() == '__base64__':
            process_func = 'Fn::Base64'
            lines = lines[1:]
        else:
            process_func = None

        func_stack = StringParserStack(self)

        # Parse the line, factoring in the previous lines' stack-altering
        # function calls, to build a single stack of all strings and
        # functions.
        for line in lines:
            self._parse_line(line, func_stack)

        # Make sure we have a completed stack without any missing
        # end blocks.
        if len(func_stack) > 1:
            raise ConstructorError('Unbalanced braces in template')

        cur_stack = func_stack.current
        result = cur_stack.normalize_content(cur_stack)

        if process_func:
            result = {
                process_func: result,
            }

        return result

    def _parse_line(self, s, func_stack=None):
        """Parse a line for any references, functions, or variables.

        Any substrings starting with "@@" will be turned into a
        { "Ref": "<name>" } mapping.

        Any substrings contained within "<% ... %>" will be turned into a
        { "Fn::<name>": { ... } } mapping.

        Any substrings starting with "$$" will be resolved into a variable's
        content, if the variable exists, or a VarReference if not.

        The provided function stack will be updated based on the results
        of the parse.
        """
        prev = 0

        if func_stack is None:
            stack = StringParserStack(self)
        else:
            stack = func_stack

        if not stack:
            stack.push(StringParserStackItem())

        for m in self.PARSE_STR_RE.finditer(s):
            start = m.start()
            groups = m.groupdict()

            if start > 0:
                stack.current.add_content(s[prev:start])

            if groups['func_name']:
                self._handle_func(groups, stack)
            elif groups['func_close']:
                self._handle_func_block_close(stack)
            elif groups['ref_name']:
                self._handle_ref_name(groups, stack)
            elif groups['var_name']:
                self._handle_var(stack, groups['var_name'])
            elif groups['var_path']:
                self._handle_var(stack, groups['var_path'])

            prev = m.end()

        if prev != len(s):
            stack.current.add_content(s[prev:])

        if func_stack is not None:
            return None
        else:
            parts = stack.current.contents

            if len(parts) > 1:
                return self.template_state.collapse_variables(parts)
            else:
                return parts[0]

    def _handle_func(self, groups, stack):
        """Handles functions found in a line.

        The list of parameters to the function will be parsed, and a
        Function or similar subclass will be instantiated with the
        information from the function.
        """
        func_name = groups['func_name']
        params = groups['params']
        cur_stack = stack.current
        in_block_func = isinstance(cur_stack, BlockFunction)

        if params:
            norm_params = [
                self._parse_line(value)
                for value in self.FUNC_PARAM_RE.split(params)
            ]
        else:
            norm_params = []

        cls = self.FUNCTIONS.get(func_name, BlockFunction)
        func = cls(func_name, norm_params, stack=stack)
        func.validate(stack)

        can_push = cur_stack.add_content(func)

        if can_push and groups['func_open']:
            stack.push(func)

    def _handle_func_block_close(self, stack):
        """Handles the end of block functions found in a line."""
        for i in range(stack.current.pop_count):
            stack.pop()

    def _handle_ref_name(self, groups, stack):
        """Handles resource references found in a line."""
        stack.current.add_content({
            'Ref': groups['ref_name']
        })

    def _handle_var(self, stack, var_name):
        """Handles variable references found in a line."""
        stack.current.add_content(VarReference(var_name))


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

    def __eq__(self, other):
        return self.name == other.name

    def __repr__(self):
        return '<VarReference(%s)>' % self.name


class VarsStringsList(list):
    """A list containing VarReferences."""

    def __repr__(self):
        return ('<VarsStringsList(%s)>'
                % super(VarsStringsList, self).__repr__())


class UncollapsibleList(list):
    """A list that cannot be collapsed."""

    def __repr__(self):
        return ('<UncollapsibleList(%s)>'
                % super(UncollapsibleList, self).__repr__())


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


class TemplateCompiler(object):
    """Compiles a CloudFormer template to a CloudFormation template.

    The compiled template will be accessible through the ``doc``
    attribute.
    """

    def __init__(self, for_amis=False):
        self.doc = None
        self.for_amis = for_amis
        self.ami_outputs = []

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

        # Look for any metadata specific to CloudFormer that we want to
        # process.
        self._scan_cloudformer_metadata()

    def load_file(self, filename):
        """Load a CloudFormer template from disk."""
        with open(filename, 'r') as fp:
            self.load_string(fp.read())

    def to_json(self):
        """Return a JSON string version of the compiled template."""
        return json.dumps(self.doc, indent=4)

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
