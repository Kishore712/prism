# Provider packages

Each external agent platform owns a package under this directory. A provider
package contains only the code needed to understand that platform's source
formats or invoke that platform's integration surface.

Provider code implements the neutral protocols in `prism.protocols` and
returns the neutral models in `prism.models`. Services must not import provider
packages directly. The CLI composition root selects providers through
`prism.providers.registry`.

## Adding a provider

1. Create `prism/providers/<provider>/`.
2. Keep source schemas, parsers, and provider-specific validation there.
3. Implement the appropriate protocol from `prism.protocols`.
4. Add sanitized regression data under `tests/data/<provider>/` and mirrored
   tests under `tests/providers/<provider>/`.
5. Register the completed adapter in `prism.providers.registry`.

An empty provider namespace does not imply support. Only adapters present in
the registry are available at runtime.
