# Always-warm browser: Camoufox (a hardened Firefox) run as a Playwright
# WebSocket server. Other containers connect over the internal Docker network at
# ws://subitoo_browser:1234/hello and drive the already-running browser.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# System libraries Firefox/Camoufox need to run in a container (from known-good
# Camoufox-in-Docker setups). xvfb + xauth let us run the server under a real virtual
# display (see CMD) so Firefox runs *headed* (headless=False) rather than headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgtk-3-0 \
        libasound2 \
        libx11-xcb1 \
        libdbus-glib-1-2 \
        libxtst6 \
        libxrandr2 \
        libgbm1 \
        libxshmfence1 \
        xvfb \
        xauth \
        ca-certificates \
        dumb-init \
    && rm -rf /var/lib/apt/lists/*

# Install Camoufox and download its Firefox build at image-build time.
RUN pip install --no-cache-dir "camoufox[geoip]" \
    && python -m camoufox fetch

# Pin Playwright: Camoufox declares no upper bound, so pip would grab a version that
# breaks it. MUST match the app's pyproject [browser] pin. Because we now pass a real
# boolean headless (not the "virtual" string), the old <1.54 string-validation ceiling
# no longer applies. See SPEC.md §4 "Browser: Camoufox <-> Playwright version pin" before bumping.
# (Separate layer so `camoufox fetch` stays cached.)
RUN pip install --no-cache-dir "playwright==1.53.0"

COPY server.py /app/server.py
WORKDIR /app

EXPOSE 1234
ENTRYPOINT ["dumb-init", "--"]
# Run the whole server under a virtual X display (Xvfb). Firefox launched by
# launch_server inherits DISPLAY and renders *headed* into it -> real "virtual" mode,
# which launch_server itself does NOT set up (only Camoufox()/AsyncCamoufox() do).
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24 -ac +extension GLX +render -noreset", "python", "server.py"]
