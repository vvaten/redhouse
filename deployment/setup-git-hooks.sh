#!/bin/bash
# Setup git hooks for the redhouse project
# Run this after cloning the repository: ./deployment/setup-git-hooks.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

echo "Setting up git hooks for redhouse..."

# Create pre-commit hook
cat > "$HOOKS_DIR/pre-commit" << 'HOOK_EOF'
#!/bin/bash
# Pre-commit hook to prevent committing sensitive environment files

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Files that should NEVER be committed
BLOCKED_FILES=(
    ".env.staging"
    ".env.prod"
    ".env.production"
)

# Patterns to detect in file contents
SECRET_PATTERNS=(
    "PASSWORD="
    "TOKEN="
    "_PASSWORD="
    "_TOKEN="
    "SECRET="
    "API_KEY="
)

echo "Running pre-commit security checks..."

# Check if any blocked files are being committed
for file in "${BLOCKED_FILES[@]}"; do
    if git diff --cached --name-only | grep -q "^${file}$"; then
        echo -e "${RED}ERROR: Attempting to commit blocked file: ${file}${NC}"
        echo -e "${RED}This file may contain production credentials!${NC}"
        echo ""
        echo "To fix this:"
        echo "  git reset HEAD ${file}"
        echo "  Add '${file}' to .gitignore if not already present"
        exit 1
    fi
done

# Check staged .env* files for potential secrets
for file in $(git diff --cached --name-only | grep -E '\.env'); do
    # Skip .env.example as it should have dummy values
    if [[ "$file" == *.example ]]; then
        continue
    fi

    echo "Checking ${file} for potential secrets..."

    # Check if file contains suspicious patterns
    for pattern in "${SECRET_PATTERNS[@]}"; do
        if git diff --cached "$file" | grep -q "^+.*${pattern}"; then
            # Check if it looks like a real credential (not empty, not example)
            if git diff --cached "$file" | grep "^+.*${pattern}" | grep -vq -E '(your-|example|dummy|fake|test|xxx|<|>|\*\*\*)'; then
                echo -e "${RED}ERROR: File ${file} appears to contain real credentials!${NC}"
                echo -e "${YELLOW}Found pattern: ${pattern}${NC}"
                echo ""
                echo "Matched lines:"
                git diff --cached "$file" | grep "^+.*${pattern}" | head -3
                echo ""
                echo "To fix this:"
                echo "  1. Remove real credentials from ${file}"
                echo "  2. Use .env.example for templates with dummy values"
                echo "  3. Add ${file} to .gitignore"
                echo "  4. Store real credentials outside git (e.g., in secret manager)"
                exit 1
            fi
        fi
    done
done

echo "Security checks passed!"
exit 0
HOOK_EOF

# Make hook executable
chmod +x "$HOOKS_DIR/pre-commit"

echo "Git hooks installed successfully!"
echo ""
echo "Installed hooks:"
echo "  - pre-commit: Prevents committing files with production credentials"
echo ""
echo "These hooks will run automatically on git commit."
