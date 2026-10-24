#!/usr/bin/env python3
"""Scaffold a custom mope.io-style project (Node server + canvas client).

The animal tree, world size and mechanics are modelled on mope.io as it was
around 25 May 2020 and live in a single editable config.json. The generated game
is an original implementation, not a copy of mope.io's code or art.

    python3 create_mope_project.py my-mope
    python3 create_mope_project.py my-mope --name "My Mope" --port 8080
    python3 create_mope_project.py my-mope --fetch-snapshot

--fetch-snapshot downloads the Wayback Machine capture of mope.io closest to
2020-05-25 into <project>/reference/ so you can compare the animal list and
UI against the original. It is for private reference only; the folder is
git-ignored and nothing in it is used by the generated game.

Run the result with:  cd my-mope && npm install && npm start
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SNAPSHOT_DATE = "20200525"

# Approximation of the May 2020 tree. Edit freely, or compare with --fetch-snapshot.
# xp = total XP needed to evolve into this animal.
ANIMALS = [
    # land herbivores
    {"id": "mouse",     "tier": 1,  "biome": "land",  "diet": "plant", "xp": 0,       "r": 14, "speed": 5.0, "color": "#b9a38a"},
    {"id": "rabbit",    "tier": 2,  "biome": "land",  "diet": "plant", "xp": 150,     "r": 17, "speed": 5.2, "color": "#d8cfc4"},
    {"id": "pig",       "tier": 3,  "biome": "land",  "diet": "plant", "xp": 500,     "r": 21, "speed": 4.9, "color": "#f2a7b5"},
    {"id": "deer",      "tier": 4,  "biome": "land",  "diet": "plant", "xp": 1200,    "r": 25, "speed": 5.4, "color": "#b7793f"},
    {"id": "zebra",     "tier": 5,  "biome": "land",  "diet": "plant", "xp": 2500,    "r": 29, "speed": 5.6, "color": "#eeeeee"},
    # land carnivores
    {"id": "fox",       "tier": 6,  "biome": "land",  "diet": "meat",  "xp": 5000,    "r": 31, "speed": 5.7, "color": "#e0762c"},
    {"id": "wolf",      "tier": 7,  "biome": "land",  "diet": "meat",  "xp": 9000,    "r": 34, "speed": 5.8, "color": "#8c8c94"},
    {"id": "lion",      "tier": 8,  "biome": "land",  "diet": "meat",  "xp": 16000,   "r": 38, "speed": 5.8, "color": "#d9a441"},
    {"id": "grizzly",   "tier": 9,  "biome": "land",  "diet": "meat",  "xp": 28000,   "r": 43, "speed": 5.5, "color": "#6b4a2f"},
    {"id": "dragon",    "tier": 10, "biome": "land",  "diet": "meat",  "xp": 60000,   "r": 50, "speed": 5.9, "color": "#b0302a"},
    # sea
    {"id": "shrimp",    "tier": 1,  "biome": "water", "diet": "plant", "xp": 0,       "r": 13, "speed": 5.0, "color": "#f08a6c"},
    {"id": "crab",      "tier": 2,  "biome": "water", "diet": "plant", "xp": 150,     "r": 17, "speed": 4.8, "color": "#d8452f"},
    {"id": "fish",      "tier": 3,  "biome": "water", "diet": "plant", "xp": 500,     "r": 21, "speed": 5.3, "color": "#4aa3d6"},
    {"id": "jellyfish", "tier": 4,  "biome": "water", "diet": "plant", "xp": 1200,    "r": 25, "speed": 5.0, "color": "#c58be0"},
    {"id": "octopus",   "tier": 5,  "biome": "water", "diet": "meat",  "xp": 2500,    "r": 30, "speed": 5.4, "color": "#a64d79"},
    {"id": "shark",     "tier": 6,  "biome": "water", "diet": "meat",  "xp": 6000,    "r": 36, "speed": 5.9, "color": "#5d7a8c"},
    {"id": "kraken",    "tier": 7,  "biome": "water", "diet": "meat",  "xp": 20000,   "r": 46, "speed": 5.6, "color": "#3a2a6b"},
]

CONFIG = {
    "name": "{{NAME}}",
    "port": 0,
    "world": {"width": 6000, "height": 6000, "waterFraction": 0.25},
    "tickRate": 30,
    "sendRate": 20,
    "view": 1100,
    "start": {"land": "mouse", "water": "shrimp"},
    "food": {"plant": 450, "meat": 0, "xp": 12},
    "thirst": {"drainPerSec": 3.0, "sprintMultiplier": 2.0, "drinkPerSec": 25.0},
    "sprintSpeed": 1.6,
    "killXpShare": 0.35,
    "animals": ANIMALS,
}

SERVER_JS = r'''const http = require("http");
const fs = require("fs");
const path = require("path");
const { WebSocketServer } = require("ws");
const cfg = require("./config.json");

const PORT = process.env.PORT || cfg.port;
const { width: W, height: H, waterFraction } = cfg.world;
const waterY = H * (1 - waterFraction);
const animals = Object.fromEntries(cfg.animals.map((a) => [a.id, a]));
const chain = (biome) => cfg.animals.filter((a) => a.biome === biome).sort((a, b) => a.xp - b.xp);

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
const server = http.createServer((req, res) => {
  const url = req.url === "/" ? "/index.html" : req.url.split("?")[0];
  const file = path.join(__dirname, "public", path.normalize(url).replace(/^(\.\.[\/\\])+/, ""));
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end("not found"); }
    res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
    res.end(data);
  });
});
const wss = new WebSocketServer({ server });

let nextId = 1;
const players = new Map();
let foods = [];

const rand = (a, b) => a + Math.random() * (b - a);
function spawnFood(kind, x, y, xp) {
  foods.push({ id: nextId++, kind, x: x ?? rand(0, W), y: y ?? rand(0, H), xp: xp ?? cfg.food.xp });
}
for (let i = 0; i < cfg.food.plant; i++) spawnFood("plant");

function spawn(p, biome) {
  const a = animals[cfg.start[biome]];
  p.animal = a.id; p.xp = 0; p.thirst = 100; p.alive = true; p.sprint = false;
  p.x = rand(200, W - 200);
  p.y = biome === "water" ? rand(waterY + 100, H - 100) : rand(200, waterY - 200);
}

function nextEvolution(p) {
  const cur = animals[p.animal];
  return chain(cur.biome).find((a) => a.xp > cur.xp && p.xp >= a.xp) || null;
}

function die(p, killer) {
  const a = animals[p.animal];
  if (killer) killer.xp += Math.floor(p.xp * cfg.killXpShare);
  for (let i = 0; i < 3 + a.tier; i++) {
    spawnFood("meat", p.x + rand(-a.r, a.r), p.y + rand(-a.r, a.r), Math.max(10, (p.xp * 0.5) / (3 + a.tier)));
  }
  p.alive = false;
  p.ws.send(JSON.stringify({ t: "dead", xp: Math.floor(p.xp) }));
}

wss.on("connection", (ws) => {
  const p = { id: nextId++, ws, name: "", alive: false, angle: 0, power: 0 };
  players.set(p.id, p);
  ws.on("message", (raw) => {
    let m; try { m = JSON.parse(raw); } catch { return; }
    if (m.t === "join" && !p.alive) {
      p.name = String(m.name || "mope").slice(0, 16);
      spawn(p, m.biome === "water" ? "water" : "land");
      ws.send(JSON.stringify({ t: "init", id: p.id, world: cfg.world, animals: cfg.animals, name: cfg.name }));
    } else if (m.t === "move" && p.alive) {
      p.angle = +m.angle || 0;
      p.power = Math.min(1, Math.max(0, +m.power || 0));
      p.sprint = !!m.sprint;
    } else if (m.t === "evolve" && p.alive) {
      const next = nextEvolution(p);
      if (next) p.animal = next.id;
    }
  });
  ws.on("close", () => players.delete(p.id));
});

const dt = 1 / cfg.tickRate;
setInterval(() => {
  const live = [...players.values()].filter((p) => p.alive);
  for (const p of live) {
    const a = animals[p.animal];
    const inWater = p.y > waterY;
    const home = (a.biome === "water") === inWater;
    let speed = a.speed * 60 * p.power * (p.sprint ? cfg.sprintSpeed : 1) * (home ? 1 : 0.55);
    p.x = Math.min(W, Math.max(0, p.x + Math.cos(p.angle) * speed * dt));
    p.y = Math.min(H, Math.max(0, p.y + Math.sin(p.angle) * speed * dt));

    // thirst: land animals drink in water, sea animals suffocate on land
    if (a.biome === "land") {
      p.thirst += (inWater ? cfg.thirst.drinkPerSec : -cfg.thirst.drainPerSec * (p.sprint && p.power > 0 ? cfg.thirst.sprintMultiplier : 1)) * dt;
    } else {
      p.thirst += (inWater ? cfg.thirst.drinkPerSec : -cfg.thirst.drainPerSec * 3) * dt;
    }
    p.thirst = Math.min(100, p.thirst);
    if (p.thirst <= 0) { die(p, null); continue; }

    // eating
    for (let i = foods.length - 1; i >= 0; i--) {
      const f = foods[i];
      if (f.kind === "meat" && a.diet !== "meat") continue;
      if (f.kind === "plant" && a.diet === "meat") continue;
      if (Math.hypot(f.x - p.x, f.y - p.y) < a.r + 8) {
        p.xp += f.xp; foods.splice(i, 1);
        if (f.kind === "plant") spawnFood("plant");
      }
    }
  }
  // predation: carnivores eat strictly lower tiers they overlap
  for (const p of live) {
    if (!p.alive) continue;
    const a = animals[p.animal];
    if (a.diet !== "meat") continue;
    for (const q of live) {
      if (q === p || !q.alive) continue;
      const b = animals[q.animal];
      if (b.tier < a.tier && Math.hypot(p.x - q.x, p.y - q.y) < a.r) die(q, p);
    }
  }
}, 1000 / cfg.tickRate);

setInterval(() => {
  const live = [...players.values()].filter((p) => p.alive);
  for (const p of live) {
    const view = cfg.view;
    const near = (o) => Math.abs(o.x - p.x) < view && Math.abs(o.y - p.y) < view;
    const msg = {
      t: "state",
      you: { x: p.x, y: p.y, xp: Math.floor(p.xp), thirst: p.thirst, animal: p.animal, canEvolve: !!nextEvolution(p) },
      players: live.filter((o) => o !== p && near(o)).map((o) => ({ id: o.id, n: o.name, a: o.animal, x: Math.round(o.x), y: Math.round(o.y) })),
      food: foods.filter(near).map((f) => ({ k: f.kind, x: Math.round(f.x), y: Math.round(f.y) })),
    };
    if (p.ws.readyState === 1) p.ws.send(JSON.stringify(msg));
  }
  // slowly decay uneaten meat
  foods = foods.filter((f) => f.kind !== "meat" || Math.random() > 0.002);
}, 1000 / cfg.sendRate);

server.listen(PORT, () => console.log(`${cfg.name} listening on http://localhost:${PORT}`));
'''

INDEX_HTML = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}}</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #1c2b1c; font-family: system-ui, sans-serif; }
  canvas { display: block; }
  #menu { position: fixed; inset: 0; display: flex; flex-direction: column; gap: 12px; align-items: center; justify-content: center; background: rgba(0,0,0,.55); color: #fff; }
  #menu h1 { margin: 0; font-size: 56px; }
  #menu input, #menu button { font-size: 18px; padding: 10px 16px; border-radius: 8px; border: 0; }
  #menu button { cursor: pointer; background: #5cb85c; color: #fff; }
  #menu button.water { background: #3b8fd6; }
  #hud { position: fixed; left: 12px; bottom: 12px; color: #fff; font-size: 14px; text-shadow: 0 1px 2px #000; }
  #bar { width: 200px; height: 12px; background: #0006; border-radius: 6px; overflow: hidden; margin-top: 6px; }
  #thirst { height: 100%; width: 100%; background: #4aa3d6; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="hud" hidden><div id="info"></div><div id="bar"><div id="thirst"></div></div></div>
<div id="menu">
  <h1>{{NAME}}</h1>
  <input id="name" placeholder="nickname" maxlength="16">
  <button id="land">Play on land</button>
  <button id="water" class="water">Play in the sea</button>
  <small>mouse = move &middot; hold click = sprint &middot; space = evolve</small>
</div>
<script src="client.js"></script>
</body>
</html>
'''

CLIENT_JS = r'''const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
const menu = document.getElementById("menu");
const hud = document.getElementById("hud");
const info = document.getElementById("info");
const thirstBar = document.getElementById("thirst");
const resize = () => { canvas.width = innerWidth; canvas.height = innerHeight; };
addEventListener("resize", resize); resize();

let ws, world, animals = {}, state = null, sprint = false, mouse = { x: 0, y: 0 };

function join(biome) {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host);
  ws.onopen = () => ws.send(JSON.stringify({ t: "join", name: document.getElementById("name").value, biome }));
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.t === "init") {
      world = m.world; animals = Object.fromEntries(m.animals.map((a) => [a.id, a]));
      menu.hidden = true; hud.hidden = false;
    } else if (m.t === "state") state = m;
    else if (m.t === "dead") { menu.hidden = false; hud.hidden = true; state = null; ws.close(); }
  };
}
document.getElementById("land").onclick = () => join("land");
document.getElementById("water").onclick = () => join("water");

addEventListener("mousemove", (e) => { mouse = { x: e.clientX, y: e.clientY }; });
addEventListener("mousedown", () => { sprint = true; });
addEventListener("mouseup", () => { sprint = false; });
addEventListener("keydown", (e) => {
  if (e.code === "Space" && ws && ws.readyState === 1) ws.send(JSON.stringify({ t: "evolve" }));
});
setInterval(() => {
  if (!ws || ws.readyState !== 1 || !state) return;
  const dx = mouse.x - canvas.width / 2, dy = mouse.y - canvas.height / 2;
  const power = Math.min(1, Math.hypot(dx, dy) / 120);
  ws.send(JSON.stringify({ t: "move", angle: Math.atan2(dy, dx), power, sprint }));
}, 50);

function blob(x, y, r, color, label) {
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color; ctx.fill();
  ctx.lineWidth = 3; ctx.strokeStyle = "#0004"; ctx.stroke();
  if (label) { ctx.fillStyle = "#fff"; ctx.font = "13px system-ui"; ctx.textAlign = "center"; ctx.fillText(label, x, y - r - 6); }
}

function draw() {
  requestAnimationFrame(draw);
  ctx.fillStyle = "#2e4a2a"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!state) return;
  const you = state.you;
  const ox = canvas.width / 2 - you.x, oy = canvas.height / 2 - you.y;
  ctx.save(); ctx.translate(ox, oy);
  const waterY = world.height * (1 - world.waterFraction);
  ctx.fillStyle = "#1f5f8f"; ctx.fillRect(0, waterY, world.width, world.height - waterY);
  ctx.strokeStyle = "#000"; ctx.strokeRect(0, 0, world.width, world.height);
  for (const f of state.food) blob(f.x, f.y, 6, f.k === "meat" ? "#c0392b" : "#e8453c");
  for (const p of state.players) blob(p.x, p.y, animals[p.a].r, animals[p.a].color, p.n);
  blob(you.x, you.y, animals[you.animal].r, animals[you.animal].color, "you");
  ctx.restore();
  info.textContent = `${you.animal}  |  XP ${you.xp}${you.canEvolve ? "  |  press SPACE to evolve!" : ""}`;
  thirstBar.style.width = Math.max(0, you.thirst) + "%";
}
draw();
'''

PACKAGE_JSON = {
    "name": "{{SLUG}}",
    "version": "0.1.0",
    "private": True,
    "description": "Custom mope.io-style game",
    "main": "server.js",
    "scripts": {"start": "node server.js"},
    "dependencies": {"ws": "^8.18.0"},
}

README = """# {{NAME}}

