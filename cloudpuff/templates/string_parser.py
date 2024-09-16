from __future__ import annotations

import re

from yaml.constructor import ConstructorError

from cloudpuff.templates.expression_parser import ExpressionParser
from cloudpuff.templates.state import (IfCondition, UncollapsibleList,
                                       VarReference, VarsStringsList)


# CloudFormation functions, optionally with opening blocks
FUNC_RE = re.compile(
    r'<%\s*(?P<func_name>[A-Za-z][A-Za-z0-9]+)\s*'
    r'(\((?P<params>(.*))\))?'
    r'(?P<func_open>\s*{)?\s*%>\n?')

# Closing braces for block-level CloudFormation functions
CLOSE_FUNC_RE = re.compile(r'(?P<func_close><%\s*}\s*%>)\n?')

# Resource/Parameter references
REFERENCE_RE = re.compile(
    r'@@(?P<ref_brace>{)?'
    r'(?P<ref_name>(?P<var_in_ref>\$\$)?'
    r'([A-Za-z0-9:_]|(?(ref_brace)(?(var_in_ref)\.)))+)'
    r'(?(ref_brace)})')

# Template variables
VARIABLE_RE = re.compile(
    r'\$\$((?P<var_name>[A-Za-z0-9_]+)|{(?P<var_path>[A-Za-z0-9_.]+)})')


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
                self.normalize_content(c)
                for c in content
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

    PARAMS_RE = re.compile(',\s*')

    @classmethod
    def parse_params(cls, params_str, process_string_func):
        """Parse the parameters for the function.

        By default, this splits the parameters by comma, and converts them
        into a list, with each value processed as a templated string.

        Subclasses can override this to provide custom behavior.
        """
        if not params_str:
            return []

        return [
            process_string_func(strip_quotes(value))
            for value in cls.PARAMS_RE.split(params_str)
        ]

    def __init__(self, func_name, params=None, **kwargs):
        super(Function, self).__init__(**kwargs)

        self.func_name = func_name

        if params is None:
            self.params = []
        else:
            self.params = params

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

    EXPR_RE = re.compile('(%s)' % '|'.join([
        r'\(',
        r'\)',
        r'\|\|',
        r'&&',
        r'==',
        r'!=',
        '"[^"]*"',
        "'[^']*'",
        '[A-Za-z0-9_]+',
        REFERENCE_RE.pattern,
        VARIABLE_RE.pattern,
    ]))

    EXPR_OPS = {
        '||': (1, 'LEFT'),
        '&&': (2, 'LEFT'),
        '==': (3, 'LEFT'),
        '!=': (3, 'LEFT'),
    }

    @classmethod
    def parse_params(cls, params_str, process_string_func):
        """Parse a conditional expression in the parameters.

        The if statement's parameters may be a valid conditional expression,
        which will be parsed and turned into a series of CloudFormation
        operator expressions. This allows for complex logic in conditionals.
        """
        def _process_op(op, lhs, rhs):
            if op == '||':
                return {
                    'Fn::Or': UncollapsibleList([lhs, rhs]),
                }
            elif op == '&&':
                return {
                    'Fn::And': UncollapsibleList([lhs, rhs]),
                }
            elif op == '==':
                return {
                    'Fn::Equals': UncollapsibleList([lhs, rhs]),
                }
            elif op == '!=':
                return {
                    'Fn::Not': [{
                        'Fn::Equals': UncollapsibleList([lhs, rhs]),
                    }]
                }

        tokenizer = ExpressionParser(cls.EXPR_RE, cls.EXPR_OPS,
                                     process_string_func, _process_op)

        return [tokenizer.parse(params_str)]

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
        """Normalizes the if-true and if-false content."""
        if_true_content = self.normalize_content(self._if_true_content)
        if_false_content = self.normalize_content(self._if_false_content)

        if not if_false_content:
            if_false_content = {
                'Ref': 'AWS::NoValue',
            }

        return [
            if_true_content,
            if_false_content,
        ]

    def serialize(self):
        norm_func_name = self.normalize_function_name()
        norm_contents = self.normalize_function_contents(self.contents)

        assert len(self.params) == 1

        if isinstance(self.params[0], dict):
            param = IfCondition(self.params[0])
        elif isinstance(self.params[0], str):
            param = self.params[0]
        else:
            raise ConstructorError('Invalid parameter to If: %r'
                                   % self.params[0])

        return {
            norm_func_name: UncollapsibleList([param] + norm_contents),
        }


class ElseBlockFunction(BlockFunction):
    """An Else block, as part of an If statement."""

    def validate(self, stack):
        """Validate the Else block's position in hte stack.

        This will ensure that the Else block is within an If block.
        """
        if not isinstance(stack.current, IfBlockFunction):
            raise ConstructorError('Found Else without a matching If')


