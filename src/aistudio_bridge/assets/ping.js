(async () => {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), 25000);
  try {
    const r = await fetch('https://generativelanguage.googleapis.com/v1beta/models', { 
      headers: { 'x-goog-api-key': '{{GEMINI_API_KEY}}' },
      signal: controller.signal
    });
    clearTimeout(id);
    return r.status;
  } catch (e) {
    return -1;
  }
})()
