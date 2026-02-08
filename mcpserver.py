import json
import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse

# ============================================================================
# CONFIGURATION
# ============================================================================

# 🔑 AUTHENTICATION
# The Agent (OpenClaw) must send this key in the header: "X-API-Key: hackathon-secret-123"
MCP_API_KEY = "hackathon-secret-123" 

# 📸 META GRAPH API CREDENTIALS
# Replace these with your actual values from the Meta Developer Portal
ACCESS_TOKEN = "EAFvFkqkSsaQBQuwLVtV6FVixoKlAsJR9COdiQwZARYzoILWB8CpESlFSSlrm7haZC6qZAgn3U7C537SHCuzIfIDMpDeOKXeB3ZBZAcSE9XBhQdQJpY29pW6HRRaAtE7tt8ZAKrkLynPUhbUjgJ6ZBnHa8OTAdqUfNWKxXftNR1ZAaUe1ZBmZAM791WaPJqjSMEoGgoG7IBayRQCdvoPZAMRrVOGfzGrBkpfMmj8ZCYeY"
IG_USER_ID = "17841480756943196"
GRAPH_URL = "https://graph.facebook.com/v24.0"

# ============================================================================
# SECURITY & HELPERS
# ============================================================================

app = FastAPI()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    """Enforces authentication on all MCP requests."""
    if api_key == MCP_API_KEY:
        return api_key
    raise HTTPException(status_code=403, detail="⛔ Unauthorized: Invalid API Key")

def sse_pack(data):
    """Wraps JSON response in Server-Sent Events format (Required by ArmorIQ)."""
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
        # 1. INITIALIZE (Handshake)
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
        # 2. TOOLS/LIST (Capabilities)
        # --------------------------------------------------------------------
        elif method == "tools/list":
            response_data = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "publish_photo_post",
                            "description": "Publishes a photo to Instagram. (Policy checks handled by OpenClaw).",
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
                            "description": "Fetches comments from the latest post to check for engagement needs.",
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
                            "description": "Fetches account metrics (Followers, Reach).",
                            "inputSchema": {"type": "object", "properties": {}}
                        }
                    ]
                }
            }

        # --------------------------------------------------------------------
        # 3. TOOLS/CALL (Execution)
        # --------------------------------------------------------------------
        elif method == "tools/call":
            tool_name = req_data["params"]["name"]
            args = req_data["params"].get("arguments", {})
            
            # Default result container
            tool_result = {"status": "error", "message": "Unknown tool"}

            # --- TOOL: PUBLISH PHOTO ---
            if tool_name == "publish_photo_post":
                try:
                    # 1. Create Container
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

            # --- TOOL: GET COMMENTS ---
            elif tool_name == "get_recent_comments":
                try:
                    # 1. Get latest media ID
                    media_res = requests.get(f"{GRAPH_URL}/{IG_USER_ID}/media", params={
                        "fields": "id", "limit": 1, "access_token": ACCESS_TOKEN
                    }).json()
                    
                    if "data" in media_res and len(media_res["data"]) > 0:
                        latest_id = media_res["data"][0]["id"]
                        # 2. Get comments
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

            # --- TOOL: REPLY TO COMMENT ---
            elif tool_name == "reply_to_comment":
                try:
                    reply_res = requests.post(f"{GRAPH_URL}/{args['comment_id']}/replies", params={
                        "message": args["message"],
                        "access_token": ACCESS_TOKEN
                    })
                    tool_result = reply_res.json()
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # --- TOOL: INSIGHTS ---
            elif tool_name == "get_account_insights":
                try:
                    # Note: Account must be Business/Creator for this endpoint
                    insights = requests.get(f"{GRAPH_URL}/{IG_USER_ID}", params={
                        "fields": "followers_count,media_count",
                        "access_token": ACCESS_TOKEN
                    }).json()
                    tool_result = insights
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # ----------------------------------------------------------------
            # RESPONSE FORMATTING (Strict Requirements)
            # ----------------------------------------------------------------
            response_data = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_result) # Requirement: Stringified JSON
                        }
                    ]
                }
            }

        # Return as SSE Stream
        return StreamingResponse(iter([sse_pack(response_data)]), media_type="text/event-stream")

    except Exception as e:
        # Global Error Handler
        error_response = {
            "jsonrpc": "2.0",
            "id": msg_id if 'msg_id' in locals() else None,
            "error": {"code": -32603, "message": str(e)}
        }
        return StreamingResponse(iter([sse_pack(error_response)]), media_type="text/event-stream")

if __name__ == "__main__":
    print("🚀 MCP Server starting on port 8000...")
    print(f"🔑 API Key required: {MCP_API_KEY}")
    uvicorn.run(app, host="127.0.0.1", port=8000)