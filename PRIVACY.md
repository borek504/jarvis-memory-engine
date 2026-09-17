# Privacy model

The engine is designed for local-first operation. Examples and tests in this repository use synthetic data only.

Do not commit or publish:

- live memory databases or context snapshots;
- credentials, tokens, OAuth material, private keys, cookies, or service-account files;
- account identifiers, remote object IDs, e-mail addresses, or user-specific absolute paths;
- raw conversation transcripts, private handoffs, or runtime logs containing personal data.

Integrators are responsible for configuring any transport or synchronization layer without weakening these boundaries.
