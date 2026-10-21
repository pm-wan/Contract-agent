-- Development-only role. Production credentials are provisioned outside this repository.
CREATE ROLE contract_runtime LOGIN PASSWORD 'local-dev-only';
GRANT CONNECT ON DATABASE contract_audit TO contract_runtime;
GRANT USAGE ON SCHEMA public TO contract_runtime;
