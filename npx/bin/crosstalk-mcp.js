#!/usr/bin/env node
"use strict";

/**
 * crosstalk-mcp launcher.
 *
 * Two ways to boot the relay:
 *   crosstalk-mcp python   -> run the bundled Python edition (needs python3 on the host)
 *   crosstalk-mcp docker   -> run the published GHCR image     (needs docker on the host)
 *
 * The relay is configured via environment variables (RELAY_TOKEN, RELAY_PARTICIPANTS,
 * PORT, HOST, RELAY_DB). Convenience flags below map onto those env vars for either command.
 */

const fs = require("fs");
const path = require("path");
const { spawn, spawnSync } = require("child_process");

const PKG = require("../package.json");
const DEFAULT_IMAGE = "ghcr.io/humbre-tonto/crosstalk-mcp-python:latest";

// ----- arg parsing -----
// Known flags map to env vars; everything the relay reads from the environment.
const FLAG_TO_ENV = {
  "--port": "PORT",
  "-p": "PORT",
  "--host": "HOST",
  "--token": "RELAY_TOKEN",
  "--participants": "RELAY_PARTICIPANTS",
  "--db": "RELAY_DB",
};

function parseArgs(argv) {
  const out = { command: null, image: null, env: {}, help: false, version: false };
  const rest = argv.slice();
  while (rest.length) {
    const arg = rest.shift();
    if (arg === "python" || arg === "docker") {
      out.command = arg;
    } else if (arg === "help" || arg === "--help" || arg === "-h") {
      out.help = true;
    } else if (arg === "--version" || arg === "-v") {
      out.version = true;
    } else if (arg === "--image") {
      out.image = rest.shift();
    } else if (FLAG_TO_ENV[arg]) {
      out.env[FLAG_TO_ENV[arg]] = rest.shift();
    } else if (arg.startsWith("--") && arg.includes("=")) {
      const [k, v] = arg.split(/=(.*)/s);
      if (FLAG_TO_ENV[k]) out.env[FLAG_TO_ENV[k]] = v;
      else if (k === "--image") out.image = v;
    } else {
      console.error(`crosstalk-mcp: ignoring unrecognized argument "${arg}"`);
    }
  }
  return out;
}

const USAGE = `crosstalk-mcp ${PKG.version} - launcher for the crosstalk relay

Usage:
  npx crosstalk-mcp <command> [options]

Commands:
  python            Run the bundled Python edition (requires python3 on the host).
  docker            Run the published Docker image (requires docker on the host).
  help              Show this help.

Options (apply to both commands; also settable via environment):
  -p, --port <n>          Port to listen on           (env PORT, default 8765)
      --host <addr>       Bind address                (env HOST, default 0.0.0.0)
      --token <tok>       Shared bearer token         (env RELAY_TOKEN)
      --participants <m>  Per-participant tokens      (env RELAY_PARTICIPANTS, "id:tok,id2:tok2")
      --db <path>         SQLite db path              (env RELAY_DB, default relay.db)
      --image <ref>       (docker only) image to run  (default ${DEFAULT_IMAGE})
  -v, --version           Print version.

Examples:
  npx crosstalk-mcp python --port 8765 --token s3cret
  npx crosstalk-mcp docker --port 9000 --participants "humanX:tokX,humanY:tokY"
`;

// ----- helpers -----
function which(candidates, versionArgs) {
  for (const cmd of candidates) {
    try {
      const r = spawnSync(cmd, versionArgs, { stdio: "ignore" });
      if (r.status === 0) return cmd;
    } catch (_) {
      /* keep trying */
    }
  }
  return null;
}

function resolvePythonDir() {
  // (1) bundled copy inside the published package; (2) sibling dir when run from a repo clone.
  const candidates = [
    path.join(__dirname, "..", "python"),
    path.join(__dirname, "..", "..", "python"),
  ];
  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, "crosstalk_mcp.py"))) return dir;
  }
  return null;
}

function forwardSignals(child) {
  const relay = (sig) => {
    if (!child.killed) child.kill(sig);
  };
  process.on("SIGINT", relay);
  process.on("SIGTERM", relay);
  child.on("exit", (code, signal) => {
    process.exit(signal ? 1 : code == null ? 0 : code);
  });
}

function ensurePythonDeps(py, pyDir) {
  const check = spawnSync(py, ["-c", "import mcp, uvicorn, starlette"], { stdio: "ignore" });
  if (check.status === 0) return true;
  console.error("crosstalk-mcp: installing Python dependencies (mcp, uvicorn)...");
  const reqs = path.join(pyDir, "requirements.txt");
  const args = fs.existsSync(reqs)
    ? ["-m", "pip", "install", "-r", reqs]
    : ["-m", "pip", "install", "mcp>=1.9.0", "uvicorn>=0.30"];
  const inst = spawnSync(py, args, { stdio: "inherit" });
  if (inst.status !== 0) {
    console.error(
      "crosstalk-mcp: could not install Python dependencies automatically.\n" +
        "  Install them yourself (ideally in a virtualenv) and re-run, e.g.:\n" +
        `    ${py} -m pip install mcp uvicorn`
    );
    return false;
  }
  return true;
}

// ----- commands -----
function runPython(env) {
  const pyDir = resolvePythonDir();
  if (!pyDir) {
    console.error("crosstalk-mcp: could not locate the bundled Python source (crosstalk_mcp.py).");
    process.exit(1);
  }
  const py = which(["python3", "python"], ["--version"]);
  if (!py) {
    console.error("crosstalk-mcp: python3 not found on PATH. Install Python 3.10+ or use the docker command.");
    process.exit(1);
  }
  if (!ensurePythonDeps(py, pyDir)) process.exit(1);
  const script = path.join(pyDir, "crosstalk_mcp.py");
  console.error(`crosstalk-mcp: starting Python relay on port ${env.PORT || "8765"} ...`);
  const child = spawn(py, [script], { stdio: "inherit", env });
  forwardSignals(child);
}

function runDocker(env, image) {
  const docker = which(["docker"], ["version", "--format", "{{.Client.Version}}"]);
  if (!docker) {
    console.error("crosstalk-mcp: docker not found on PATH. Install Docker or use the python command.");
    process.exit(1);
  }
  const img = image || env.CROSSTALK_IMAGE || DEFAULT_IMAGE;
  const port = env.PORT || "8765";
  const args = ["run", "--rm", "-i", "-p", `${port}:${port}`];
  // Pass through only the relay's env vars. HOST is intentionally omitted so the container
  // keeps binding 0.0.0.0 (required for the published port to be reachable).
  for (const k of ["RELAY_TOKEN", "RELAY_PARTICIPANTS", "RELAY_DB", "PORT"]) {
    if (env[k] !== undefined && env[k] !== "") args.push("-e", `${k}=${env[k]}`);
  }
  args.push(img);
  console.error(`crosstalk-mcp: docker run ${img} (port ${port}) ...`);
  const child = spawn(docker, args, { stdio: "inherit" });
  forwardSignals(child);
}

// ----- main -----
function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.version) {
    console.log(PKG.version);
    return;
  }
  if (opts.help || !opts.command) {
    console.log(USAGE);
    process.exit(opts.command ? 0 : opts.help ? 0 : 1);
  }
  const env = Object.assign({}, process.env, opts.env);
  if (opts.command === "python") runPython(env);
  else if (opts.command === "docker") runDocker(env, opts.image);
}

main();
