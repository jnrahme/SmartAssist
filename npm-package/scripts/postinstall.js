#!/usr/bin/env node
/**
 * SmartAssist postinstall — downloads the correct native binary for the platform.
 *
 * This is the same pattern used by Claude Code (@anthropic-ai/claude-code)
 * and Codex (@openai/codex) for distributing compiled binaries via npm.
 */

const fs = require("fs");
const path = require("path");
const https = require("https");
const { execSync } = require("child_process");

const VERSION = require("../package.json").version;
const REPO = "jnrahme/SmartAssist";
const BIN_DIR = path.join(__dirname, "..", "bin");

// All entry points that need to be created
const ENTRY_POINTS = [
  "smartassist",
  "claude-sa",
  "smartassist-prompt-inject",
  "smartassist-session-start",
  "smartassist-session-end",
  "smartassist-commit-hook",
  "smartassist-show-lessons",
  "smartassist-monitor",
];

function getPlatform() {
  const os = process.platform;
  const arch = process.arch;

  const osMap = { darwin: "darwin", linux: "linux" };
  const archMap = { x64: "amd64", arm64: "arm64" };

  const mappedOs = osMap[os];
  const mappedArch = archMap[arch];

  if (!mappedOs || !mappedArch) {
    console.error(`Unsupported platform: ${os}/${arch}`);
    console.error("SmartAssist supports: macOS (ARM64/x64), Linux (x64/ARM64)");
    process.exit(1);
  }

  return `${mappedOs}-${mappedArch}`;
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const follow = (url, redirects = 0) => {
      if (redirects > 5) return reject(new Error("Too many redirects"));

      https
        .get(url, (res) => {
          if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
            return follow(res.headers.location, redirects + 1);
          }
          if (res.statusCode !== 200) {
            return reject(new Error(`Download failed: HTTP ${res.statusCode} from ${url}`));
          }
          const file = fs.createWriteStream(dest);
          res.pipe(file);
          file.on("finish", () => file.close(resolve));
          file.on("error", reject);
        })
        .on("error", reject);
    };
    follow(url);
  });
}

async function main() {
  const platform = getPlatform();
  const binaryName = `smartassist-${platform}`;
  const url = `https://github.com/${REPO}/releases/download/v${VERSION}/${binaryName}`;
  const mainBinary = path.join(BIN_DIR, "smartassist-binary");

  // Ensure bin directory exists
  fs.mkdirSync(BIN_DIR, { recursive: true });

  console.log(`SmartAssist v${VERSION}`);
  console.log(`Downloading binary for ${platform}...`);

  try {
    await download(url, mainBinary);
    fs.chmodSync(mainBinary, 0o755);
    console.log("Binary downloaded successfully.");
  } catch (err) {
    console.error(`\nFailed to download binary: ${err.message}`);
    console.error(`\nFalling back to pipx install...`);

    // Fallback: try pipx install from PyPI or GitHub
    try {
      execSync("pipx install smartassist", { stdio: "inherit" });
      console.log("Installed via pipx as fallback.");

      // Create shims that call the pipx-installed commands
      for (const cmd of ENTRY_POINTS) {
        const shim = path.join(BIN_DIR, cmd);
        fs.writeFileSync(
          shim,
          `#!/bin/sh\nexec ${cmd} "$@"\n`,
          { mode: 0o755 }
        );
      }
      return;
    } catch {
      console.error("pipx fallback also failed.");
      console.error("Install manually: pipx install smartassist");
      process.exit(1);
    }
  }

  // Create wrapper scripts for each entry point
  // The main binary handles subcommands: smartassist-binary <entrypoint> [args...]
  for (const cmd of ENTRY_POINTS) {
    const wrapper = path.join(BIN_DIR, cmd);
    const subcommand = cmd === "smartassist" ? "cli" : cmd.replace("smartassist-", "").replace("claude-", "claude_");

    fs.writeFileSync(
      wrapper,
      `#!/bin/sh\nexec "$(dirname "$0")/smartassist-binary" ${subcommand} "$@"\n`,
      { mode: 0o755 }
    );
  }

  console.log("");
  console.log("SmartAssist installed! Get started:");
  console.log("  cd your-project");
  console.log("  smartassist setup");
  console.log("");
}

main().catch((err) => {
  console.error("Installation error:", err.message);
  process.exit(1);
});
