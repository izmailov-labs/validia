"""The chat dialect adapters: one module per wire, each translating the same IR.

A dialect is a wire shape, not a vendor, so every module under here owns exactly one
request/response format and knows nothing about any other. `anthropic_messages` is
the first; the OpenAI and Google wires join it as their milestones land.

Nothing is imported here on purpose. This package is the natural place for the
registry's `ADAPTERS` mapping, but building that mapping eagerly would make
`import franca.chat.dialects` pull in every adapter -- and, through them, every
dialect's dependencies -- for a caller who wanted one. The mapping lands with the
registry, populated from the adapters the registrar is actually given; until then a
caller reaches an adapter by importing its own module.
"""
