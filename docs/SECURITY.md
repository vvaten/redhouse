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

This installs a pre-commit hook that:
- Blocks commits of `.env.staging`, `.env.prod`, etc.
- Scans for patterns like `PASSWORD=`, `TOKEN=`, `API_KEY=`
- Prevents real credentials from being committed

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
