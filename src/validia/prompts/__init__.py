"""The guided prompt builder: the questions ``validia create`` asks, and the prompt they make.

``template`` reads the builder's sections, checks and closing instructions from TOML
(``default.toml``, or a project's ``prompt.toml``); ``building`` turns answers into a
prompt, front-end agnostic, so a terminal and a web form ask the same questions.
"""
