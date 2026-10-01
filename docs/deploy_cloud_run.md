# Cloud Run deployment checklist

This repository includes the public-demo runtime controls, but no cloud deployment is created by the
codebase. The operator performs deployment and records the live checks below.

## Public-mode contract

Set `PUBLIC_MODE=true` for the public service. In that mode:

- omitted providers are forced to `fixture`;
- `sample_size` is capped at 100;
- `provider=rss` is rejected;
- POST requests use process-local token buckets (defaults: 10 globally and 5 per client per 600 s);
- the per-client key is the right-most `X-Forwarded-For` value;
- the committed Nebula analysis is seeded at startup as
  `62ce32e6-406d-5c6b-b94a-d153e37f78f6`.

The limiter is intentionally process-local. Keep Cloud Run at one maximum instance if relying on these
exact limits.

## Build and deploy

Replace the placeholder variables before running these commands.

```bash
PROJECT_ID="your-project"
REGION="your-region"
REPOSITORY="appstore-review-analysis"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/api:latest"
SERVICE="appstore-review-analysis"

# One-time project setup; skip the repository step if it already exists.
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create "$REPOSITORY" --repository-format=docker --location="$REGION"

gcloud builds submit --tag "$IMAGE"

gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --cpu-boost \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 120 \
  --concurrency 2 \
  --set-env-vars PUBLIC_MODE=true
```

`--concurrency 2` is a conservative starting point, not a measured final setting. Measure peak RSS with
concurrent fixture POSTs on the deployed image. If measured peak RSS stays comfortably below the
2 GiB service limit, concurrency 4 can be evaluated; otherwise keep 2.

## Live verification

Get the deployed URL:

```bash
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
printf '%s\n' "$URL"
curl -fsS "$URL/readyz"
curl -fsS "$URL/v1/analyses/62ce32e6-406d-5c6b-b94a-d153e37f78f6"
```

### Rate limit and forwarded-address position

Google's front end must append the actual client address at the right-most position used by the app.
Verify this on the deployed service rather than assuming it from local tests. Send a request with a
forged left-hand value, repeat it, and confirm the per-client limiter cannot be bypassed by changing only
the forged value.

```bash
curl -i -X POST "$URL/v1/analyses" \
  -H 'content-type: application/json' \
  -H 'x-forwarded-for: 203.0.113.10' \
  --data '{"app":"1459969523","country":"us","sample_size":100,"seed":42,"analyze":false}'
```

Repeat while changing only the supplied `x-forwarded-for` value. Record the observed header chain from
trusted Cloud Run request logging before treating the right-most position as verified in production.

### Fixture path

A public request with no provider should use the committed fixture:

```bash
curl -fsS -X POST "$URL/v1/analyses" \
  -H 'content-type: application/json' \
  --data '{"app":"1459969523","country":"us","sample_size":100,"seed":42,"analyze":false}'
```

Confirm `request.provider` is `fixture` and the `Location` response header is relative.

### One live iTunes attempt

Before publishing the URL, explicitly test one `provider=itunes` request from Cloud Run and record the
outcome. This checks whether Apple accepts the service's cloud egress; it does not change the public
default provider.

```bash
curl -i -X POST "$URL/v1/analyses" \
  -H 'content-type: application/json' \
  --data '{"app":"1459969523","country":"us","sample_size":1,"seed":42,"provider":"itunes","analyze":false}'
```

Do not hide a failure: record the HTTP result and keep fixture mode as the public default.

## Cost and cleanup controls

Keep `--max-instances 1` as the hard service cap. Configure a billing alert separately; alerts notify but
do not stop spend. Add an Artifact Registry cleanup policy so old image revisions are removed.

## Persistence note

The Docker default is SQLite at `/app/var/app.db`. Cloud Run instance-local filesystem state is not a
durable user-data store. The fixed committed analysis is recreated on startup, but analyses created on
the public service should be treated as ephemeral unless `DATABASE_URL` points to an external persistent
database.
