# SmartAssist Publishing Plan

Domain: smartassist-memory.com (Hostinger)
Package: smartassist
Repo: github.com/jnrahme/SmartAssist (private after publish)

---

## Goal

Users install SmartAssist with one command. No source code visible. Professional CLI tool experience.

```bash
# macOS
brew install smartassist-memory/tap/smartassist

# Any platform
curl -fsSL https://smartassist-memory.com/install | sh

# Then
cd my-project && smartassist setup
```

---

## Phase 1: Compile to Native Binary

### Why Nuitka over PyInstaller

| | Nuitka | PyInstaller |
|---|---|---|
| Output | Native C binary | Bundled Python + bytecode |
| Reverse engineering | Very hard (compiled C) | Easy (extract .pyc files) |
| Startup time | ~100ms (native) | ~2-3s (Python bootstrap) |
| File size | 50-100MB | 100-200MB |
| Source protection | Strong | Weak |

### Build Matrix

| Platform | Architecture | Binary name | Build on |
|---|---|---|---|
| macOS | ARM64 (Apple Silicon) | smartassist-darwin-arm64 | GitHub Actions macos-14 |
| macOS | x86_64 (Intel) | smartassist-darwin-amd64 | GitHub Actions macos-13 |
| Linux | x86_64 | smartassist-linux-amd64 | GitHub Actions ubuntu-latest |
| Linux | ARM64 | smartassist-linux-arm64 | GitHub Actions ubuntu-latest (cross-compile) |

### Build Command

```bash
pip install nuitka
python -m nuitka \
  --standalone \
  --onefile \
  --output-filename=smartassist \
  --include-package=smartassist \
  --include-package=mcp \
  --include-package=sentence_transformers \
  --include-package=lancedb \
  --nofollow-import-to=tests \
  smartassist/cli.py
```

### GitHub Actions Workflow

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: macos-14
            target: darwin-arm64
          - os: macos-13
            target: darwin-amd64
          - os: ubuntu-latest
            target: linux-amd64
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install nuitka .
      - run: python -m nuitka --standalone --onefile --output-filename=smartassist-${{ matrix.target }} smartassist/cli.py
      - uses: actions/upload-artifact@v4
        with:
          name: smartassist-${{ matrix.target }}
          path: smartassist-${{ matrix.target }}

  release:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            smartassist-darwin-arm64/smartassist-darwin-arm64
            smartassist-darwin-amd64/smartassist-darwin-amd64
            smartassist-linux-amd64/smartassist-linux-amd64
```

### Dependency Consideration

sentence-transformers and lancedb include native extensions. Two approaches:

**Option A: Bundle everything (larger binary, ~200-500MB)**
- Include all ML dependencies in the binary
- User downloads once, everything works offline
- First `rag_search` call still downloads the bge-m3 model (~400MB) from Hugging Face

**Option B: Thin binary + runtime download (smaller binary, ~50MB)**
- Binary includes SmartAssist core only
- On first `smartassist setup`, download ML dependencies
- Faster initial install, slower first use

**Recommendation:** Option B — thin binary. The ML models are only needed for `rag_search` (the MCP semantic path), not for the hook injection path. Most of the value works without them. Download lazily on first MCP server start.

---

## Phase 2: Distribution Channels

### 2.1 Homebrew Tap (macOS — primary)

Create repo: `github.com/smartassist-memory/homebrew-tap`

```ruby
# Formula/smartassist.rb
class Smartassist < Formula
  desc "AI memory system that learns from developer feedback"
  homepage "https://smartassist-memory.com"
  version "1.1.0"

  on_macos do
    on_arm do
      url "https://github.com/jnrahme/SmartAssist/releases/download/v1.1.0/smartassist-darwin-arm64"
      sha256 "HASH_HERE"
    end
    on_intel do
      url "https://github.com/jnrahme/SmartAssist/releases/download/v1.1.0/smartassist-darwin-amd64"
      sha256 "HASH_HERE"
    end
  end

  on_linux do
    url "https://github.com/jnrahme/SmartAssist/releases/download/v1.1.0/smartassist-linux-amd64"
    sha256 "HASH_HERE"
  end

  def install
    bin.install "smartassist-#{OS.mac? ? "darwin" : "linux"}-#{Hardware::CPU.arm? ? "arm64" : "amd64"}" => "smartassist"
  end

  test do
    assert_match "smartassist", shell_output("#{bin}/smartassist version")
  end
