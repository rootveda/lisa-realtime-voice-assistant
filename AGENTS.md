# Agent instructions

When setting up or modifying this repository, read **[docs/SETUP.md](docs/SETUP.md)** first.

Key rules:

- Run stack scripts from the **repo root** (`./offline_setup/lisa_stack.sh`), not `offline_setup/app`.
- Never commit secrets, models, caches, or user RAG/chat data (see [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)).
- After clone: `./scripts/install_git_hooks.sh` — blocks unsafe commit/push to GitHub.
- Operator docs: [docs/user-guide.md](docs/user-guide.md), [docs/stack-start-stop.md](docs/stack-start-stop.md).
