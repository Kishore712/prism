# ChatGPT provider

This package owns ChatGPT-specific acquisition logic.

The current `SyntheticChatGPTExportAdapter` accepts only Prism's explicitly
versioned synthetic ChatGPT-like sample. It does not claim compatibility with
the live ChatGPT product or an official export format. A real source variant
must be introduced with sanitized regression data, strict validation, and
provider contract tests before it is registered as supported.

Future ChatGPT-specific capture mechanisms, such as a browser-extension
payload adapter, belong in this package. They must expose provider-neutral
objects through protocols in `prism.protocols`.
