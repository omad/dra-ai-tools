# GitHub Token Clone Troubleshooting (macOS + Git + GCM)

If Git clone over HTTPS fails with:

> `remote: Invalid username or token. Password authentication is not supported for Git operations.`

use the guidance below.

## 1) Use the right username/token format

For HTTPS URLs, authentication is:

- **Username**: depends on token type
- **Password**: the token value itself

### Common token prefixes

| Prefix | Token type | Typical owner | Git HTTPS username | Notes |
|---|---|---|---|---|
| `ghp_` | Classic Personal Access Token (PAT) | User account | Your GitHub username | Legacy PAT format. |
| `github_pat_` | Fine-grained PAT | User account | Your GitHub username | Preferred PAT format for user tokens. |
| `gho_` | OAuth access token | OAuth app flow | Your GitHub username (usually) | Usually for app/API flows; may work for Git if scopes allow. |
| `ghu_` | GitHub App user-to-server token | GitHub App on behalf of user | `x-access-token` (recommended) | Short-lived; app/user permissions must allow repo access. |
| `ghs_` | GitHub App installation token | GitHub App installation | `x-access-token` | **Short-lived**; must include repo content permission. |
| `ghr_` | Refresh token (not for Git) | OAuth/App auth flow | N/A | Do **not** use directly for Git clone. |

> For `ghs_...` and `ghu_...`, use username `x-access-token`.

Example (one-off clone, bypassing credential helper):

```bash
git -c credential.helper= clone https://x-access-token:YOUR_GHS_TOKEN@github.com/OWNER/REPO.git
```

Safer pattern (avoid token in shell history):

```bash
git -c credential.helper= clone https://github.com/OWNER/REPO.git
# prompt:
# Username: x-access-token
# Password: <paste ghs_... token>
```

---

## 2) Discover what credentials Git/GCM is using

### Check which helpers are active

```bash
git config --show-origin --get-all credential.helper
```

### Ask Git what it would use for github.com

```bash
printf "protocol=https\nhost=github.com\n\n" | git credential fill
```

If output shows unexpected username/password metadata, a cached credential is being used.

### Turn on auth tracing (safe for local troubleshooting)

```bash
GIT_TRACE=1 GIT_CURL_VERBOSE=1 git ls-remote https://github.com/OWNER/REPO.git
```

This helps verify whether Git is sending old credentials.

---

## 3) Clear cached GitHub credentials

## Option A: Clear via GCM

List what GCM has stored:

```bash
git credential-manager list
```

Erase GitHub entry:

```bash
printf "protocol=https\nhost=github.com\n\n" | git credential-manager erase
```

Then retry auth and re-enter the correct username/token pair.

## Option B: Clear via macOS Keychain

From terminal (safe scripted deletion):

```bash
security find-internet-password -s github.com
security delete-internet-password -s github.com
```

Or GUI:

1. Open **Keychain Access**
2. Search for `github.com`
3. Delete GitHub internet password entries related to old credentials

---

## 4) Common causes when `ghs_...` still fails

1. **Token expired** (installation tokens are short-lived).
2. **Wrong username** used (`x-access-token` required).
3. **App installation not on target repo/org**.
4. **Missing repo permissions** (at least contents read for clone/fetch).
5. **Org SSO/policy restrictions** blocking token usage.
6. **Cached stale credentials** still being sent by GCM/Keychain.

---

## 5) Quick reset + retry flow

```bash
# 1) Remove cached github.com credential
printf "protocol=https\nhost=github.com\n\n" | git credential-manager erase

# 2) Confirm helpers
git config --show-origin --get-all credential.helper

# 3) Retry clone, force manual prompt
git -c credential.helper= clone https://github.com/OWNER/REPO.git
# Username: x-access-token
# Password: <new ghs_... token>
```

