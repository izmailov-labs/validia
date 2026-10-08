"""The ``validia`` command: the terminal front end over the library.

``app`` assembles the command from one module per family -- ``suite_commands``
(``init``, ``create``, ``template``, ``add``, ``check``), ``config_command``,
``rule_commands`` (``lint`` and ``rules``) and ``run_command`` -- on the helpers in
``common``. ``interview`` asks the terminal's questions, ``settings`` resolves the
command's settings through whence, and ``_loop`` is the one place an event loop runs.
"""

from .app import Cli, CliError, main

__all__ = ["Cli", "CliError", "main"]
