# Scraper MCP Test CURLs

Use these exactly as-is for local server `http://127.0.0.1:8002/mcp`.

## Session

```bash
# initialize (capture mcp-session-id from response headers)
curl -i -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":"init-1",
    "method":"initialize",
    "params":{
      "protocolVersion":"2024-11-05",
      "capabilities":{},
      "clientInfo":{"name":"curl","version":"1.0"}
    }
  }'
```

```bash
# send initialized notification (replace SESSION_ID)
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: SESSION_ID" \
  -d '{
    "jsonrpc":"2.0",
    "method":"notifications/initialized",
    "params":{}
  }'
```

## Tools

```bash
# tools/list
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"tools-1","method":"tools/list","params":{}}'
```

```bash
# fetch_page
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t1","method":"tools/call","params":{"name":"fetch_page","arguments":{"req":{"url":"https://hashagile.com/","last_content_hash":null,"wait_for":null,"js_code":null,"session_id":"hs-session-1","scroll_to_bottom":false,"stealth_mode":false,"proxy":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# fetch_page_full
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t2","method":"tools/call","params":{"name":"fetch_page_full","arguments":{"req":{"url":"https://hashagile.com/","include_media":true,"include_links":true,"include_raw_html":false,"screenshot":false,"wait_for":null,"js_code":null,"session_id":"hs-session-1","scroll_to_bottom":false,"stealth_mode":false,"proxy":null,"last_content_hash":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# fetch_pages_batch
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t3","method":"tools/call","params":{"name":"fetch_pages_batch","arguments":{"req":{"urls":["https://hashagile.com/"],"include_media":false,"include_links":true,"wait_for":null,"js_code":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# fetch_links
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t4","method":"tools/call","params":{"name":"fetch_links","arguments":{"req":{"url":"https://hashagile.com/","same_domain_only":true,"include_patterns":[],"exclude_patterns":[],"wait_for":null,"session_id":"hs-session-1","max_links":200,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# discover_urls (depth + breadth via max_depth + max_total_urls)
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t5","method":"tools/call","params":{"name":"discover_urls","arguments":{"req":{"seed_url":"https://hashagile.com/","max_depth":2,"max_total_urls":20,"same_domain_only":true,"include_patterns":[],"exclude_patterns":[],"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# deep_crawl (depth + breadth via max_depth + max_pages)
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t6","method":"tools/call","params":{"name":"deep_crawl","arguments":{"req":{"seed_url":"https://hashagile.com/","strategy":"bfs","max_depth":2,"max_pages":20,"include_external":false,"include_patterns":[],"exclude_patterns":[],"include_media":false,"query":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# screenshot_page
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t7","method":"tools/call","params":{"name":"screenshot_page","arguments":{"req":{"url":"https://hashagile.com/","wait_for":null,"full_page":false,"js_code":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# extract_structured
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t8","method":"tools/call","params":{"name":"extract_structured","arguments":{"req":{"url":"https://hashagile.com/","schema_json":{"type":"object","properties":{"title":{"type":"string"},"summary":{"type":"string"}}},"wait_for":null,"js_code":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# extract_structured_no_llm (shorthand schema now supported)
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t9","method":"tools/call","params":{"name":"extract_structured_no_llm","arguments":{"req":{"url":"https://hashagile.com/","extraction_schema":{"name":"Hashagile","baseSelector":"body","title":"h1","primary_cta":"a[href]"},"wait_for":null,"js_code":null,"scraping_config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# crawl_url
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t10","method":"tools/call","params":{"name":"crawl_url","arguments":{"req":{"url":"https://hashagile.com/","config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500},"include_media":true,"include_links":true,"max_depth":0,"max_pages":20,"strategy":"bfs"}}}}'
```

```bash
# search_and_crawl
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t11","method":"tools/call","params":{"name":"search_and_crawl","arguments":{"req":{"query":"https://hashagile.com/","max_results":5,"config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500},"include_media":false,"include_links":true}}}}'
```

```bash
# extract_links
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t12","method":"tools/call","params":{"name":"extract_links","arguments":{"req":{"url":"https://hashagile.com/","config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500},"same_domain_only":true,"max_links":50}}}}'
```

```bash
# extract_media
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t13","method":"tools/call","params":{"name":"extract_media","arguments":{"req":{"url":"https://hashagile.com/","config":{"max_depth":2,"max_links_per_page":30,"max_total_links":100,"allow_external_domains":false,"allow_subdomains":true,"allowed_domains":[],"blocked_domains":[],"max_concurrent_requests":3,"request_delay_ms":500}}}}}'
```

```bash
# normalize_to_schema (placeholder tool response)
curl -sS -X POST http://127.0.0.1:8002/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"t14","method":"tools/call","params":{"name":"normalize_to_schema","arguments":{"req":{"raw_content":"sample text","target_schema":{"type":"object","properties":{"title":{"type":"string"},"summary":{"type":"string"}}}}}}}'
```
