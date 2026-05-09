# LinkedIn Connector for Claude Cowork

Post to LinkedIn directly from a Cowork window. Supports text posts and image posts.

---

## Prerequisites

- [Claude desktop app](https://claude.ai/download) with Cowork mode
- Python 3.10+ with `pip`
- A LinkedIn account

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/linkedin-connector.git
cd linkedin-connector
```

---

## Step 2 — Install dependencies

```bash
pip install mcp requests
```

Verify it worked:

```bash
python3 -c "import mcp, requests; print('ok')"
```

---

## Step 3 — Create a LinkedIn Developer app

1. Go to [linkedin.com/developers/apps/new](https://www.linkedin.com/developers/apps/new)
2. Fill in app name (anything), select the default **LinkedIn** company page
3. Once created, go to the **Products** tab and add:
   - **Sign In with LinkedIn using OpenID Connect**
   - **Share on LinkedIn**
4. Go to the **Auth** tab and note your **Client ID** and **Client Secret**
5. Under **OAuth 2.0 settings**, add this redirect URL: `http://localhost:8000/callback`

---

## Step 4 — Configure credentials

Copy the example config and fill in your credentials:

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "redirect_uri": "http://localhost:8000/callback",
  "scopes": "openid profile w_member_social"
}
```

---

## Step 5 — Register with Claude

Run the setup script:

```bash
python3 add_mcp_config.py
```

This writes the server entry into Claude's config file automatically.

---

## Step 6 — Restart Claude

Quit and reopen the Claude desktop app. The LinkedIn connector will appear as connected.

---

## Step 7 — Authenticate

In any Cowork session, ask Claude:

> "Authenticate my LinkedIn connector"

A browser window will open. Log in and approve access. You'll see a confirmation page. This only needs to be done once — the token is saved locally and refreshes automatically.

---

## Usage

Once authenticated, you can just talk to Claude naturally:

> "Post this to my LinkedIn: [your text]"

> "Post this to LinkedIn with the image at /Users/you/Downloads/photo.png"

> "Post this to LinkedIn with this image: https://example.com/image.jpg"

**Note on images:** the image must be either a local file path on your computer or a public URL. Images dropped directly into the Cowork chat are not automatically saved to disk — tell Claude where the file is, or paste a URL.

### Available tools

| Tool | What it does |
|------|-------------|
| `authenticate` | One-time OAuth login via browser |
| `create_post` | Publish a text post |
| `create_post_with_image` | Publish a post with an image (local file path or public URL) |
| `get_profile` | Check which LinkedIn account is connected |

---

## Files

```
linkedin-connector/
├── server.py              # MCP server
├── config.json            # Your credentials (git-ignored)
├── config.example.json    # Template
├── add_mcp_config.py      # Registers the server with Claude
├── requirements.txt       # Python dependencies
└── README.md
```

---

## Notes

- Your credentials and tokens are stored locally and never leave your machine
- Access tokens expire after 60 days and refresh automatically
- Each user needs their own LinkedIn Developer app (free, takes ~5 minutes)
