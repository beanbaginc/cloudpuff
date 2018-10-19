"""Template-related errors."""

from __future__ import unicode_literals


class TemplateError(Exception):
    """Error with a CloudPuff template."""

    def __init__(self, message, filename):
        """Initialize the error.

        Args:
            message (unicode):
                The error message.

            filename (unicode):
                The template filename.
        """
        super(TemplateError, self).__init__(message)

        self.filename = filename


class TemplateSyntaxError(TemplateError):
    """Syntax error with a CloudPuff template."""

    def __init__(self, message, filename, line, column, code):
        """Initialize the error.

        Args:
            message (unicode):
                The error message.

            filename (unicode):
                The template filename.

            line (int):
                The line number where the error occurred.

            column (int):
                The column number where the error occurred.

            code (unicode):
                The source code containing the syntax error.
        """
        super(TemplateError, self).__init__(
            '%s in "%s", line %s, column %s:\n%s' % (message, filename, line,
                                                     column, code))

        self.line = line
        self.column = column
        self.code = code
