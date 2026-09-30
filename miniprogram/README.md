# Optional WeChat Client

This directory contains the original mobile client for StepWise. The React dashboard in `web/` is
the primary public demo; the Mini Program is retained to show that the same backend supports a second
client.

Before importing this directory into WeChat Developer Tools, replace the placeholders in `app.js`:

- `YOUR_WECHAT_CLOUD_ENV`
- `YOUR_CLOUD_HOSTING_DOMAIN`
- `YOUR_CLOUD_SERVICE_NAME`

The client submits sensor text to `POST /api/v1/analyses/text`, polls the status endpoint, and loads
the structured result after the job succeeds. No WeChat credentials or deployment identifiers are
committed to this repository.
