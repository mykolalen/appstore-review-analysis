# Azure Container Apps deployment (no-card student route)

This is the deployment used for the public demo. It costs nothing: an
[Azure for Students](https://learn.microsoft.com/en-us/azure/education-hub/about-azure-for-students)
subscription needs no payment card, and a Container App on the Consumption plan that scales to zero
stays inside the free monthly grant (180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests per
subscription). When the student credit runs out, Azure disables the resources instead of charging.

The container is the same image as [`Dockerfile`](../Dockerfile) and
[`docs/deploy_cloud_run.md`](deploy_cloud_run.md); only the hosting differs.

## 1. Image

[`.github/workflows/publish-image.yml`](../.github/workflows/publish-image.yml) builds the image after CI
passes on `main` and pushes it to GitHub Container Registry:

```text
ghcr.io/mykolalen/appstore-review-analysis:latest
ghcr.io/mykolalen/appstore-review-analysis:<commit-sha>
```

The package inherits the repository's public visibility, so Azure pulls it anonymously without registry
credentials. A fork published from a private repository would need Package settings → Change visibility →
Public first.

## 2. Create the Container App (Azure portal)

Portal → **Container Apps** → **Create**:

| Tab | Field | Value |
| --- | --- | --- |
| Basics | Subscription | Azure for Students |
| Basics | Resource group | new: `rg-appstore-review` |
| Basics | Container app name | `appstore-review-analysis` |
| Basics | Deployment source | Container image |
| Basics | Region | an allowed student region, e.g. West Europe (pick another if the policy rejects it) |
| Basics | Container Apps environment | create new, Consumption only; Monitoring → *Don't save logs* |
| Container | Use quickstart image | unchecked |
| Container | Image source | Docker Hub or other registries, Public |
| Container | Registry login server | `ghcr.io` |
| Container | Image and tag | `mykolalen/appstore-review-analysis:latest` |
| Container | CPU and memory | 1 CPU core, 2 Gi memory |
| Container | Environment variables | `PUBLIC_MODE` = `true` |
| Ingress | Ingress | enabled, accepting traffic from anywhere, HTTP |
| Ingress | Target port | `8080` |

After creation, open **Application → Scale** and set **min replicas 0, max replicas 1**. One replica keeps
the process-local public rate limits exact; zero keeps the idle cost at zero.

A request after an idle period starts a replica: Azure pulls the image and the app loads both models, so
the first response can take a minute. Later requests are served by the warm replica.

## 3. Live verification

```bash
URL="https://<app>.<region>.azurecontainerapps.io"
curl -fsS "$URL/healthz"
curl -fsS "$URL/readyz"
curl -fsS "$URL/v1/analyses/62ce32e6-406d-5c6b-b94a-d153e37f78f6" >/dev/null
curl -fsS "$URL/v1/analyses/62ce32e6-406d-5c6b-b94a-d153e37f78f6/reviews?format=csv" | head -3
curl -fsS -X POST "$URL/v1/analyses" \
  -H 'content-type: application/json' \
  --data '{"app":"1459969523","country":"us","sample_size":100,"seed":42,"analyze":true}'
```

The POST without a provider must report `request.provider` = `fixture`. The interactive API docs are at
`$URL/docs`.

Container Apps ingress appends the client address to `X-Forwarded-For`, which matches the app's
right-most-hop rate-limit key. Check it on the live service: repeat a POST while changing only a forged
left-hand `x-forwarded-for` value and confirm the per-client limit still applies after five POSTs.

## 4. Updating and cleanup

Each push to `main` that passes CI publishes a new `latest` image. To roll it out, open the Container App
→ **Revisions and replicas** → **Create new revision** (same settings). Delete the resource group
`rg-appstore-review` to remove everything.
