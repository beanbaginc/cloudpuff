from __future__ import unicode_literals

from collections import OrderedDict

import six


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
        self.embedded_files = set()
        self.if_conditions = OrderedDict()
        self.base_dir = None
        self.filename = None

    def update(self, other_state):
        self.macros.update(other_state.macros)
        self.variables.update(other_state.variables)
        self.unresolved_variables.update(other_state.unresolved_variables)
        self.imported_files.update(other_state.imported_files)
        self.embedded_files.update(other_state.embedded_files)

    def resolve(self, name, d):
        """Resolve a variable or macro name or path.

        If the name contains one or more dots, it will be looked up as
        a path within the provided dictionary.
        """
        result = d

        for part in name.split('.'):
            result = result[part]

        return result

    def process_tree(self, node_value, variables=None,
                     resolve_variables=True, resolve_if_conditions=False):
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
                 self.process_tree(value, variables, resolve_variables,
                                   resolve_if_conditions))
                for key, value in six.iteritems(node_value)
            )
        elif isinstance(node_value, list):
            value = [
                self.process_tree(item, variables, resolve_variables,
                                  resolve_if_conditions)
                for item in self.collapse_variables(node_value, variables)
            ]

            if isinstance(node_value, VarsStringsList):
                if all(isinstance(item, basestring) for item in value):
                    value = ''.join(value)
                else:
                    value = {
                        'Fn::Join': ['', value],
                    }
            elif isinstance(node_value, UncollapsibleList):
                value = UncollapsibleList(value)

            return value
        elif resolve_variables and isinstance(node_value, VarReference):
            try:
                value = self.resolve(node_value.name, variables)
                self.unresolved_variables.discard(node_value)

                return value
            except KeyError:
                raise KeyError('Unknown variable "%s"' % node_value.name)
        elif isinstance(node_value, IfCondition):
            if resolve_if_conditions:
                name = 'IfCondition%d' % (len(self.if_conditions) + 1)
                self.if_conditions[name] = self.process_tree(
                    node_value.condition, variables, resolve_variables)

                return name
            else:
                return IfCondition(self.process_tree(node_value.condition,
                                                     variables,
                                                     resolve_variables))
        else:
            return node_value

    def normalize_vars_list(self, l):
        """Normalize a list to a list or VarsStringsList.

        If the list contains any VarReferences, this will be returned as a
        VarsStringsList. That allows the string to be later identified, so
        that it can potentially be turned back into a single string.
        """
        has_vars = False

        for item in l:
            if isinstance(item, VarReference):
                has_vars = True
            elif not isinstance(item, basestring):
                return l

        if has_vars:
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


class IfCondition(object):
    """State on a parsed If condition.

    This stores information on a parsed expression for an If condition,
    and the parsed If function's dictionary that references it.
    """

    def __init__(self, condition):
        self.condition = condition

    def __eq__(self, other):
        if not isinstance(other, IfCondition):
            return False

        return self.condition == other.condition

    def __repr__(self):
        return '<IfCondition(%r)>' % self.condition


class VarReference(object):
    """A reference to a variable.

    These are used as placeholders for variables that are referenced.
    They are later resolved into the variable contents.
    """

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        if not isinstance(other, VarReference):
            return False

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
