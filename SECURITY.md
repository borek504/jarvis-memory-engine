# Security

Please do not open public issues containing secrets, credentials, private memory data, database files, or personal information.

For now, report potential security issues privately to the repository owner through GitHub.

The project is designed around a local-first model: canonical memory data stays local unless an integrator explicitly configures a transport layer.

## v0.1 threat boundary

The public store creates its SQLite file with owner-only permissions and rejects user-controlled symbolic-link path components. The database directory must be owned by the current operating-system user and must not be group- or world-writable.

The compact public API does not claim verified physical erasure, encrypted storage, protection from a compromised operating-system account, or production transport security. Integrators that need those properties must add and independently qualify deployment-specific controls.
