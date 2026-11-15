# Deploy Dosely to Google Cloud Run

One container serves the website and the API (see `Dockerfile` and `cloudrun.sh`). The site listens on Cloud Run's
`$PORT` and forwards `/api/*` to the API on `127.0.0.1:8000` inside the same container.

## Fastest path: Cloud Shell (nothing to install)

1. Open <https://console.cloud.google.com>, pick or create a project, and make sure billing is enabled.
2. Click the **Cloud Shell** icon (`>_`, top right). In the terminal that opens, run:

```sh
git clone https://github.com/Mukul07777/Dosely.git && cd Dosely
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

gcloud run deploy dosely --source . --region asia-south1 --allow-unauthenticated \
  --memory 2Gi --cpu 1 --min-instances 1 --max-instances 1 --no-cpu-throttling
```

3. Answer `y` if it asks to create an Artifact Registry repository. The build takes about 8 to 12 minutes.
4. The last line printed is the **Service URL** (`https://dosely-....a.run.app`). Open it, then open `/circle`.

The app runs without any keys: emails are recorded in an outbox and the weekly note uses built-in text.

## Add the keys (real emails and the Gemini note)

```sh
gcloud services enable secretmanager.googleapis.com
printf '%s' 'your-16-character-gmail-app-password' | gcloud secrets create smtp-password --data-file=-
printf '%s' 'your-gemini-api-key'                  | gcloud secrets create gemini-key --data-file=-
python3 -c "import secrets;print(secrets.token_hex(32),end='')" | gcloud secrets create dosely-data-key --data-file=-

# Let the service read them
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor

gcloud run services update dosely --region asia-south1 \
  --set-env-vars SMTP_HOST=smtp.gmail.com,SMTP_PORT=587,SMTP_USER=you@gmail.com,EMAIL_FROM="Dosely <you@gmail.com>" \
  --set-secrets SMTP_PASSWORD=smtp-password:latest,GEMINI_API_KEY=gemini-key:latest,KINCARE_DATA_KEY=dosely-data-key:latest
```

- Gmail app password: <https://myaccount.google.com/apppasswords> (needs 2-Step Verification).
- Gemini key: <https://aistudio.google.com/apikey>.

Keys live in Secret Manager, never in the image or the repository.

## Why these flags

- `--no-cpu-throttling --min-instances 1`: email reminders run on a one-minute loop inside the service. With
  throttled CPU or zero instances, reminders stop between requests.
- `--max-instances 1`: the database is SQLite inside the container, so there must be exactly one copy of it.
  It is reset when the container restarts. For lasting data, mount a Cloud Storage bucket
  (`--add-volume name=data,type=cloud-storage,bucket=YOUR_BUCKET --add-volume-mount volume=data,mount-path=/data`)
  and set `KINCARE_DB=/data/kincare.db`.
- `TZ=Asia/Kolkata` is set in the image, so an 08:00 dose means 08:00 in India.

## Check it

- `/circle` opens, and creating a profile works.
- `GET /api/circle/email/status` shows which email provider is active and whether Gemini is configured.
- Requests that change data are only accepted from the site itself: the API checks the browser's `Origin` against
  the host the site was reached on, so no address needs to be configured before the first deploy.
