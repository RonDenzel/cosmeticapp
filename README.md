
# Cosmetic Interpreter — Streamlit App

This app ports the notebook's "executor" to a Streamlit UI with optional Firebase-backed inventory sync.

## Files
- `app.py` — Streamlit app
- `requirements.txt` — Python deps
- `runtime.txt` — Python version for Streamlit Cloud
- `.streamlit/secrets.toml` — **Do NOT commit**; add via Streamlit Cloud → App → Settings → Secrets
- `cosmetics_library.json` — your library data (place in repo root)
- `cosmetics_images/` — images folder (keep the theme subfolders & filenames)
- `firebase-service-account.json` — **Do NOT commit**. For local runs only.

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```

For Firebase sync locally, either:
1) Put `firebase-service-account.json` in the project root and set the same content in `.streamlit/secrets.toml` under `[firebase].service_account`, or
2) Export `STREAMLIT_SECRETS` in your environment (advanced).

## Deploy (Streamlit Community Cloud)
1. Push this repo to GitHub.
2. In the app settings on Streamlit Cloud, add Secrets with:
   ```toml
   [firebase]
   database_url = "https://cosmetic-c44de-default-rtdb.asia-southeast1.firebasedatabase.app"
   service_account = { ... full JSON object ... }
   ```
3. Deploy with `app.py` as the entry point.

## Security
- Never commit service account keys or `.streamlit/secrets.toml` to a public repo.
- This app uses Firebase Admin for *admin-side* account creation and email-only "login" (no password verification via Admin SDK). For production sign-in flows, use Firebase Client SDK + custom auth.
