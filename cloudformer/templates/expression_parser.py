from __future__ import unicode_literals

from collections import namedtuple


Token = namedtuple('Token', ('name', 'value'))


class ExpressionParseError(ValueError):
    """An error parsing an expression."""


class ExpressionParser(object):
    """Parses an expression with values and operators.

    ExpressionParser takes a regex for iterating through an expression,
    a dictionary of operators, and functions to process the values and
    operators. It can then parse the expression as per the provided arguments,
    resulting in a processd set of data.

    The expression parser uses precedence climbing, which treats the expression
    as several nested sub-expressions. Each sub-expression has in common the
    lowest precedence level of all the operators contained within.

    The provided expression is first broken into a set of Tokens, each
    containing the type of token (LEFTPAREN, RIGHTPAREN, OP, or VALUE) and
    the string from the expression that token represents.

    The tokens are then turned into a lists consisting of atoms (a value, or
    parenthesized sub-expression) by computing the next atom from the next
    token in the list and checking the operator following it.

    If the operator's precedence level is lower than the minimum precedence
    level currently being processed, the sub-expression is complete. If it
    meets the minimum precedence level, the sub-expression continues. The
    left-hand side value is serialized/processed as per the process_op_func,
    and the right-hand side is computed as the next sub-expression.

    For more details, see this guide, which this code is based off of:
    http://eli.thegreenplace.net/2012/08/02/parsing-expressions-by-precedence-climbing
    """

    def __init__(self, pattern, ops, process_value_func, process_op_func):
        self.pattern = pattern
        self.ops = ops
        self.process_value_func = process_value_func
        self.process_op_func = process_op_func

        self._tokens = None
        self._cur_token = None

    def parse(self, expr):
        """Parse the provided expression.

        The expression will be parsed according to the provided pattern,
        operators, and processing functions. The result will be returned.
        """
        assert self._tokens is None

        self._tokens = self._iter_tokens(expr)
        self._next_token()

        try:
            return self._compute_expression()
        finally:
            self._tokens = None
            self._cur_token = None

    def _next_token(self):
        """Compute the next token.

        The next token will be fetched from the generator in _iter_tokens.
        """
        try:
            self._cur_token = self._tokens.next()
        except StopIteration:
            self._cur_token = None

    def _iter_tokens(self, expr):
        """Iterate through all tokens in the given expression.

        The expression will be parsed, as per the regular expression
        provided to the ExpressionParser. It will then be turned into a
        series of Tokens.
        """
        for m in self.pattern.finditer(expr):
            s = m.group(0)

            if s == '(':
                yield Token('LEFTPAREN', s)
            elif s == ')':
                yield Token('RIGHTPAREN', s)
            elif s in self.ops.keys():
                yield Token('OP', s)
            else:
                if ((s.startswith('"') and s.endswith('"')) or
                    (s.startswith("'") and s.endswith("'"))):
                    s = s[1:-1]

                yield Token('VALUE', self.process_value_func(s))

    def _compute_expression(self, min_precedence=1):
        """Compute a sub-expression from the tokens.

        The contents of this sub-expression will consist of the processed
        tokens with a precedence level greater than or equal to the
        provided minimum level.

        The sub-expression may contain other nested sub-expressions.
        """
        lhs = self._compute_atom()

        while True:
            token = self._cur_token

            if (token is None or
                token.name != 'OP' or
                self.ops[token.value][0] < min_precedence):
                break

            assert token.name == 'OP'

            op = token.value
            precedence, assoc = self.ops[op]

            if assoc == 'LEFT':
                next_min_precedence = precedence + 1
            else:
                next_min_precedence = precedence

            self._next_token()
            rhs = self._compute_expression(next_min_precedence)
            lhs = self._compute_op(op, lhs, rhs)

        return lhs

    def _compute_atom(self):
        """Compute an atom from the current token.

        An atom represents a sub-expression (wrapped in parenthesis), or
        a token value.
        """
        token = self._cur_token

        if token is None:
            raise ExpressionParseError('Unexpected end of expression')
        elif token.name == 'LEFTPAREN':
            self._next_token()
            value = self._compute_expression()

            if self._cur_token.name != 'RIGHTPAREN':
                raise ExpressionParseError('Unmatched "("')

            self._next_token()

            return value
        elif token.name == 'OP':
            raise ExpressionParseError('Unexpected operator "%s" found'
                                       % token.value)
        elif token.name == 'VALUE':
            self._next_token()

            return token.value

    def _compute_op(self, op, lhs, rhs):
        """Compute the result of an operator applied to two sub-expressions.

        The process_op_func provided to the ExpressionParser will be called
        on the operator and the left-hand-side/right-hand-side sub-expressions.
        It must return a value, which will be used as the result of the
        sub-expression.
        """
        if op not in self.ops:
            raise ExpressionParseError('Unknown operator "%s"' % op)

        return self.process_op_func(op, lhs, rhs)
