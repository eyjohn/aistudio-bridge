#!/bin/bash

PORT=${1:-8080}

# Fetch Models
echo "Testing Models GET on port $PORT..."
curl http://127.0.0.1:$PORT/v1beta/models
echo -e "\n"

# Simple standard request
echo "Testing standard POST on port $PORT..."
curl -X POST http://127.0.0.1:$PORT/v1beta/models/gemini-flash-lite-latest:generateContent \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Explain proxy streaming in 5 words."}]}]}'

echo -e "\n\nTesting SSE Stream on port $PORT..."
# Stream request
curl -X POST http://127.0.0.1:$PORT/v1beta/models/gemini-flash-lite-latest:streamGenerateContent?alt=sse \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Count from 1 to 5 slowly."}]}]}'

echo -e "\n\nTesting SLOW SSE Stream on port $PORT..."
# Stream request
curl -X POST http://127.0.0.1:$PORT/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?alt=sse \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Print 10 paragraphs of lorum ipsum."}]}]}'