end
```

Install: `brew install smartassist-memory/tap/smartassist`
Update: `brew upgrade smartassist`

### 2.2 Install Script (cross-platform)

Host at `https://smartassist-memory.com/install`

```bash
#!/bin/sh
set -e

VERSION="1.1.0"
REPO="jnrahme/SmartAssist"

# Detect OS and architecture
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

case "$OS" in
  darwin) OS="darwin" ;;
  linux)  OS="linux" ;;
  *)      echo "Unsupported OS: $OS"; exit 1 ;;
esac

case "$ARCH" in
  x86_64|amd64)  ARCH="amd64" ;;
  arm64|aarch64) ARCH="arm64" ;;
  *)             echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

BINARY="smartassist-${OS}-${ARCH}"
URL="https://github.com/${REPO}/releases/download/v${VERSION}/${BINARY}"
INSTALL_DIR="/usr/local/bin"

echo "Installing SmartAssist ${VERSION} (${OS}/${ARCH})..."
curl -fsSL "$URL" -o "${INSTALL_DIR}/smartassist"
chmod +x "${INSTALL_DIR}/smartassist"

echo "SmartAssist installed successfully!"
echo ""
echo "Get started:"
echo "  cd your-project"
echo "  smartassist setup"
```

Install: `curl -fsSL https://smartassist-memory.com/install | sh`

### 2.3 npm Wrapper (for Node.js developers)

Create `@smartassist-memory/cli` on npm — a thin wrapper that downloads the native binary on postinstall:

```json
{
  "name": "@smartassist-memory/cli",
  "version": "1.1.0",
  "bin": { "smartassist": "./bin/smartassist" },
  "scripts": {
    "postinstall": "node scripts/download-binary.js"
  }
}
```

Install: `npm install -g @smartassist-memory/cli`

This is how Claude Code and Codex distribute — npm package that wraps a native binary.

### 2.4 pip/pipx (keep as fallback for Python developers)

Keep the existing PyPI path for developers who prefer Python tooling. This DOES expose source but serves a different audience.

Install: `pipx install smartassist`

---

## Phase 3: Website (smartassist-memory.com)

### Structure

```
website/
├── index.html          # Landing page
├── docs/
│   ├── index.html      # Getting started
│   ├── how-it-works.html  # Adapted from smartassist-overview.html
│   ├── cli.html        # CLI reference
│   └── mcp-tools.html  # MCP tool reference
├── install             # Install script (shell, no extension)
├── assets/
│   ├── css/
│   ├── js/
│   └── images/
├── robots.txt
└── sitemap.xml
```

### Landing Page Content

```
SmartAssist Memory
An AI that gets smarter every project.

SmartAssist learns from your feedback and corrections.
Next time, it injects the right lessons before you even ask.

[Install for macOS]  [Install for Linux]  [How It Works]

---

How it works:
1. You give feedback → :) or :(
2. SmartAssist creates a lesson (using your AI agent's full context)
3. Thompson Sampling learns which lessons help
4. Next prompt → the best lessons are injected automatically
5. Your AI agent gets it right the first time

---

Works with Claude Code and Codex.
One install. Any project. Gets smarter every day.
```

### Hosting on Hostinger VPS

