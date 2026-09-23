# Проверка самого комплекта

Это не результаты тестов SOPDS и не проверка VM пользователя.

- python_syntax: passed
- shell_syntax: passed
- compose_yaml_structure: passed
- prepare_fresh_git_repository_nonroot: passed
- secret_file_permissions: passed
- idempotent_preserves_passwords: passed
- git_excludes_secrets: passed
- partial_credential_state_refused: passed
- shell_env_load: passed
- docker_postgres_runtime: not run
- sopds_runtime: not run
- user_vm: not accessed