class Base64Function(Function):
    """A wrapper around Fn::Base64."""

    @classmethod
    def parse_params(cls, params_str, process_string_func):
        """Parse the parameters to the Base64 function.

        Unlike most functions, Base64 does not take a list of arguments,
        but rather takes a single argument. This processes the parameters
        as a single argument.
        """
        params = Function.parse_params(params_str, process_string_func)

        if len(params) > 1:
            raise ConstructorError('Too many parameters passed to Base64')
        elif len(params) == 1:
            return params[0]
        else:
            return ''


class GetAZsFunction(Function):
    """A wrapper around Fn::GetAZs, for getting availability zones."""

    @classmethod
    def parse_params(cls, params_str, process_string_func):
        """Parse the parameters to the GetAZs function.

        Unlike most functions, GetAZs does not take a list of arguments,
        but rather takes a single argument. This processes the parameters
        as a single argument.
        """
        params = Function.parse_params(params_str, process_string_func)

        if len(params) > 1:
            raise ConstructorError('Too many parameters passed to GetAZs')
        elif len(params) == 1:
            return params[0]
        else:
            return ''


class SelectFunction(Function):
    """A wrapper around Fn::Select, for indexing into a list."""

    SELECT_PARAMS_RE = re.compile('(%s)' % '|'.join([
        r'^(?P<index>\d+),\s*(\[(?P<array>.+)\]',
        REFERENCE_RE.pattern,
        r'(?P<func>[A-Za-z]*\(.*\)))$'
    ]))

    ARRAY_ITEM_RE = re.compile('(%s)' % '|'.join([
        '"[^"]+"',
        "'[^']+'",
        VARIABLE_RE.pattern,
    ]))

    @classmethod
    def parse_params(cls, params_str, process_string_func):
        """Parse the parameters to the Select function.

        This handles the two cases supported by Fn::Select: Specifying an
        index and an array of values, or specifying an index and a reference.
        """
        m = cls.SELECT_PARAMS_RE.match(params_str)

        if not m:
            raise ConstructorError(
                'Cannot parse parameters to Select function: "%s"'
                % params_str)

        index = m.group('index')
        array = m.group('array')

        if array:
            return [
                index,
                UncollapsibleList([
                    process_string_func(strip_quotes(m.group(0)))
                    for m in cls.ARRAY_ITEM_RE.finditer(array)
                ])
            ]

        func = m.group('func')

        if func:
            return [index, process_string_func('<%% %s %%>' % func)]

        ref = m.group('ref_name')

        if ref:
            return [index, process_string_func('@@' + ref)]

        # We shouldn't be able to reach here.
        assert False

    def serialize(self):
        """Serialize the Select call to a function.

        Returns:
            dict:
            The CloudFormation representation of this function.
        """
        norm_func_name = self.normalize_function_name()

        assert len(self.params) == 2
        index, container = self.params

        if not isinstance(container, UncollapsibleList):
            container = self.normalize_content(container)

        return {
            norm_func_name: [index, container],
        }


class ImportValueFunction(Function):
    """A wrapper around Fn::ImportValue."""

    def serialize(self):
        """Serialize the ImportValue call to a function.

        Returns:
            dict:
            The CloudFormation representation of this function.
        """
        return {
            'Fn::ImportValue': self.normalize_content(self.params),
        }


class StringParser(object):
    """Parses a string for functions, variables, and references."""

    PARSE_STR_RE = re.compile('(%s)' % '|'.join([
        FUNC_RE.pattern,
        CLOSE_FUNC_RE.pattern,
        REFERENCE_RE.pattern,
        VARIABLE_RE.pattern,
    ]))

    FUNCTIONS = {
        'Base64': Base64Function,
        'If': IfBlockFunction,
        'ElseIf': IfBlockFunction,
        'Else': ElseBlockFunction,
        'GetAZs': GetAZsFunction,
        'Select': SelectFunction,
        'ImportValue': ImportValueFunction,
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
        if not s:
            return ''

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

        cls = self.FUNCTIONS.get(func_name, BlockFunction)
        norm_params = cls.parse_params(params, self._parse_line)

        func = cls(func_name, norm_params, stack=stack)
        func.validate(stack)

        can_push = stack.current.add_content(func)

        if can_push and groups['func_open']:
            stack.push(func)

    def _handle_func_block_close(self, stack):
        """Handles the end of block functions found in a line."""
        for i in range(stack.current.pop_count):
            stack.pop()

    def _handle_ref_name(self, groups, stack):
        """Handles resource references found in a line."""
        ref = groups['ref_name']

        if ref.startswith('$$'):
            ref = VarReference(ref[2:])

        stack.current.add_content({
            'Ref': ref,
        })

    def _handle_var(self, stack, var_name):
        """Handles variable references found in a line."""
        stack.current.add_content(VarReference(var_name))


def strip_quotes(s):
    """Strip leading and trailing quotes from a string."""
    if ((s.startswith('"') and s.endswith('"')) or
        (s.startswith("'") and s.endswith("'"))):
        return s[1:-1]
    else:
        return s
