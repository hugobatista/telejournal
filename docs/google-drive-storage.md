## Google Drive Storage Configuration

This guide shows how to configure telejournal with Google Drive, including how
to create the Google OAuth client ID and client secret.

## Prerequisites

- A Google account
- A Google Cloud project
- A running telejournal setup
- Your Telegram allowed user ID configured in telejournal

## Create Google OAuth Credentials

1. Open Google Cloud Console:
   [https://console.cloud.google.com](https://console.cloud.google.com)
2. Select or create a project for telejournal.
3. Enable the Google Drive API:
   APIs and Services > Library > Google Drive API > Enable.
4. Configure OAuth consent screen:
   - Choose user type (External is common for personal use).
   - Fill app name and support email.
   - Add your Google account as a test user when app is in testing mode.
5. Create credentials:
   APIs and Services > Credentials > Create credentials > OAuth client ID.
6. Choose application type:
   - TVs and Limited Input devices (recommended for telejournal device flow)
7. Create and copy:
   - Client ID -> `STORAGE_GOOGLE_DRIVE_CLIENT_ID`
   - Client secret -> `STORAGE_GOOGLE_DRIVE_CLIENT_SECRET`

If you get `invalid_client` during `/storageauth`, verify that your OAuth
client type supports Device Authorization and that ID and secret are correct.

## Optional: Create a Dedicated Drive Folder

1. In Google Drive, create a folder (for example `telejournal`).
2. Open the folder and copy its ID from the URL.
3. Set it as `STORAGE_GOOGLE_DRIVE_FOLDER_ID`.

If no folder ID is set, telejournal writes to your Drive root.

## Configure telejournal

Set provider and credentials in environment variables:

```env
STORAGE_PROVIDER=google_drive
STORAGE_GOOGLE_DRIVE_CLIENT_ID=your_google_client_id
STORAGE_GOOGLE_DRIVE_CLIENT_SECRET=your_google_client_secret
STORAGE_GOOGLE_DRIVE_FOLDER_ID=your_drive_folder_id
STORAGE_GOOGLE_DRIVE_BATCH_WINDOW_SECONDS=60
```

Or in YAML:

```yaml
storage:
  provider: google_drive
  google_drive:
    client_id: "${STORAGE_GOOGLE_DRIVE_CLIENT_ID}"
    client_secret: "${STORAGE_GOOGLE_DRIVE_CLIENT_SECRET}"
    folder_id: "${STORAGE_GOOGLE_DRIVE_FOLDER_ID}"
    batch_window_seconds: 60
```

## Complete Device Authorization

1. Start telejournal.
2. In Telegram, run:

```text
/storageauth start
```

3. Open the verification URL from the bot message and enter the user code.
4. Complete sign in and consent.
5. Back in Telegram, run:

```text
/storageauth complete
```

6. Verify status:

```text
/storageauth status
```

telejournal stores refreshed tokens back into your YAML config when possible.

## Troubleshooting

- `client_id` missing:
  ensure `STORAGE_GOOGLE_DRIVE_CLIENT_ID` is set or
  `storage.google_drive.client_id` is present in YAML.
- `client_secret` missing:
  ensure `STORAGE_GOOGLE_DRIVE_CLIENT_SECRET` is set or
  `storage.google_drive.client_secret` is present in YAML.
- `invalid_client`:
  recreate credentials using a client type that supports device flow and update
  telejournal config.
- Authorization pending:
  complete login in the browser, then run `/storageauth complete` again.
