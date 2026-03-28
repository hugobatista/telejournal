## OneDrive Storage Configuration

This guide shows how to configure telejournal with OneDrive, including how to
create the Microsoft app client ID and client secret.

## Prerequisites

- A Microsoft account (personal or organizational)
- A running telejournal setup
- Your Telegram allowed user ID configured in telejournal

## Create Azure App Credentials

1. Open the Microsoft Entra admin center:
   [https://entra.microsoft.com](https://entra.microsoft.com)
2. Go to Entra ID > App registrations > New registration.
3. Choose a name such as `telejournal-onedrive`.
4. For supported account types, choose one of:
   - Accounts in any organizational directory and personal Microsoft accounts
   - Personal Microsoft accounts only
5. Leave Redirect URI empty (device code flow does not require it).
6. Create the app.

After creation:

1. Copy the **Application (client) ID**.
   - Use this value as `STORAGE_ONEDRIVE_CLIENT_ID`.
2. Copy the **Directory (tenant) ID** only if you want tenant-specific auth.
   - Otherwise, keep the telejournal default `common`.
3. Open Certificates and secrets > New client secret.
4. Add a description and expiry period, then create it.
5. Copy the secret value immediately.
   - Use this value as `STORAGE_ONEDRIVE_CLIENT_SECRET`.

## Enable Public Client Flows

Device code authentication requires public client flow support.

1. Open your app registration in Microsoft Entra.
2. Go to Authentication.
3. In Advanced settings, set **Allow public client flows** to **Yes**.
4. Save changes.

Reference:
[https://go.microsoft.com/fwlink/?linkid=2286380](https://go.microsoft.com/fwlink/?linkid=2286380)

## Configure Microsoft Graph Permissions

1. Open API permissions > Add a permission > Microsoft Graph.
2. Choose Delegated permissions.
3. Add:
   - `Files.ReadWrite`
   - `offline_access`
4. Save changes.

Notes:

- For many personal-account scenarios, explicit admin consent is not needed.
- For organizational tenants, an admin may need to grant consent.

## Configure telejournal

Set provider and credentials in environment variables:

```env
STORAGE_PROVIDER=onedrive
STORAGE_ONEDRIVE_TENANT_ID=common
STORAGE_ONEDRIVE_CLIENT_ID=your_app_client_id
STORAGE_ONEDRIVE_CLIENT_SECRET=your_app_client_secret
STORAGE_ONEDRIVE_ROOT_PATH=Apps/telejournal
STORAGE_ONEDRIVE_BATCH_WINDOW_SECONDS=60
```

Or in YAML:

```yaml
storage:
  provider: onedrive
  onedrive:
    tenant_id: common
    client_id: "${STORAGE_ONEDRIVE_CLIENT_ID}"
    client_secret: "${STORAGE_ONEDRIVE_CLIENT_SECRET}"
    root_path: Apps/telejournal
    batch_window_seconds: 60
```

## Complete Device Authorization

1. Start telejournal.
2. In Telegram, run:

```text
/storageauth start
```

3. Open the verification URL from the bot message and sign in.
4. Back in Telegram, run:

```text
/storageauth complete
```

5. Verify status:

```text
/storageauth status
```

telejournal stores refreshed tokens back into your YAML config when possible.

## Troubleshooting

- `client_id` missing:
  ensure `STORAGE_ONEDRIVE_CLIENT_ID` is set or
  `storage.onedrive.client_id` is present in YAML.
- `client_secret` missing:
  ensure `STORAGE_ONEDRIVE_CLIENT_SECRET` is set or
  `storage.onedrive.client_secret` is present in YAML.
- Authorization pending:
  complete login in the browser, then run `/storageauth complete` again.
- If `/storageauth start` fails before showing a code:
   confirm **Allow public client flows** is enabled in app Authentication
   settings.

