# Security Configuration Notes

## 🔒 Keeping Your Real Configuration Private

This document explains how the repository is configured to keep your real IPs, ports, and credentials private while sharing example configurations publicly.

## Files in GitHub (Public)

### ✅ Safe to Share - Example Configurations

| File | Contains | Purpose |
|------|----------|---------|
| `.env.example` | Fake IPs (192.168.1.100:8080) | Template for users to copy |
| `config_example.py` | Placeholder tokens | Template for Pushover setup |
| `irrigation_app.py` | Fake IPs in repository | Example URLs for reference |
| `README.md` | Fake IPs and localhost | Documentation examples |

**Example IPs Used:**
- Control4: `192.168.1.100:8080`
- App Server: `localhost:5080`

## Files on Local Pi (Private)

### 🔐 Never Pushed to GitHub

| File | Contains | Status |
|------|----------|--------|
| `.env` | **Real Control4 URLs** | In .gitignore |
| `config.py` | **Real Pushover tokens** | In .gitignore |
| `irrigation_app.py` (local) | **Real IPs** | Skip-worktree enabled |
| `irrigation.db` | Database with history | In .gitignore |
| `backups/` | Database backups | In .gitignore |

## How It Works

### 1. .gitignore Protection
The following files are excluded from git tracking:
```
.env
config.py
*.db
backups/
__pycache__/
```

### 2. Skip-Worktree for irrigation_app.py
The `irrigation_app.py` file is tracked in git (with fake IPs) but local changes are ignored:
```bash
git update-index --skip-worktree irrigation_app.py
```

This means:
- ✅ GitHub has the file with fake IPs
- ✅ Local Pi has the file with real IPs
- ✅ Git ignores local modifications
- ✅ You won't accidentally commit real IPs

### 3. Example Files
Example files provide templates:
```bash
cp .env.example .env          # Then edit with real URLs
cp config_example.py config.py # Then edit with real tokens
```

## Verification

Check what's in GitHub vs Local:

```bash
# Show GitHub version (fake IPs)
git show HEAD:irrigation_app.py | grep C4_COMMANDS -A3

# Show Local version (real IPs)
grep C4_COMMANDS irrigation_app.py -A3

# Check skip-worktree status
git ls-files -v | grep "^S"
```

## If You Need to Update irrigation_app.py

If you need to update the code (not just IPs) in `irrigation_app.py`:

```bash
# 1. Temporarily re-enable tracking
git update-index --no-skip-worktree irrigation_app.py

# 2. Make your code changes (keep IPs as examples)
nano irrigation_app.py

# 3. Commit and push
git add irrigation_app.py
git commit -m "Update irrigation logic"
git push

# 4. Restore your real IPs locally
nano irrigation_app.py  # Change IPs back to real ones

# 5. Re-enable skip-worktree
git update-index --skip-worktree irrigation_app.py
```

## Security Checklist

Before pushing to GitHub, verify:

- [ ] No real IPs in committed files
- [ ] `.env` is in .gitignore
- [ ] `config.py` is in .gitignore
- [ ] `*.db` files are excluded
- [ ] Example files use fake data
- [ ] Local app still works with real config

## Current Status

✅ **GitHub Repository**: All sensitive data sanitized
✅ **Local Application**: Running with real configuration
✅ **Skip-worktree**: Enabled for irrigation_app.py
✅ **App Status**: Healthy and operational (v6.2.3)

---

**Last Updated**: December 2, 2025
**Repository**: https://github.com/rayalon1984/irrigation-web-app
