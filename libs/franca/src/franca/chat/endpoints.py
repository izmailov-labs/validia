"""Built-in chat endpoint rows: where franca knows how to reach a chat model.

An endpoint is a row of data, never a branch. Everything that varies between one
provider's chat surface and another's -- the host, the path, which header carries the
key, the version header the wire insists on, what the surface is known to support --
is a field on `Endpoint`, so adding a provider is adding a row here (or, for a plugin,
registering one from outside franca) rather than editing any code that sends requests.

Two fields on the row below are load-bearing beyond their obvious meaning.

`id` is the trace key and the cassette directory name, so `"anthropic/chat/messages"`
is a stable name that outlives any refactor: changing it invalidates recorded fixtures
and breaks every dashboard keyed on it.

`extra_headers` carries `anthropic-version`, which the Messages API requires on every
request. Keeping it here rather than in the adapter is what makes an API-version bump
a one-line data change that no adapter has to know about; the `Connector` merges it
under anything the adapter or the caller set, and always under the auth header.

`features` is tri-state per feature (`True` verified, `False` verified absent, `None`
unchecked), and the values below are claims about the Messages API that franca has
confirmed: it streams, it caches, and it is a production surface rather than a preview.
Everything nobody has checked is left `None` by omission rather than guessed at.
"""

from typing import Final

from franca.core.endpoint import Endpoint, EndpointFeatures
from franca.core.enums import AuthScheme
from franca.core.ids import ANTHROPIC, ANTHROPIC_MESSAGES, CHAT, OPENAI, OPENAI_CHAT

ANTHROPIC_MESSAGES_ENDPOINT: Final = Endpoint(
    id="anthropic/chat/messages",
    provider=ANTHROPIC,
    capability=CHAT,
    dialect=ANTHROPIC_MESSAGES,
    base_url="https://api.anthropic.com",
    path="/v1/messages",
    auth=AuthScheme.x_api_key,
    extra_headers={"anthropic-version": "2023-06-01"},
    features=EndpointFeatures(streaming=True, caching=True, production=True),
)
"""Anthropic's Messages API: `POST https://api.anthropic.com/v1/messages`, `x-api-key`.

The one endpoint M0 can actually call. `AnthropicMessagesAdapter` speaks its dialect,
and the row's `path` is why that adapter leaves `WireRequest.path` at `None`.
"""

OPENAI_CHAT_ENDPOINT: Final = Endpoint(
    id="openai/chat/completions",
    provider=OPENAI,
    capability=CHAT,
    dialect=OPENAI_CHAT,
    base_url="https://api.openai.com",
    path="/v1/chat/completions",
    auth=AuthScheme.bearer,
    features=EndpointFeatures(streaming=True, caching=True, production=True),
)
"""OpenAI's Chat Completions API: `POST https://api.openai.com/v1/chat/completions`.

`auth` is `bearer` rather than `x-api-key`, which is the whole reason `AuthScheme`
is an enum on the row: the same `Connector` serves both providers and branches on
data, not on the provider's name.

`extra_headers` is empty on purpose. Unlike Anthropic, this wire carries no required
version header -- `OpenAI-Organization` and `OpenAI-Project` are optional scoping
headers, so they belong to whoever constructs the `Connector`, not to the row.

The Responses API (`/v1/responses`) is a *different dialect*, not a variant of this
one: it renames the prompt field, the output field and every usage counter. It gets
its own row and its own adapter when it lands.
"""
