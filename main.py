import json
import os
import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse

# ============================================================================
# CONFIGURATION (Loaded from Environment Variables)
# ============================================================================

MCP_API_KEY = os.environ.get("MCP_API_KEY")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")
IG_USER_ID = os.environ.get("IG_USER_ID")
GRAPH_URL = "https://graph.facebook.com/v19.0"

# ============================================================================
# SECURITY & HELPERS
# ============================================================================

app = FastAPI()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    """Enforces authentication on all MCP requests."""
    # Allow local testing if no key is set in env, otherwise enforce it
    if not MCP_API_KEY:
        print("WARNING: No MCP_API_KEY set. Allowing all requests.")
        return "debug-mode"
        
    if api_key == MCP_API_KEY:
        return api_key
    raise HTTPException(status_code=403, detail="⛔ Unauthorized: Invalid API Key")

def sse_pack(data):
    """Wraps JSON response in Server-Sent Events format."""
    return f"event: message\ndata: {json.dumps(data)}\n\n"

# ============================================================================
# MCP ENDPOINT
# ============================================================================

@app.post("/mcp", dependencies=[Depends(verify_api_key)])
async def handle_mcp_request(request: Request):
    try:
        req_data = await request.json()
        method = req_data.get("method")
        msg_id = req_data.get("id")
        
        response_data = None

        # --------------------------------------------------------------------
        # 1. INITIALIZE
        # --------------------------------------------------------------------
        if method == "initialize":
            response_data = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {
                        "name": "armor-iq-social-executor",
                        "version": "1.0.0"
                    }
                }
            }

        # --------------------------------------------------------------------
        # 2. TOOLS/LIST
        # --------------------------------------------------------------------
        elif method == "tools/list":
            response_data = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "publish_photo_post",
                            "description": "Publishes a photo to Instagram.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "image_url": {"type": "string"},
                                    "caption": {"type": "string"}
                                },
                                "required": ["image_url", "caption"]
                            }
                        },
                        {
                            "name": "get_recent_comments",
                            "description": "Fetches comments from the latest post.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "limit": {"type": "integer", "default": 5}
                                }
                            }
                        },
                        {
                            "name": "reply_to_comment",
                            "description": "Replies to a specific comment.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "comment_id": {"type": "string"},
                                    "message": {"type": "string"}
                                },
                                "required": ["comment_id", "message"]
                            }
                        },
                        {
                            "name": "get_account_insights",
                            "description": "Fetches account metrics.",
                            "inputSchema": {"type": "object", "properties": {}}
                        }
                    ]
                }
            }

        # --------------------------------------------------------------------
        # 3. TOOLS/CALL
        # --------------------------------------------------------------------
        elif method == "tools/call":
            tool_name = req_data["params"]["name"]
            args = req_data["params"].get("arguments", {})
            tool_result = {"status": "error", "message": "Unknown tool"}

            # --- PUBLISH PHOTO ---
            if tool_name == "publish_photo_post":
                try:
                    # 1. Container
                    res = requests.post(f"{GRAPH_URL}/{IG_USER_ID}/media", params={
                        "image_url": args["image_url"],
                        "caption": args["caption"],
                        "access_token": ACCESS_TOKEN
                    })
                    res.raise_for_status()
                    creation_id = res.json().get("id")
                    # 2. Publish
                    pub_res = requests.post(f"{GRAPH_URL}/{IG_USER_ID}/media_publish", params={
                        "creation_id": creation_id,
                        "access_token": ACCESS_TOKEN
                    })
                    tool_result = pub_res.json()
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # --- GET COMMENTS ---
            elif tool_name == "get_recent_comments":
                try:
                    media_res = requests.get(f"{GRAPH_URL}/{IG_USER_ID}/media", params={
                        "fields": "id", "limit": 1, "access_token": ACCESS_TOKEN
                    }).json()
                    
                    if "data" in media_res and len(media_res["data"]) > 0:
                        latest_id = media_res["data"][0]["id"]
                        comments = requests.get(f"{GRAPH_URL}/{latest_id}/comments", params={
                            "fields": "id,text,username,timestamp",
                            "limit": args.get("limit", 5),
                            "access_token": ACCESS_TOKEN
                        }).json()
                        tool_result = comments.get("data", [])
                    else:
                        tool_result = {"status": "no_posts_found"}
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # --- REPLY ---
            elif tool_name == "reply_to_comment":
                try:
                    reply_res = requests.post(f"{GRAPH_URL}/{args['comment_id']}/replies", params={
                        "message": args["message"],
                        "access_token": ACCESS_TOKEN
                    })
                    tool_result = reply_res.json()
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # --- INSIGHTS ---
            elif tool_name == "get_account_insights":
                try:
                    insights = requests.get(f"{GRAPH_URL}/{IG_USER_ID}", params={
                        "fields": "followers_count,media_count",
                        "access_token": ACCESS_TOKEN
                    }).json()
                    tool_result = insights
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # FORMAT RESPONSE
            response_data = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(tool_result)
                    }]
                }
            }

        return StreamingResponse(iter([sse_pack(response_data)]), media_type="text/event-stream")

    except Exception as e:
        error_response = {
            "jsonrpc": "2.0",
            "id": msg_id if 'msg_id' in locals() else None,
            "error": {"code": -32603, "message": str(e)}
        }
        return StreamingResponse(iter([sse_pack(error_response)]), media_type="text/event-stream")

if __name__ == "__main__":
    # Render assigns a PORT environment variable. We must use it.
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Render Service on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)