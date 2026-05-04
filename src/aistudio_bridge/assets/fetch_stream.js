async function bridgeFetchStream(url, method, headers, bodyText, reqId) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000); // 60s timeout

    try {
        const fetchOptions = { method, signal: controller.signal };
        if (headers && Object.keys(headers).length > 0) fetchOptions.headers = headers;
        if (bodyText) fetchOptions.body = bodyText;

        const res = await fetch(url, fetchOptions);
        clearTimeout(timeoutId);

        // Report status and headers
        window.__stream_meta(JSON.stringify({
            reqId: reqId,
            status: res.status,
            headers: Object.fromEntries(res.headers.entries())
        }));

        const reader = res.body.getReader();
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                window.__stream_chunk(JSON.stringify({reqId: reqId, done: true}));
                break;
            }
            // Convert binary chunk to base64
            let binary = '';
            for (let i = 0; i < value.byteLength; i++) {
                binary += String.fromCharCode(value[i]);
            }
            window.__stream_chunk(JSON.stringify({reqId: reqId, chunk: btoa(binary)}));
        }
    } catch(e) {
        clearTimeout(timeoutId);
        window.__stream_meta(JSON.stringify({
            reqId: reqId,
            error: e.name === 'AbortError' ? 'Fetch timeout (60s)' : e.toString()
        }));
    }
}
