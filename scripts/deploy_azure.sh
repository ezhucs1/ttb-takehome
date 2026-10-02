#!/usr/bin/env bash
# Deploy LabelVerify to Azure App Service as a Linux container: always on (no cold starts),
# HTTPS by default, and a persistent /home directory for the SQLite database and images.
#
# Needs the Azure CLI (az >= 2.60) signed in (az login) and Docker (the image is built
# here and pushed to Azure Container Registry: free and trial subscriptions do not allow
# Azure's own cloud build). Then creates a B1 App Service plan, a web app from that image,
# and the app settings for a public URL.
#
#   ANTHROPIC_API_KEY=sk-ant-... GEMINI_API_KEY=... scripts/deploy_azure.sh
#
# Free and trial subscriptions start with a quota of zero B1 instances. Either request
# one (portal: Quotas > App Service > B1 > request 1; usually granted in minutes) or run
# with SKU=F1 for the free tier, which has no Always On: the first request after twenty
# idle minutes is slow, after that it answers normally.
#
# Claude reads the labels. With GEMINI_API_KEY set, Gemini words the specialist's
# correction notices (its free tier covers that call); without it, Claude does.
#
# Re-running the script with the same APP name rebuilds the image and restarts the app.
# Tear everything down with:  az group delete --name "$RG" --yes
set -euo pipefail

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY (the model key the deployment will use)}"
RG="${RG:-labelverify-rg}"
LOCATION="${LOCATION:-eastus}"
# One stable name per subscription, so a re-run reuses the registry, plan and app.
APP="${APP:-labelverify-$(az account show --query id --output tsv | cut -c1-8)}"
ACR="${ACR:-$(echo "$APP" | tr -d -)}"                                   # registry names: letters and digits only
PLAN="${PLAN:-$APP-plan}"
SKU="${SKU:-B1}"                                                         # Basic: Always On is available
IMAGE="labelverify:$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"

echo "resource group $RG in $LOCATION, app $APP, registry $ACR, image $IMAGE"
az group show --name "$RG" --output none 2>/dev/null \
  || az group create --name "$RG" --location "$LOCATION" --output none   # the group's own region does not matter

# 1. Build the image from this checkout and push it to the registry.
az acr show --name "$ACR" --resource-group "$RG" --output none 2>/dev/null \
  || az acr create --name "$ACR" --resource-group "$RG" --sku Basic --admin-enabled true --output none
REGISTRY="$(az acr show --name "$ACR" --resource-group "$RG" --query loginServer --output tsv)"
ACR_USER="$(az acr credential show --name "$ACR" --resource-group "$RG" --query username --output tsv)"
ACR_PASS="$(az acr credential show --name "$ACR" --resource-group "$RG" --query 'passwords[0].value' --output tsv)"
if command -v docker >/dev/null 2>&1; then
  DOCKER=docker
  if ! docker info >/dev/null 2>&1; then
    echo "your user cannot reach the Docker daemon; using sudo docker (add yourself to the docker group to avoid this)" >&2
    DOCKER="sudo docker"
  fi
  # Sign Docker in with the registry's own credentials rather than az acr login, which
  # would need Docker access as the current user.
  printf '%s' "$ACR_PASS" | $DOCKER login "$REGISTRY" --username "$ACR_USER" --password-stdin
  $DOCKER build -t "$REGISTRY/$IMAGE" .
  $DOCKER push "$REGISTRY/$IMAGE"
else
  echo "docker is not installed; trying Azure's cloud build (not available on free subscriptions)" >&2
  az acr build --registry "$ACR" --resource-group "$RG" --image "$IMAGE" . --output none
fi

# 2. The plan and the web app.
az appservice plan show --name "$PLAN" --resource-group "$RG" --output none 2>/dev/null \
  || az appservice plan create --name "$PLAN" --resource-group "$RG" --location "$LOCATION" \
       --is-linux --sku "$SKU" --output none
if ! az webapp show --name "$APP" --resource-group "$RG" --output none 2>/dev/null; then
  # The image name is given without the registry host: az prepends the registry URL.
  az webapp create --name "$APP" --resource-group "$RG" --plan "$PLAN" \
    --container-image-name "$IMAGE" \
    --container-registry-url "https://$REGISTRY" \
    --container-registry-user "$ACR_USER" --container-registry-password "$ACR_PASS" --output none
else
  az webapp config container set --name "$APP" --resource-group "$RG" \
    --container-image-name "$IMAGE" \
    --container-registry-url "https://$REGISTRY" \
    --container-registry-user "$ACR_USER" --container-registry-password "$ACR_PASS" --output none
fi

# 3. Settings for a public URL. /home persists across restarts and deployments on App
#    Service, so the database, the stored images and the session secret live there.
az webapp config appsettings set --name "$APP" --resource-group "$RG" --output none --settings \
  WEBSITES_PORT=8000 \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=true \
  WEBSITES_CONTAINER_START_TIME_LIMIT=240 \
  DATABASE_URL=sqlite:////home/data/labelverify.db \
  SECRET_KEY="$SECRET_KEY" \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  LABELVERIFY_DEMO_ACCOUNTS=false \
  LABELVERIFY_SECURE_COOKIES=true \
  LABELVERIFY_DAILY_READ_LIMIT="${LABELVERIFY_DAILY_READ_LIMIT:-150}" \
  LABELVERIFY_FALLBACK=tesseract \
  ${GEMINI_API_KEY:+GEMINI_API_KEY="$GEMINI_API_KEY" LABELVERIFY_NOTICE_PROVIDER=gemini}
ALWAYS_ON=true
case "$SKU" in F1|FREE|Free|free|D1|SHARED|Shared|shared) ALWAYS_ON=false ;; esac   # not offered on these tiers
az webapp config set --name "$APP" --resource-group "$RG" --always-on "$ALWAYS_ON" --http20-enabled true \
  --generic-configurations '{"healthCheckPath": "/healthz"}' --output none
az webapp update --name "$APP" --resource-group "$RG" --https-only true --output none
az webapp restart --name "$APP" --resource-group "$RG" --output none

URL="https://$(az webapp show --name "$APP" --resource-group "$RG" --query defaultHostName --output tsv)"
echo "waiting for $URL/healthz"
for _ in $(seq 1 40); do
  if curl -fsS "$URL/healthz" >/dev/null 2>&1; then
    echo "up: $URL"
    echo "sign in with the accounts in README.md (the dialog hides them on a public URL)"
    exit 0
  fi
  sleep 10
done
echo "the app did not answer within 400 s; check: az webapp log tail --name $APP --resource-group $RG" >&2
exit 1
