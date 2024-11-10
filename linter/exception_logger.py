from pylint.checkers import BaseChecker
from pylint.lint import PyLinter
import astroid


class ExceptionLoggerChecker(BaseChecker):
    name = 'try-except-logger'
    msgs = {
        'E9001': (
            'Missing logger.error with traceback in except block',
            'missing-exception-logger-error',
            'All except blocks must log the error using logger.error with traceback.format_exc().'
        ),
    }
    options = ()

    def __init__(self, linter=None):
        super().__init__(linter)

    def visit_try(self, node):
        # Skip non-opencxl files
        if not node.root().name.startswith('opencxl.'):
            return

        for handler in node.handlers:
            if self._is_whitelisted_exceptions(handler):
                continue

            # If the except block contains a raise statement, skip
            if any(self._contains_raise(stmt) for stmt in handler.body):
                continue

            if not any(self._contains_logger_error(stmt) for stmt in handler.body):
                self.add_message('missing-exception-logger-error', node=node)

    def _is_whitelisted_exceptions(self, handler):
        """
        Checks if the except handler is specifically for `asyncio.exceptions.TimeoutError`.
        """
        whitelisted_exceptions = ['TimeoutError', 'CancelledError', 'ConnectionClosed']
        if handler.type:
            for exception in whitelisted_exceptions:
                if isinstance(handler.type, astroid.Attribute) and handler.type.attrname == exception:
                    return True
                if isinstance(handler.type, astroid.Name) and handler.type.name == exception:
                    return True
        return False

    def _contains_raise(self, node):
        if isinstance(node, astroid.Raise):
            return True
        return any(self._contains_raise(child) for child in node.get_children())

    def _contains_logger_error(self, node):
        if isinstance(node, astroid.Expr) and isinstance(node.value, astroid.Call):
            func = node.value.func
            if (
                isinstance(func, astroid.Attribute)
                and func.attrname == 'error'
                and isinstance(func.expr, astroid.Name)
                and func.expr.name == 'logger'
            ):
                # Check all arguments for traceback.format_exc()
                for arg in node.value.args:
                    if self._contains_traceback_format_exc(arg):
                        return True
        return False

    def _contains_traceback_format_exc(self, node):
        if isinstance(node, astroid.Call):
            func = node.func
            # Check for traceback.format_exc()
            if (
                isinstance(func, astroid.Attribute)
                and func.attrname == 'format_exc'
                and isinstance(func.expr, astroid.Name)
                and func.expr.name == 'traceback'
            ):
                return True
            # Recursively check arguments of this call
            return any(self._contains_traceback_format_exc(arg) for arg in node.args)
        elif isinstance(node, astroid.BinOp) and node.op == '%':
            # Handle old-style string formatting: "msg % args"
            return any(self._contains_traceback_format_exc(child) for child in [node.left, node.right])
        elif isinstance(node, astroid.JoinedStr):
            # Handle f-strings by checking all values in the f-string
            return any(self._contains_traceback_format_exc(value) for value in node.values)
        elif isinstance(node, astroid.FormattedValue):
            # If it's an f-string, check the value inside
            return self._contains_traceback_format_exc(node.value)
        elif isinstance(node, astroid.Call) or isinstance(node, astroid.Expr):
            # Recursively check nested calls and expressions
            return any(self._contains_traceback_format_exc(child) for child in node.get_children())
        return False

def register(linter: PyLinter) -> None:
    linter.register_checker(ExceptionLoggerChecker(linter))
