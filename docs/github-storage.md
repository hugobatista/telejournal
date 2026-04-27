## GitHub Storage Configuration

This guide shows how to configure telejournal with the GitHub storage provider,
including how to create a repository token.

## Prerequisites

- A GitHub account
- A target GitHub repository for journal data
- A running telejournal setup
- Your Telegram allowed user ID configured in telejournal

## Create or Select a Repository

1. Create a new repository (recommended private) or choose an existing one.
2. Note owner and repository name:
   - Owner -> `STORAGE_GITHUB_OWNER`
   - Repository -> `STORAGE_GITHUB_REPO`
3. Choose the branch telejournal should write to (usually `main`).

## Create a Fine-Grained Personal Access Token

1. Open GitHub token settings:
   [https://github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens)
2. Select Generate new token (fine-grained).
3. Set token name and expiration.
4. Set Resource owner to the owner of your target repository.
5. Limit Repository access to only the target repository.
6. Under Repository permissions, set:
   - Contents: Read and write
   - Metadata: Read-only (default)
7. Generate token and copy it immediately.

Use this token as `STORAGE_GITHUB_TOKEN`.

## Configure telejournal

Set provider and credentials in environment variables:

```env
STORAGE_PROVIDER=github_repo
STORAGE_GITHUB_OWNER=your-org-or-user
STORAGE_GITHUB_REPO=your-journal-repo
STORAGE_GITHUB_BRANCH=main
STORAGE_GITHUB_TOKEN=your_fine_grained_pat
STORAGE_GITHUB_PATH_PREFIX=
STORAGE_GITHUB_BATCH_WINDOW_SECONDS=60
```

Or in YAML:

```yaml
storage:
  provider: github_repo
  github_repo:
    owner: your-org-or-user
    repo: your-journal-repo
    branch: main
    token: "${STORAGE_GITHUB_TOKEN}"
    path_prefix: ""
    api_base_url: https://api.github.com
    batch_window_seconds: 60
```

## Run telejournal

Start the bot after configuration:

```bash
telejournal run
```

For first validation, send one message in Telegram and confirm that a daily
note file is created in the repository.

## Notes

- GitHub provider does not use `/storageauth`.
- Writes are queued and flushed in burst commits based on
  `batch_window_seconds`.
- On shutdown, telejournal performs a best-effort final flush.

## Troubleshooting

- `storage.github_repo.owner is required`:
  set `STORAGE_GITHUB_OWNER` or `storage.github_repo.owner`.
- `storage.github_repo.repo is required`:
  set `STORAGE_GITHUB_REPO` or `storage.github_repo.repo`.
- `storage.github_repo.token is required`:
  set `STORAGE_GITHUB_TOKEN` or `storage.github_repo.token`.
- GitHub API 401 or 403:
  verify token is valid, not expired, and scoped to the target repository with
  Contents read/write permission.
