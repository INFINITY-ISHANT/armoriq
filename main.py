import json
import os
import requests
import uvicorn
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from fastapi import FastAPI, Request, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse

# ============================================================================
# CONFIGURATION (Loaded from Environment Variables)
# ============================================================================

# 🔑 AUTHENTICATION
MCP_API_KEY = os.environ.get("MCP_API_KEY")

# 📸 META GRAPH API CREDENTIALS
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")
IG_USER_ID = os.environ.get("IG_USER_ID")
GRAPH_URL = "https://graph.facebook.com/v24.0"

# 🔍 GOOGLE SEARCH API CREDENTIALS
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.environ.get("SEARCH_ENGINE_ID")

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
# IMAGE PROCESSING HELPERS
# ============================================================================

def _fetch_image_urls(query, num_images=1):
    """Fetches image URLs from Google Custom Search."""
    if not SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        raise ValueError("SEARCH_API_KEY or SEARCH_ENGINE_ID not set")
    
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'q': query,
        'key': SEARCH_API_KEY,
        'cx': SEARCH_ENGINE_ID,
        'searchType': 'image',
        'num': num_images,
        'safe': 'active'
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    search_results = response.json()
    if 'items' not in search_results:
        return []
    return [item['link'] for item in search_results['items']]

def _download_image(url, folder, filename):
    """Downloads an image from a URL."""
    if not os.path.exists(folder):
        os.makedirs(folder)
    file_path = os.path.join(folder, filename)
    img_data = requests.get(url, timeout=10).content
    with open(file_path, 'wb') as handler:
        handler.write(img_data)
    return file_path

def _apply_text_overlay(image_path, text, output_path, author=None):
    """Overlays text on an image professionally."""
    img = Image.open(image_path).convert("RGBA")
    
    # 1. Darken image for better contrast
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(0.5) # Reduce brightness to 50%
    
    draw = ImageDraw.Draw(img)
    width, height = img.size
    
    # 2. Find font - fallback safely if linux fonts missing
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path):
        font_path = None # Fallback to default
    
    # Base font size (scaled to image height)
    font_size = int(height / 15)
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        bold_path = font_path.replace(".ttf", "-Bold.ttf") if font_path else None
        bold_font = ImageFont.truetype(bold_path, int(font_size * 1.2)) if bold_path and os.path.exists(bold_path) else font
    except:
        font = ImageFont.load_default()
        bold_font = font

    # 3. Wrap text
    max_width = int(width * 0.8)
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        test_line = " ".join(current_line + [word])
        w = draw.textbbox((0, 0), test_line, font=font)[2]
        if w <= max_width:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]
    lines.append(" ".join(current_line))
    
    # 4. Calculate total text height
    line_spacing = int(font_size * 0.4)
    total_height = sum([draw.textbbox((0, 0), line, font=font)[3] for line in lines]) + (len(lines) - 1) * line_spacing
    
    author_text = ""
    author_h = 0
    if author:
        author_text = f"- {author}"
        author_h = draw.textbbox((0, 0), author_text, font=bold_font)[3]
        total_height += author_h + line_spacing * 2

    # 5. Draw lines
    curr_y = (height - total_height) // 2
    for line in lines:
        w = draw.textbbox((0, 0), line, font=font)[2]
        draw.text(((width - w) // 2, curr_y), line, font=font, fill=(255, 255, 255, 255))
        curr_y += draw.textbbox((0, 0), line, font=font)[3] + line_spacing
    
    if author:
        curr_y += line_spacing # Extra gap before author
        w = draw.textbbox((0, 0), author_text, font=bold_font)[2]
        draw.text(((width - w) // 2, curr_y), author_text, font=bold_font, fill=(255, 255, 255, 255))

    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path

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
                        },
                        {
                            "name": "fetch_google_images",
                            "description": "Fetches image URLs from Google Custom Search API and downloads them.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"},
                                    "num_images": {"type": "integer", "default": 5}
                                },
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "create_quote_image",
                            "description": "Fetches a background image and overlays a quote on it professionally.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "search_query": {"type": "string", "description": "Keywords to find a suitable background image."},
                                    "quote": {"type": "string", "description": "The quote text to overlay."},
                                    "author": {"type": "string", "description": "Optional author of the quote."}
                                },
                                "required": ["search_query", "quote"]
                            }
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
                    res = requests.post(f"{GRAPH_URL}/{IG_USER_ID}/media", params={
                        "image_url": args["image_url"],
                        "caption": args["caption"],
                        "access_token": ACCESS_TOKEN
                    })
                    res.raise_for_status()
                    creation_id = res.json().get("id")

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

            # --- FETCH GOOGLE IMAGES ---
            elif tool_name == "fetch_google_images":
                query = args.get("query")
                num_images = args.get("num_images", 5)
                # Render has ephemeral filesystem, stick to /tmp or just create local folder
                save_folder = './downloaded_images'
                
                try:
                    image_urls = _fetch_image_urls(query, num_images)
                    if not image_urls:
                        tool_result = {"status": "no_images_found", "message": "No images found."}
                    else:
                        downloaded_files = []
                        for i, url in enumerate(image_urls):
                            try:
                                clean_query = query.replace(' ', '_')
                                filename = f"{clean_query}_{i}.jpg"
                                path = _download_image(url, save_folder, filename)
                                downloaded_files.append({
                                    "filename": filename,
                                    "path": path,
                                    "source_url": url
                                })
                            except Exception as e:
                                print(f"Failed to download image {i}: {e}")
                        
                        tool_result = {
                            "status": "success", 
                            "downloaded_count": len(downloaded_files),
                            "files": downloaded_files
                        }
                except ValueError as ve:
                    tool_result = {"status": "CONFIG_ERROR", "message": str(ve)}
                except Exception as e:
                    tool_result = {"status": "API_ERROR", "details": str(e)}

            # --- CREATE QUOTE IMAGE ---
            elif tool_name == "create_quote_image":
                search_query = args.get("search_query")
                quote = args.get("quote")
                author = args.get("author")
                
                download_folder = './downloaded_images'
                final_folder = './final_posts'
                if not os.path.exists(final_folder):
                    os.makedirs(final_folder)

                try:
                    image_urls = _fetch_image_urls(search_query, 1)
                    if not image_urls:
                        tool_result = {"status": "error", "message": "No background image found."}
                    else:
                        bg_path = _download_image(image_urls[0], download_folder, "temp_bg.jpg")
                        
                        import time
                        final_filename = f"quote_{int(time.time())}.jpg"
                        final_path = os.path.join(final_folder, final_filename)
                        
                        _apply_text_overlay(bg_path, quote, final_path, author)
                        
                        tool_result = {
                            "status": "success",
                            "message": "Quote image created successfully.",
                            "final_image_path": final_path,
                            "original_image_url": image_urls[0]
                        }
                except ValueError as ve:
                    tool_result = {"status": "CONFIG_ERROR", "message": str(ve)}
                except Exception as e:
                    tool_result = {"status": "ERROR", "details": str(e)}

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