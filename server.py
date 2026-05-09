import asyncio
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── paths ──────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
CONFIG_FILE = BASE / "config.json"
TOKEN_FILE = BASE / "tokens.json"

# ── load config ────────────────────────────────────────────────────────────
with open(CONFIG_FILE) as f:
    cfg = json.load(f)

CLIENT_ID = cfg["client_id"]
CLIENT_SECRET = cfg["client_secret"]
REDIRECT_URI = cfg["redirect_uri"]
SCOPES = cfg["scopes"]

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
API_BASE = "https://api.linkedin.com"

server = Server("linkedin-connector")


# ── token helpers ──────────────────────────────────────────────────────────
def load_tokens() -> dict | None:
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return None


def save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    resp.raise_for_status()
    return resp.json()


def get_valid_token() -> str:
    tokens = load_tokens()
    if not tokens:
        raise RuntimeError("Not authenticated. Use the 'authenticate' tool first.")
    return tokens["access_token"]


def get_person_id(access_token: str) -> str:
    resp = requests.get(
        f"{API_BASE}/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()["sub"]


def refresh_if_needed(access_token: str) -> str:
    """If access_token triggers a 401, refresh and return new token."""
    tokens = load_tokens()
    if tokens and "refresh_token" in tokens:
        new_tokens = refresh_access_token(tokens["refresh_token"])
        save_tokens(new_tokens)
        return new_tokens["access_token"]
    return access_token


# ── OAuth callback ─────────────────────────────────────────────────────────
_auth_code: str | None = None
_auth_error: str | None = None
_auth_event = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code, _auth_error
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;text-align:center;padding:60px">
                <h2>&#10003; Authenticated!</h2>
                <p>You can close this tab and return to Cowork.</p>
                </body></html>
            """)
        elif "error" in params:
            error = params.get("error", ["unknown"])[0]
            desc = params.get("error_description", ["no description"])[0]
            _auth_error = f"{error}: {desc}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>Auth error</h2><p>{_auth_error}</p>".encode())
        else:
            _auth_error = f"Unexpected callback: {self.path}"
            self.send_response(400)
            self.end_headers()
        _auth_event.set()

    def log_message(self, *args):
        pass


def _run_callback_server():
    HTTPServer(("localhost", 8000), CallbackHandler).handle_request()


# ── posting helpers ────────────────────────────────────────────────────────
def _ugc_post(access_token: str, author_urn: str, text: str, visibility: str) -> requests.Response:
    """UGC Posts API — no version header required, widely supported."""
    return requests.post(
        f"{API_BASE}/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": visibility
            },
        },
    )


def _fetch_image_to_temp(url: str) -> str:
    """Download an image from a URL to a temp file, return the path."""
    import tempfile, urllib.parse
    ext = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(resp.content)
    tmp.close()
    return tmp.name


def _upload_image(access_token: str, author_urn: str, image_path: str) -> str:
    """Upload an image to LinkedIn and return the asset URN."""
    # Step 1: register the upload
    resp = requests.post(
        f"{API_BASE}/v2/assets?action=registerUpload",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": author_urn,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        },
    )
    resp.raise_for_status()
    data = resp.json()
    upload_url = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn = data["value"]["asset"]

    # Step 2: upload the image bytes
    with open(image_path, "rb") as f:
        image_data = f.read()

    put_resp = requests.put(
        upload_url,
        data=image_data,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    put_resp.raise_for_status()

    return asset_urn


def _ugc_post_with_image(
    access_token: str, author_urn: str, text: str, visibility: str, asset_urn: str
) -> requests.Response:
    return requests.post(
        f"{API_BASE}/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "IMAGE",
                    "media": [
                        {
                            "status": "READY",
                            "description": {"text": ""},
                            "media": asset_urn,
                            "title": {"text": ""},
                        }
                    ],
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": visibility
            },
        },
    )


# ── MCP tools ──────────────────────────────────────────────────────────────
@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="authenticate",
            description=(
                "Complete LinkedIn OAuth. Opens a browser window for you to log in. "
                "Run this once — tokens are saved locally and refreshed automatically."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="create_post",
            description="Publish a text post to your LinkedIn feed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Post content (plain text, up to ~3000 chars).",
                    },
                    "visibility": {
                        "type": "string",
                        "enum": ["PUBLIC", "CONNECTIONS"],
                        "description": "Who can see the post. Defaults to PUBLIC.",
                        "default": "PUBLIC",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="create_post_with_image",
            description="Publish a LinkedIn post with an image. Accepts a local file path or a public URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Post caption / text.",
                    },
                    "image": {
                        "type": "string",
                        "description": "Absolute local path to the image file, or a public image URL (http/https).",
                    },
                    "visibility": {
                        "type": "string",
                        "enum": ["PUBLIC", "CONNECTIONS"],
                        "default": "PUBLIC",
                    },
                },
                "required": ["text", "image"],
            },
        ),
        Tool(
            name="add_comment",
            description="Post a comment on a LinkedIn post. Use the post ID returned by create_post or create_post_with_image (the full urn:li:share:... string).",
            inputSchema={
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "string",
                        "description": "The post URN, e.g. urn:li:share:1234567890",
                    },
                    "text": {
                        "type": "string",
                        "description": "The comment text.",
                    },
                },
                "required": ["post_id", "text"],
            },
        ),
        Tool(
            name="create_post_with_link",
            description=(
                "Publish a LinkedIn post and immediately add the link as the first comment. "
                "Use this instead of putting the link in the post body — LinkedIn's algorithm "
                "reduces reach for posts that contain links."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Post body text (no link here).",
                    },
                    "link": {
                        "type": "string",
                        "description": "The URL to add as the first comment.",
                    },
                    "visibility": {
                        "type": "string",
                        "enum": ["PUBLIC", "CONNECTIONS"],
                        "default": "PUBLIC",
                    },
                },
                "required": ["text", "link"],
            },
        ),
        Tool(
            name="list_my_posts",
            description="List your recent LinkedIn posts with their IDs. Useful for finding post IDs to comment on.",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent posts to return (default 10, max 20).",
                        "default": 10,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="get_profile",
            description="Return your LinkedIn display name and person ID.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global _auth_code, _auth_error, _auth_event

    # ── authenticate ──────────────────────────────────────────────────────
    if name == "authenticate":
        _auth_code = None
        _auth_error = None
        _auth_event.clear()

        threading.Thread(target=_run_callback_server, daemon=True).start()

        url = f"{AUTH_URL}?" + urlencode({
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": "cowork_linkedin",
        })
        webbrowser.open(url)

        if not _auth_event.wait(timeout=120):
            return [TextContent(type="text", text="Timed out waiting for LinkedIn auth. Try again.")]

        if not _auth_code:
            return [TextContent(type="text", text=f"Auth failed — {_auth_error or 'no code received'}")]

        resp = requests.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": _auth_code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        resp.raise_for_status()
        tokens = resp.json()
        save_tokens(tokens)

        return [TextContent(type="text", text=(
            "✓ Authenticated successfully! Tokens saved.\n"
            f"Access token expires in: {tokens.get('expires_in', '?')} seconds (~60 days)."
        ))]

    # ── create_post ───────────────────────────────────────────────────────
    elif name == "create_post":
        text = arguments["text"]
        visibility = arguments.get("visibility", "PUBLIC")

        access_token = get_valid_token()
        person_id = get_person_id(access_token)
        author_urn = f"urn:li:person:{person_id}"

        resp = _ugc_post(access_token, author_urn, text, visibility)

        if resp.status_code == 401:
            access_token = refresh_if_needed(access_token)
            resp = _ugc_post(access_token, author_urn, text, visibility)

        if resp.status_code in (200, 201):
            post_id = resp.headers.get("x-restli-id", resp.headers.get("X-RestLi-Id", "unknown"))
            return [TextContent(type="text", text=f"✓ Post published! ID: {post_id}")]
        else:
            return [TextContent(type="text", text=f"Error {resp.status_code}: {resp.text}")]

    # ── create_post_with_image ────────────────────────────────────────────
    elif name == "create_post_with_image":
        text = arguments["text"]
        image = arguments["image"]
        visibility = arguments.get("visibility", "PUBLIC")

        # resolve URL vs local path
        tmp_path = None
        if image.startswith("http://") or image.startswith("https://"):
            try:
                image_path = _fetch_image_to_temp(image)
                tmp_path = image_path
            except Exception as e:
                return [TextContent(type="text", text=f"Failed to download image: {e}")]
        else:
            image_path = image
            if not Path(image_path).exists():
                return [TextContent(type="text", text=f"Image not found: {image_path}")]

        access_token = get_valid_token()
        person_id = get_person_id(access_token)
        author_urn = f"urn:li:person:{person_id}"

        try:
            asset_urn = _upload_image(access_token, author_urn, image_path)
        except Exception as e:
            return [TextContent(type="text", text=f"Image upload failed: {e}")]
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

        resp = _ugc_post_with_image(access_token, author_urn, text, visibility, asset_urn)

        if resp.status_code == 401:
            access_token = refresh_if_needed(access_token)
            resp = _ugc_post_with_image(access_token, author_urn, text, visibility, asset_urn)

        if resp.status_code in (200, 201):
            post_id = resp.headers.get("x-restli-id", resp.headers.get("X-RestLi-Id", "unknown"))
            return [TextContent(type="text", text=f"✓ Post with image published! ID: {post_id}")]
        else:
            return [TextContent(type="text", text=f"Error {resp.status_code}: {resp.text}")]

    # ── add_comment ───────────────────────────────────────────────────────
    elif name == "add_comment":
        post_id = arguments["post_id"]
        text = arguments["text"]

        access_token = get_valid_token()
        person_id = get_person_id(access_token)
        actor_urn = f"urn:li:person:{person_id}"

        # encode the share URN for use in the URL path
        from urllib.parse import quote
        encoded_urn = quote(post_id, safe="")

        resp = requests.post(
            f"{API_BASE}/v2/socialActions/{encoded_urn}/comments",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "actor": actor_urn,
                "message": {"text": text},
            },
        )

        if resp.status_code == 401:
            access_token = refresh_if_needed(access_token)
            resp = requests.post(
                f"{API_BASE}/v2/socialActions/{encoded_urn}/comments",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                json={
                    "actor": actor_urn,
                    "message": {"text": text},
                },
            )

        if resp.status_code in (200, 201):
            return [TextContent(type="text", text=f"✓ Comment posted!")]
        else:
            return [TextContent(type="text", text=f"Error {resp.status_code}: {resp.text}")]

    # ── create_post_with_link ─────────────────────────────────────────────
    elif name == "create_post_with_link":
        text = arguments["text"]
        link = arguments["link"]
        visibility = arguments.get("visibility", "PUBLIC")

        access_token = get_valid_token()
        person_id = get_person_id(access_token)
        author_urn = f"urn:li:person:{person_id}"

        resp = _ugc_post(access_token, author_urn, text, visibility)

        if resp.status_code == 401:
            access_token = refresh_if_needed(access_token)
            resp = _ugc_post(access_token, author_urn, text, visibility)

        if resp.status_code not in (200, 201):
            return [TextContent(type="text", text=f"Error creating post {resp.status_code}: {resp.text}")]

        post_id = resp.headers.get("x-restli-id", resp.headers.get("X-RestLi-Id", ""))
        if not post_id:
            return [TextContent(type="text", text="Post published but couldn't retrieve post ID to add comment.")]

        # Post the link as first comment
        from urllib.parse import quote
        encoded_urn = quote(post_id, safe="")

        comment_resp = requests.post(
            f"{API_BASE}/v2/socialActions/{encoded_urn}/comments",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "actor": author_urn,
                "message": {"text": link},
            },
        )

        if comment_resp.status_code in (200, 201):
            return [TextContent(type="text", text=(
                f"✓ Post published and link added as first comment!\n"
                f"Post ID: {post_id}"
            ))]
        else:
            return [TextContent(type="text", text=(
                f"✓ Post published (ID: {post_id}) but failed to add link as comment "
                f"({comment_resp.status_code}): {comment_resp.text}"
            ))]

    # ── list_my_posts ─────────────────────────────────────────────────────
    elif name == "list_my_posts":
        count = min(int(arguments.get("count", 10)), 20)

        access_token = get_valid_token()
        person_id = get_person_id(access_token)
        author_urn = f"urn:li:person:{person_id}"

        resp = requests.get(
            f"{API_BASE}/v2/ugcPosts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            params={
                "q": "authors",
                "authors": f"List({author_urn})",
                "count": count,
                "sortBy": "LAST_MODIFIED",
            },
        )

        if resp.status_code == 401:
            access_token = refresh_if_needed(access_token)
            resp = requests.get(
                f"{API_BASE}/v2/ugcPosts",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                params={
                    "q": "authors",
                    "authors": f"List({author_urn})",
                    "count": count,
                    "sortBy": "LAST_MODIFIED",
                },
            )

        if resp.status_code not in (200, 201):
            return [TextContent(type="text", text=f"Error {resp.status_code}: {resp.text}")]

        elements = resp.json().get("elements", [])
        if not elements:
            return [TextContent(type="text", text="No posts found.")]

        lines = []
        for el in elements:
            post_id = el.get("id", "unknown")
            content = el.get("specificContent", {})
            share = content.get("com.linkedin.ugc.ShareContent", {})
            snippet = share.get("shareCommentary", {}).get("text", "")[:80]
            if len(share.get("shareCommentary", {}).get("text", "")) > 80:
                snippet += "…"
            lines.append(f"ID: {post_id}\n    {snippet}")

        return [TextContent(type="text", text="\n\n".join(lines))]

    # ── get_profile ───────────────────────────────────────────────────────
    elif name == "get_profile":
        access_token = get_valid_token()
        resp = requests.get(
            f"{API_BASE}/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        info = resp.json()
        return [TextContent(type="text", text=(
            f"Name: {info.get('name', '?')}\n"
            f"Person ID: {info.get('sub', '?')}\n"
            f"Email: {info.get('email', 'n/a')}"
        ))]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ── entrypoint ─────────────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
