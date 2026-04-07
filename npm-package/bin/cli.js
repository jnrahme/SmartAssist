#!/usr/bin/env node
/**
 * SmartAssist Memory — npm entry point.
 *
 * Thin wrapper that ensures SmartAssist is installed via pipx,
 * then forwards commands to the Python CLI.
 *
 * Usage:
 *   npx smartassist-memory init          # install + setup in current project
 *   npx smartassist-memory serve         # start MCP server (for agent registration)
 *   npx smartassist-memory doctor        # check installation health
 *   npx smartassist-memory <command>     # any smartassist CLI command
 */

const { execSync, spawn } = require("child_process");
const os = require("os");

const PACKAGE = "smartassist";
const GITHUB_URL = "git+https://github.com/jnrahme/SmartAssist.git";

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, { encoding: "utf-8", stdio: "pipe", ...opts }).trim();
  } catch {
    return null;
  }
}

function isInstalled() {
  // Check if smartassist CLI is available
  const which = os.platform() === "win32" ? "where" : "which";
  return run(`${which} smartassist`) !== null;
}

function hasPipx() {
  const which = os.platform() === "win32" ? "where" : "which";
  return run(`${which} pipx`) !== null;
}

function hasPip() {
  const which = os.platform() === "win32" ? "where" : "which";
  return run(`${which} pip3`) !== null || run(`${which} pip`) !== null;
}

function install() {
  console.log("Installing SmartAssist...\n");

  if (hasPipx()) {
    console.log("Using pipx...");
    const result = run(`pipx install "${GITHUB_URL}"`, { stdio: "inherit" });
    if (result !== null || isInstalled()) {
      console.log("\nSmartAssist installed successfully via pipx.");
      return true;
    }
  }

  if (hasPip()) {
    console.log("pipx not found. Using pip3...");
    const result = run(`pip3 install "${GITHUB_URL}"`, { stdio: "inherit" });
    if (result !== null || isInstalled()) {
      console.log("\nSmartAssist installed successfully via pip3.");
      return true;
    }
  }

  console.error("\nError: Python 3.10+ with pip or pipx is required.");
  console.error("Install pipx: https://pipx.pypa.io/stable/installation/");
  console.error("Then run: npx smartassist-memory init");
  process.exit(1);
}

function forward(args) {
  const child = spawn("smartassist", args, {
    stdio: "inherit",
    shell: true,
  });
  child.on("close", (code) => process.exit(code || 0));
  child.on("error", () => {
    console.error("Error: smartassist command not found.");
    console.error("Run: npx smartassist-memory init");
    process.exit(1);
  });
}

// ── Main ─────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const command = args[0] || "help";

if (command === "init") {
  // Install if needed, then run setup
  if (!isInstalled()) {
    install();
  } else {
    console.log("SmartAssist already installed.");
  }
  // Check for --agent flag
  const agentIdx = args.indexOf("--agent");
  if (agentIdx !== -1 && args[agentIdx + 1]) {
    const agent = args[agentIdx + 1];
    console.log(`\nRegistering with ${agent}...\n`);
    forward(["setup-agent", agent]);
  } else {
    console.log("\nRunning setup...\n");
    forward(["setup"]);
  }
} else if (command === "serve") {
  // MCP server mode — used by: claude mcp add rlhf -- npx -y smartassist-memory serve
  if (!isInstalled()) {
    install();
  }
  forward(["serve"]);
} else {
  // Forward any command to smartassist CLI
  if (!isInstalled()) {
    console.error("SmartAssist is not installed. Run: npx smartassist-memory init");
    process.exit(1);
  }
  forward(args);
}
