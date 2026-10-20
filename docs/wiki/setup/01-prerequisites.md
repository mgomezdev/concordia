# Prerequisites

This page assumes **nothing**. If you've never used a terminal, you're in the right place.
If you use Docker daily, skip to [Quick Start](00-quick-start.md).

---

## What you're about to install

Four programs that work as a team. You won't install them one by one — you'll install **one**
tool (Docker), hand it a single configuration file, and it fetches and starts everything else.

Think of Docker as a shipping-container system: each app arrives sealed in its own container
with everything it needs inside. You never install Python, Node, or OrcaSlicer yourself.

---

## What you need

| Requirement | Why |
|---|---|
| A computer running Windows 10/11, macOS, or Linux | Any of the three works. |
| About **8 GB of free disk space** | The Laminus image plus the OrcaSlicer install is the bulk of it. |
| An internet connection for the first start | ~1.5 GB of images, plus a ~300 MB OrcaSlicer download. |
| About 30 minutes | Mostly waiting. |

You do **not** need OrcaSlicer installed on your computer. Laminus brings its own.

---

## Step 1 — Install Docker Desktop

Docker Desktop is the program that runs the containers.

1. Go to <https://www.docker.com/products/docker-desktop/>
2. Download the version for your operating system.
3. Run the installer and accept the defaults.
4. **Restart your computer** if it asks. On Windows it usually does.
5. Launch **Docker Desktop** and leave it running.

Docker Desktop must be **running** whenever you want to use the stack. On Windows and macOS
it adds a whale icon to your menu bar / system tray. If the whale isn't there, Docker isn't
running and none of the commands below will work.

> **Windows note.** Docker Desktop may ask to enable WSL2 (Windows Subsystem for Linux) and
> install a component. Say yes. This is normal and Docker handles it for you.

---

## Step 2 — Open a terminal

A terminal is a window where you type commands instead of clicking. You'll use it about six
times total.

| Your OS | How to open it |
|---|---|
| **Windows** | Press `Win`, type `powershell`, press Enter. |
| **macOS** | Press `Cmd+Space`, type `terminal`, press Enter. |
| **Linux** | `Ctrl+Alt+T`, or search "Terminal". |

Check Docker is working. Type this and press Enter:

```bash
docker version
```

You should see a block of text with `Server: Docker Desktop` in it. If you instead see
"command not found" or "the docker daemon is not running", Docker Desktop isn't installed or
isn't started — go back to Step 1.

> **Copying commands.** You can copy any command from this wiki and paste it into the
> terminal. On Windows PowerShell, paste with `Ctrl+V` or a right-click.

---

## Step 3 — Install Git (or download a ZIP)

Git is how you fetch the configuration files.

**Option A — Git (recommended).** Download from <https://git-scm.com/downloads>, install with
defaults, then close and reopen your terminal. Verify:

```bash
git --version
```

**Option B — no Git.** Go to the Concordia repository on GitHub, click the green **Code**
button, choose **Download ZIP**, and unzip it somewhere you can find again (e.g. your
Documents folder). Then skip the `git clone` step in [First Run](02-first-run.md) and just
`cd` into the unzipped folder.

---

## Step 4 — Pick a home for it

Choose a folder to keep this in. Anywhere is fine as long as you can find it again.

```bash
# Windows
cd ~/Documents

# macOS / Linux
cd ~
```

`cd` means "change directory" — it moves the terminal into that folder. Everything you type
afterwards happens there.

---

## You're ready

Continue to [First Run](02-first-run.md).

If anything above didn't behave as described, check
[Troubleshooting](05-troubleshooting.md#setup-problems) before continuing — a broken Docker
install produces confusing errors three steps later.