Custom mope.io-style game, modelled on mope.io as of 25 May 2020.

    npm install
    npm start        # http://localhost:{{PORT}}

Everything tunable (animal tree, XP thresholds, world size, thirst, food) lives
in `config.json`. Animals evolve when your XP passes the next animal's `xp`
value; press Space. Land animals must drink in the water; sea animals suffocate
on land. Carnivores eat any lower-tier animal they touch.
"""


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "mope-project"


def open_with_retry(url, timeout=60, attempts=5, base_delay=5):
    """urlopen that backs off exponentially on 429/5xx and network errors."""
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code >= 500
            wait = base_delay * 2 ** (attempt - 1)
            if e.code == 429 and e.headers.get("Retry-After", "").isdigit():
                wait = max(wait, int(e.headers["Retry-After"]))
            if not retryable or attempt == attempts:
                raise
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == attempts:
                raise
            wait = base_delay * 2 ** (attempt - 1)
            reason = str(e)
        print(f"  {reason}; retry {attempt}/{attempts - 1} in {wait}s", file=sys.stderr)
        time.sleep(wait)


def fetch_snapshot(dest):
    """Download the Wayback capture nearest SNAPSHOT_DATE for private reference."""
    dest.mkdir(parents=True, exist_ok=True)
    api = f"https://archive.org/wayback/available?url=mope.io&timestamp={SNAPSHOT_DATE}"
    try:
        with open_with_retry(api, timeout=30) as r:
            closest = json.load(r).get("archived_snapshots", {}).get("closest")
        if not closest:
            print("  no Wayback snapshot found", file=sys.stderr)
            return
        ts = closest["timestamp"]
        print(f"  using capture {ts}")
        base = f"https://web.archive.org/web/{ts}id_/"
        with open_with_retry(base + "https://mope.io/") as r:
            html = r.read().decode("utf-8", "replace")
        (dest / "index.html").write_text(html)
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
        for src in scripts:
            url = src if src.startswith("http") else "https://mope.io/" + src.lstrip("/")
            name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("//", 1)[-1].split("?")[0])
            try:
                with open_with_retry(base + url) as r:
                    (dest / name).write_bytes(r.read())
                print(f"  saved {name}")
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"  skipped {url}: {e}", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"  snapshot fetch failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("directory", help="project directory to create")
    ap.add_argument("--name", help="game title (default: directory name)")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--fetch-snapshot", action="store_true",
                    help="download the 2020-05-25 Wayback capture into reference/")
    ap.add_argument("--force", action="store_true", help="write into a non-empty directory")
    args = ap.parse_args()

    root = Path(args.directory)
    if root.exists() and any(root.iterdir()) and not args.force:
        sys.exit(f"{root} is not empty (use --force to overwrite)")
    name = args.name or root.resolve().name
    subs = {"{{NAME}}": name, "{{SLUG}}": slugify(name), "{{PORT}}": str(args.port)}

    def sub(text):
        for k, v in subs.items():
            text = text.replace(k, v)
        return text

    config = json.loads(sub(json.dumps(CONFIG)))
    config["port"] = args.port
    package = json.loads(sub(json.dumps(PACKAGE_JSON)))

    (root / "public").mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (root / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    (root / "server.js").write_text(sub(SERVER_JS))
    (root / "public" / "index.html").write_text(sub(INDEX_HTML))
    (root / "public" / "client.js").write_text(sub(CLIENT_JS))
    (root / "README.md").write_text(sub(README))
    (root / ".gitignore").write_text("node_modules/\nreference/\n")

    if args.fetch_snapshot:
        print("Fetching Wayback snapshot...")
        fetch_snapshot(root / "reference")

    print(f"Created {name!r} in {root}\n  cd {root} && npm install && npm start")


if __name__ == "__main__":
    main()
