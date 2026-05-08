// Silver Dome — Vercel Dashboard Connection Handler
// Connects to the Pi's WebSocket server from the Vercel-hosted dashboard.
//
// Usage: Add ?host=192.168.1.100:8080 to the URL to connect to a specific Pi.
// Default: tries localhost:8080 (for local dev).

(function() {
    const params = new URLSearchParams(window.location.search);
    const host = params.get('host') || 'localhost:8080';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

    // Expose the target host for the Socket.IO connection in the main page
    window.SILVER_DOME_HOST = host;
    window.SILVER_DOME_WS_URL = `${protocol}//${host}`;

    console.log(`[Silver Dome] Connecting to Pi at ${host}`);
})();