```nginx
# /etc/nginx/sites-available/smartassist-memory.com
server {
    listen 80;
    server_name smartassist-memory.com www.smartassist-memory.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name smartassist-memory.com www.smartassist-memory.com;

    ssl_certificate /etc/letsencrypt/live/smartassist-memory.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/smartassist-memory.com/privkey.pem;

    root /var/www/smartassist-memory.com;
    index index.html;

    # Install script (no .sh extension — clean URL)
    location = /install {
        default_type text/plain;
        alias /var/www/smartassist-memory.com/install;
    }

    # Docs
    location /docs {
        try_files $uri $uri/ $uri.html =404;
    }

    # Cache static assets
    location ~* \.(css|js|png|jpg|svg|woff2)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        try_files $uri $uri/ =404;
    }
}
```

### SSL Setup

```bash
# On Hostinger VPS
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d smartassist-memory.com -d www.smartassist-memory.com
```

### Auto-Deploy from GitHub

```yaml
# .github/workflows/deploy-website.yml
name: Deploy Website
on:
  push:
    paths: ['website/**']
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /var/www/smartassist-memory.com
            git pull origin main
            # Or rsync from GitHub Actions runner
```

---

## Phase 4: License & Access Control

### License Options

| License | Source visible? | Free use? | Commercial use? |
|---|---|---|---|
| BUSL-1.1 (current) | Yes (on GitHub) | Yes (non-commercial) | No (until change date) |
| Proprietary + compiled binary | No | Free tier possible | Paid tier |
| MIT/Apache | Yes | Yes | Yes |

**Recommendation:** Keep BUSL-1.1 on the source repo (stays private). Distribute compiled binaries with a proprietary EULA for the binary. This gives you:
- Source stays private (repo is private)
- Binary is free to use for individuals
- Commercial/team use requires a license (future monetization)

### Optional: License Key System

For future monetization — add a simple key check:
```python
# On first run
def check_license():
    key = load_key()  # from ~/.smartassist/license.key
    if key is None:
        # Free tier: 100 lessons, 1 project
        return FreeTier()
    if validate_key(key):  # check against your API
        return ProTier()
```

This is NOT needed for launch. Build it when you have users asking for team features.

---

## Phase 5: Launch Checklist

### Before Tagging v1.1.0

- [ ] All 418 tests pass
- [ ] `smartassist doctor` reports "ready" on fresh install
- [ ] MCP tools verified live (rag_search, create_lesson, rag_dashboard)
- [ ] Thompson reranking working (verified in this session)
- [ ] MemAlign dual-memory injection working (verified in this session)
- [ ] Hybrid search + cross-encoder restored (verified in this session)
- [ ] No bt-mobile-app-specific content in seed lessons
- [ ] README updated with current install instructions
- [ ] BOMBPLAN.md NOT included in binary (dev docs stay in repo only)

### Build Pipeline

- [ ] Set up Nuitka build in GitHub Actions (release.yml)
- [ ] Test compiled binary on macOS ARM, macOS Intel, Linux x64
- [ ] Verify all entry points work from compiled binary
- [ ] Verify MCP server starts from compiled binary

### Distribution

- [ ] Create `smartassist-memory/homebrew-tap` repo
- [ ] Write Homebrew formula
- [ ] Host install script on smartassist-memory.com
- [ ] Test `brew install` end-to-end
- [ ] Test `curl | sh` end-to-end

### Website

- [ ] Build landing page from existing smartassist-overview.html
- [ ] Set up Nginx on Hostinger VPS
- [ ] Configure SSL with Let's Encrypt
- [ ] Deploy and verify smartassist-memory.com resolves
- [ ] Host install script at smartassist-memory.com/install
- [ ] Set up GitHub Actions auto-deploy for website changes

### Announce

- [ ] GitHub Release with changelog
- [ ] README badges (version, platform support)
- [ ] Tweet / post with demo GIF showing the feedback → lesson → injection loop

---

## Timeline

| Week | Milestone |
|---|---|
| Week 1 | Nuitka build pipeline + test compiled binaries |
| Week 1 | Website landing page + Nginx setup on VPS |
| Week 2 | Homebrew tap + install script |
| Week 2 | End-to-end testing on all platforms |
| Week 3 | Launch v1.1.0 — tag, build, release, announce |
