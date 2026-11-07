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

The hook itself is `scripts/hooks/pre-commit`, tracked in the repo; the
installed `.git/hooks/pre-commit` only calls it. It is a convenience: a
commit with `--no-verify` or from another clone skips it. The check that
always runs is the `secrets` job in CI, which scans the whole git history
with gitleaks, the default rules plus `.gitleaks.toml`, on every push.
`.gitleaksignore` lists the findings in the history that are no longer
secrets: revoked keys and placeholders, by fingerprint.

The hook provides these layers of protection:

### Check 1: Block Production Files
- Prevents commits of `.env.staging`, `.env.prod`, `.env.production`
- Blocks known files that contain production credentials

### Check 2: Scan Environment Files
- Scans `.env` files for patterns like `PASSWORD=`, `TOKEN=`, `API_KEY=`
- Allows `.env.example` files with placeholder values
- Detects real credentials vs. dummy values (`your-`, `example`, `test`, etc.)

### Check 3: Scan Every Staged Text File
- Docs too: the Fingrid key leaked through a Markdown example
- Blocks a hex value of 32+ characters assigned to a key, token, secret
  or password name (e.g., `API_KEY = "..."`, `"x-api-key": "..."`)
- Warns on long alphanumeric tokens (40+ characters)
- Allows safe patterns: `config.get()`, `os.getenv()`, `os.environ[]`
- Skips test files which may contain mock credentials

### Check 4: gitleaks
- Runs `gitleaks git --pre-commit --staged` with the repo rules when
  gitleaks is on PATH; without it the hook prints a note

**Example patterns detected:**
```python
# BLOCKED - Hardcoded hex API key
FINGRID_API_KEY = "<32 hex characters>"

# BLOCKED - Hardcoded token
AUTH_TOKEN = "<a JWT or other long token>"

# ALLOWED - Reading from config
api_key = config.get("FINGRID_API_KEY")
api_key = os.getenv("FINGRID_API_KEY")

# ALLOWED - Placeholder value
API_KEY = "your-api-key-here"
```

## Commit checks

What runs on `git commit`, in order:

1. The secret checks (Checks 1 to 4 above). A finding blocks the commit.
2. `scripts/run_all_checks.py --quick`: black, ruff, mypy, the unit
   tests and the code quality limits. A failure blocks the commit.

Common situations:

| Situation | What to do |
|-----------|------------|
| Fresh clone, the hook does not run | `./deployment/setup-git-hooks.sh` (git does not clone hooks) |
| A test or lint fails and the commit must go in anyway | `REDHOUSE_ALLOW_FAILING_CHECKS=1 git commit ...`; the secret checks still run |
| The hook says it found a key or token | Remove it from the change. If it was ever pushed, rotate it first |
| The CI `secrets` job fails | A secret is in a pushed commit: rotate it, then add the finding's fingerprint to `.gitleaksignore` with a comment that names the revoked value |
| The hook says gitleaks is not installed | Only a note: CI runs gitleaks on every push. To run it locally, put gitleaks 8.21.2 on PATH |
| A false positive blocks a commit | Rewrite the line (for example read the value from config). Do not add a placeholder to `.gitleaksignore` unless the value is not a secret |

Do not use `git commit --no-verify`. It skips the secret checks as well,
and only CI would catch a leak, after the push.

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
.env.example        [in git]  template with dummy values
.env.staging        [NOT in git]  real staging credentials
.env.prod           [NOT in git]  real production credentials
.env.test           [NOT in git]  test credentials, may be local
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
