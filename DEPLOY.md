# Felixa Community Health: online deployment

Status: prepared and tested locally with a fake model; not yet deployed to Railway with a real model.

1. Push this directory to a private GitHub repository.
2. Create a Railway service from that repository (it builds the `Dockerfile`).
3. Attach a persistent volume at `/data`. Allow at least 10 GB for the model, the database and temporary files.
4. Set `FELIXA_PASSWORD` to a strong unique password of at least 12 characters (the launcher refuses a shorter one). The username defaults to `felixa` (`FELIXA_USERNAME` overrides it).
5. Give the service enough RAM for the model. The hosted default is `qwen2.5:3b`: start with 8 GB and check memory and latency. Set `FELIXA_LOCAL_MODEL` to use another model (`qwen2.5:7b` needs more RAM). The launcher downloads that model and starts the web app on the same one.
6. Deploy and generate an HTTPS domain. Only the web port is exposed; Ollama listens on loopback.
7. Wait for `/health` to show `local_model_ready: true`, then run an analysis on `sample_data/sample_guild_chat.csv` and download the PDF.

Sign-in needs https. The session cookie is `Secure`, so a browser keeps it only on an https page (or at `http://127.0.0.1` on the same computer): open the app by the https domain from step 6. Over a plain-http address the app accepts the right password, the browser drops the cookie and the sign-in form comes back; the form shows a notice when that happens. A proxy of your own in front of the app has to pass the original `Host` header on, or the app refuses the sign-in form as a request from another site.

This is one password-protected, shared workspace, not separate customer accounts: everyone who signs in sees every analysis. Keep one service replica, because the database is SQLite. Back up the volume. A backup holds whatever the database held when it was taken, including messages deleted since: Delete evidence and results (see the README) does not reach into backups or snapshots of the volume.

Wrong passwords are slowed down the same way at the sign-in form and for HTTP Basic: a client address that sends five wrong ones in a row has to wait 2 seconds before its next try, then 4, 8 and so on up to 5 minutes, and is answered with a 429 and a `Retry-After` header (nothing sleeps, so a flood of bad attempts does not slow the app for anyone else). Someone who is already signed in is not held up. The count is per address as the app sees it: if the platform's proxy hides visitors' addresses, every visitor shares one count, so set `FORWARDED_ALLOW_IPS` to the proxy's address and uvicorn reads the visitor's address from `X-Forwarded-For`. A session lasts 8 hours and is kept in the app's memory: `POST /logout` ends it at once, and a restart or a new deployment signs everyone out.

The app limits the size of a request itself: an upload may carry 75 MB of files in all, a paste about 17 MB and any other form 2 MB (see the README). A request that declares more is refused with a 413 before it is read, and one that declares nothing is cut off at the limit, so a huge or hostile transfer cannot fill the container's temporary folder. A body limit at the proxy would only be a second layer.

The first launch downloads the model (about 2 GB for the default `qwen2.5:3b`, about 4.7 GB for `qwen2.5:7b`). The app stays available during the download, and `/health` reports when the model is ready. The startup script refuses to run without a password of at least 12 characters and, on Railway, without a volume.

CPU inference is slow. A full run rates three units per model call, so 300 units is 100 calls plus Vibe Check batches of 40 messages. Vibe Check reads at most about 1,000 messages from at most 25 sources, so its cost is bounded however large the upload is. Lower `FELIXA_MAX_RATED_UNITS` if runs take too long. If the model stops answering, Vibe Check tries each batch twice and gives up after three batches in a row, so at most six calls that each wait up to `FELIXA_MODEL_TIMEOUT` (480 seconds by default); lower the timeout on a small instance if you would rather it gave up sooner.
