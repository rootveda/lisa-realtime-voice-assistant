# Git hooks

Hooks block **commit** and **push** when sensitive or local-only files would reach GitHub.

Install once per clone:

```bash
./scripts/install_git_hooks.sh
```

- **pre-commit** — scans staged changes (`git_push_guard.sh --staged`)
- **pre-push** — scans full tracked tree before upload (`git_push_guard.sh --all`)

Emergency bypass (avoid unless you know why): `LISA_SKIP_PUSH_GUARD=1 git push`
