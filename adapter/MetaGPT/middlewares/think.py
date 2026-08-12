"""Middleware for thought tracing and editor command guidance injection."""

from adapter.middleware import Middleware
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor, RoleType
from utils.logging import logger

EDITOR_COMMANDS_DOC = """
    You can use the following Editor commands (**use exact parameter names!**):

    - Editor.create_file(filename: str)
    - Editor.insert_content_at_line(file_name: str, line_number: int, insert_content: str)
    - Editor.edit_file_by_replace(file_name: str,
        first_replaced_line_number: int,
        first_replaced_line_content: str,
        last_replaced_line_number: int,
        last_replaced_line_content: str,
        new_content: str)
    - Editor.open_file(path: str)

    Do NOT use parameters like 'line', 'content', 'file_path' 'file_name' unless specified above. Always use the exact parameter names and types as listed.

    After generating the command, ALWAYS check if you have used exact parameter names!
    """

class ThinkMiddleware(Middleware):
    """Record think outputs and prepend editor API contract to system prompt."""

    def __init__(self, monitor, name):
        """Initialize middleware with monitor used for step recording."""
        self.monitor = monitor
        self.name = name

    def before(self, ctx):
        """Inject editor command documentation into first system message."""
        if self.monitor is None or not isinstance(self.monitor, AttackMonitor):
            return
        system_msgs = ctx.kwargs.get('system_msgs')
        #如果调用方没传 system prompt，就造一个只含一个空字符串的列表，这样下一行 system_msgs[0] = ... 不会因为 None[0] 或空列表而报错。
        if system_msgs is None:
            system_msgs = [""]
        system_msgs[0] = self.monitor.inject_content(default_value=system_msgs[0])
        #system_msgs[0] = f'{EDITOR_COMMANDS_DOC}\n{system_msgs[0]}'
        if not self.monitor.is_injected():
            result = self.monitor.get_current_reply()
            return result
        ctx.kwargs['system_msgs'] = system_msgs

    def after(self, ctx, result):
        """Record thought content or inject replacement at target step."""
        if self.monitor is None:
            return result
        self.monitor.record_step(result, self.name, RoleType.ASSISTANT)
        return result