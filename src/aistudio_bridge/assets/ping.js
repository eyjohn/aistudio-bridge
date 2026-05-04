fetch('https://generativelanguage.googleapis.com/v1beta/models?key={{GEMINI_API_KEY}}').then(r => r.status).catch(e => -1)
