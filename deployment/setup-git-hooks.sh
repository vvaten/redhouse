#!/bin/bash
# Setup git hooks for the redhouse project
# Run this after cloning the repository: ./deployment/setup-git-hooks.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

echo "Setting up git hooks for redhouse..."

# The hook itself is tracked in scripts/hooks, so a pull updates it
cat > "$HOOKS_DIR/pre-commit" << 'HOOK_EOF'
#!/bin/bash
hook="$(git rev-parse --show-toplevel)/scripts/hooks/pre-commit"
if [ ! -x "$hook" ]; then
    echo "NOTE: this checkout has no scripts/hooks/pre-commit, so no checks run"
    exit 0
fi
exec "$hook" "$@"
HOOK_EOF

chmod +x "$HOOKS_DIR/pre-commit" "$PROJECT_ROOT/scripts/hooks/pre-commit"

echo "Git hooks installed successfully!"
echo ""
echo "Installed hooks:"
echo "  - pre-commit: runs scripts/hooks/pre-commit (secret checks, then run_all_checks.py)"
echo ""
echo "For the full secret scan, install gitleaks 8.21.2 and put it on PATH."
