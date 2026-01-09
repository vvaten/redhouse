# Security Guidelines

## Credential Management

### NEVER commit these files to git:

- `.env.staging` - Contains production credentials
- `.env.prod` / `.env.production` - Production configuration
- Any file with real passwords, tokens, or API keys

### Safe to commit:

- `.env.example` - Template with dummy values only
- Documentation files
- Source code (without embedded secrets)

## Git Hooks Protection

After cloning this repository, set up git hooks to prevent accidental credential commits:

```bash
./deployment/setup-git-hooks.sh
```

This installs a pre-commit hook that provides three layers of protection:

### Check 1: Block Production Files
- Prevents commits of `.env.staging`, `.env.prod`, `.env.production`
- Blocks known files that contain production credentials

### Check 2: Scan Environment Files
- Scans `.env` files for patterns like `PASSWORD=`, `TOKEN=`, `API_KEY=`
- Allows `.env.example` files with placeholder values
- Detects real credentials vs. dummy values (`your-`, `example`, `test`, etc.)

### Check 3: Scan Python Source Files (NEW)
- Detects hardcoded secrets in Python files (e.g., `API_KEY = "..."`)
- Identifies hex strings 32+ characters (common API key format)
- Recognizes long alphanumeric tokens (40+ characters)
- Allows safe patterns: `config.get()`, `os.getenv()`, `os.environ[]`
- Skips test files which may contain mock credentials

**Example patterns detected:**
```python
# BLOCKED - Hardcoded hex API key
FINGRID_API_KEY = "779865ac3644488cb77186b98df787cb"

# BLOCKED - Hardcoded token
AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# ALLOWED - Reading from config
api_key = config.get("FINGRID_API_KEY")
api_key = os.getenv("FINGRID_API_KEY")

# ALLOWED - Placeholder value
API_KEY = "your-api-key-here"
```

## If Credentials Are Exposed

If you accidentally commit credentials to git:

1. **IMMEDIATELY rotate the credentials** (change passwords/tokens)
2. Remove the file from git tracking:
   ```bash
   git rm --cached <filename>
   git commit -m "Remove sensitive file"
   ```
3. Add the file to `.gitignore`
4. Consider rewriting git history if needed (dangerous - use with caution):
   ```bash
   git filter-branch --force --index-filter \
     'git rm --cached --ignore-unmatch <filename>' \
     --prune-empty --tag-name-filter cat -- --all
   git push origin --force --all
   ```

## Best Practices

1. **Use templates**: Copy `.env.example` to `.env.staging` and fill in real values
2. **Keep credentials out of git**: Store in secure password managers
3. **Verify before commit**: Always check `git status` and `git diff --cached`
4. **Use the pre-commit hook**: It's your last line of defense
5. **Rotate credentials regularly**: Especially after team changes

## Environment File Structure

```
.env.example        ✅ In git (template with dummy values)
.env.staging        ❌ NOT in git (real staging credentials)
.env.prod           ❌ NOT in git (real production credentials)
.env.test           ❌ NOT in git (test credentials, may be local)
```

## Testing the Pre-commit Hook

Try committing `.env.staging` to test the protection:

```bash
# This should be BLOCKED by the pre-commit hook
git add .env.staging
git commit -m "test"

# Expected: Pre-commit hook prevents the commit
```

## Reporting Security Issues

If you discover a security vulnerability, please report it privately to the repository maintainer.
Do NOT open a public issue for security vulnerabilities.
